import asyncio
import io
import logging
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.musicxml import parse_score, read_musicxml

ROOT = Path(__file__).resolve().parent.parent
MAX_UPLOAD = 15 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 24_000_000
JOB_TTL = 30 * 60
logger = logging.getLogger("note-lens")


def engine_path():
    configured = os.environ.get("AUDIVERIS_BIN")
    candidate = configured or str(ROOT / ".tools/audiveris/opt/audiveris/bin/Audiveris")
    return shutil.which(candidate) or shutil.which("Audiveris")


def tessdata_path():
    path = Path(os.environ.get("TESSDATA_PREFIX", ROOT / ".tools/tessdata"))
    model = path / "eng.traineddata"
    return str(path.resolve()) if model.is_file() and os.access(model, os.R_OK) else None


class UploadTooLarge(Exception):
    pass


class UploadSizeLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            return await self.app(scope, receive, send)
        received = 0

        async def bounded_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > MAX_UPLOAD + 64 * 1024:
                    raise UploadTooLarge
            return message

        try:
            await self.app(scope, bounded_receive, send)
        except UploadTooLarge:
            response = JSONResponse({"detail": "Photos must be smaller than 15 MB."}, status_code=413)
            await response(scope, receive, send)


@dataclass
class Job:
    created: float
    status: str = "queued"
    score: dict | None = None
    xml: bytes | None = None
    error: str | None = None


jobs: dict[str, Job] = {}
tasks: set[asyncio.Task] = set()
worker = asyncio.Semaphore(1)


def prune_jobs():
    for job_id, job in list(jobs.items()):
        if job.status in ("done", "error") and time.time() - job.created > JOB_TTL:
            del jobs[job_id]


async def janitor():
    while True:
        await asyncio.sleep(60)
        prune_jobs()


@asynccontextmanager
async def lifespan(app):
    cleaner = asyncio.create_task(janitor())
    yield
    cleaner.cancel()
    # Let any bounded OMR subprocess finish before its temporary directory is removed.
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="Note Lens", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(UploadSizeLimit)


@app.middleware("http")
async def safety_headers(request: Request, call_next):
    if request.method == "POST":
        length = request.headers.get("content-length")
        if length and length.isdecimal() and int(length) > MAX_UPLOAD + 64 * 1024:
            return JSONResponse({"detail": "Photos must be smaller than 15 MB."}, status_code=413)
        origin = request.headers.get("origin")
        if origin and origin != f"{request.url.scheme}://{request.headers.get('host')}":
            # A TLS-terminating reverse proxy must preserve Host and forward its scheme.
            return JSONResponse({"detail": "Cross-origin uploads are not allowed."}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(self), microphone=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' blob: data:; connect-src 'self'; media-src 'self' blob:; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    )
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/health")
async def health():
    return {"ok": True, "recognitionAvailable": engine_path() is not None and tessdata_path() is not None,
            "engine": "Audiveris 5.11", "maxUploadMB": 15}


def normalize_photo(data: bytes) -> bytes:
    try:
        with Image.open(io.BytesIO(data)) as original:
            if original.format not in ("JPEG", "PNG", "WEBP"):
                raise HTTPException(415, "Choose a JPEG, PNG or WebP photo. Convert HEIC images to JPEG first.")
            if original.width * original.height > Image.MAX_IMAGE_PIXELS:
                raise HTTPException(413, "This photo exceeds 24 megapixels. Export a smaller image.")
            if min(original.size) < 300:
                raise HTTPException(422, "This photo is too small. Use an image at least 300 pixels on each side.")
            original.load()
            image = ImageOps.exif_transpose(original).convert("RGB")
            image.thumbnail((4200, 4200))
            output = io.BytesIO()
            image.save(output, "PNG")
            return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise HTTPException(422, "This image could not be opened safely. Try another JPEG, PNG or WebP photo.") from exc


def recognize(photo: bytes) -> tuple[dict, bytes]:
    binary = engine_path()
    tessdata = tessdata_path()
    if not binary or not tessdata:
        raise RuntimeError("The recognition engine or English OCR data is not installed. Ask the server operator to run scripts/setup-omr.sh.")
    with tempfile.TemporaryDirectory(prefix="note-lens-") as directory:
        folder = Path(directory)
        image = folder / "photo.png"
        image.write_bytes(photo)
        output = folder / "output"
        environment = os.environ.copy()
        environment.update({
            "HOME": str(folder),
            "XDG_CONFIG_HOME": str(folder / "config"),
            "XDG_DATA_HOME": str(folder / "data"),
            "XDG_CACHE_HOME": str(folder / "cache"),
            "TESSDATA_PREFIX": tessdata,
            "JAVA_TOOL_OPTIONS": (
                "-Xmx1536m -XX:ActiveProcessorCount=2 -Djava.awt.headless=true -Dsun.java2d.uiScale=1 "
                f"-Duser.home={folder} -Djava.io.tmpdir={folder}"
            ),
        })
        try:
            # A file, rather than a pipe, keeps verbose native-library logs out of memory.
            with (folder / "engine.log").open("w+b") as log:
                completed = subprocess.run(
                    [binary, "-batch", "-transcribe", "-export", "-output", str(output), "--", str(image)],
                    stdout=log, stderr=subprocess.STDOUT, timeout=180, env=environment,
                )
                if completed.returncode:
                    log.seek(max(0, log.tell() - 6000))
                    logger.error("Audiveris failed (%s): %s", completed.returncode, log.read().decode(errors="replace"))
                    raise RuntimeError("Recognition could not finish. Try a clearer, straight-on photo with the full staff visible.")
                log.seek(0)
                warning_count = 0
                uncertain_beams = False
                for raw_line in log:
                    line = raw_line.decode(errors="replace")
                    if "Could not initialize TessBaseAPI" in line or "couldn't load any languages" in line:
                        raise RuntimeError("Text recognition could not start. The server operator must check legacy English OCR data and its file permissions.")
                    if line.startswith("ERROR"):
                        logger.error("Audiveris reported an error: %s", line.rstrip())
                        raise RuntimeError("The engine reported a processing error. Try a clearer photo or a shorter passage.")
                    if line.startswith("WARN"):
                        warning_count += 1
                        uncertain_beams |= "No reliable beam height" in line
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Recognition exceeded three minutes. Try a smaller, clearer passage.") from exc
        exports = sorted(output.rglob("*.mxl")) + sorted(output.rglob("*.musicxml"))
        if not exports:
            raise RuntimeError("No score was recognized. Photograph printed staff notation in even light, with all five staff lines visible.")
        if len(exports) > 1:
            raise RuntimeError("The photo produced several separate scores. Photograph one musical excerpt at a time.")
        xml = read_musicxml(exports[0].read_bytes())
        score = parse_score(xml)
        if warning_count:
            score["warnings"].append(
                f"The recognition engine reported {warning_count} processing warning(s). Symbols or rhythm may be uncertain; inspect the exported score."
            )
        if uncertain_beams:
            score["warnings"].append("The engine could not reliably measure note beams. Check short-note rhythms particularly carefully.")
        score["warnings"].append("Audiveris does not provide a calibrated confidence score. These results have not been verified by a musician.")
        return score, xml


async def process_job(job_id: str, photo: bytes):
    job = jobs[job_id]
    async with worker:
        job.status = "recognizing"
        try:
            job.score, job.xml = await asyncio.to_thread(recognize, photo)
            job.status = "done"
        except Exception as exc:
            logger.exception("Recognition job %s failed", job_id)
            job.error = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "Recognition failed unexpectedly. Please try a clearer photo or contact the server operator."
            job.status = "error"


@app.post("/api/recognize", status_code=202)
async def upload_photo(request: Request):
    prune_jobs()
    if not engine_path() or not tessdata_path():
        raise HTTPException(503, "Recognition or text OCR is not configured. The server operator must run scripts/setup-omr.sh.")
    if sum(job.status in ("queued", "recognizing") for job in jobs.values()) >= 3:
        raise HTTPException(429, "The recognition queue is full. Please try again in a few minutes.")
    if len(jobs) >= 100:
        raise HTTPException(429, "The server is at capacity. Please try again later.")
    data = bytearray()
    async with request.form(max_files=1, max_fields=0) as form:
        photo = form.get("photo")
        if not isinstance(photo, StarletteUploadFile):
            raise HTTPException(422, "Select a photo to recognize.")
        while chunk := await photo.read(1024 * 1024):
            data.extend(chunk)
            if len(data) > MAX_UPLOAD:
                raise HTTPException(413, "Photos must be smaller than 15 MB.")
    normalized = await asyncio.to_thread(normalize_photo, bytes(data))
    # Recheck after awaiting the upload so concurrent requests cannot bypass admission.
    if sum(job.status in ("queued", "recognizing") for job in jobs.values()) >= 3 or len(jobs) >= 100:
        raise HTTPException(429, "The recognition queue is full. Please try again in a few minutes.")
    job_id = uuid.uuid4().hex
    jobs[job_id] = Job(created=time.time())
    task = asyncio.create_task(process_job(job_id, normalized))
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return {"id": job_id, "status": "queued"}


def find_job(job_id: str) -> Job:
    prune_jobs()
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "This recognition result expired or the server restarted. Please recognize the photo again.")
    return job


@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str):
    job = find_job(job_id)
    return {"id": job_id, "status": job.status, "score": job.score, "error": job.error}


@app.get("/api/jobs/{job_id}/musicxml")
async def download_score(job_id: str):
    job = find_job(job_id)
    if not job.xml:
        raise HTTPException(409, "The recognized score is not ready.")
    return Response(job.xml, media_type="application/vnd.recordare.musicxml+xml",
                    headers={"Content-Disposition": 'attachment; filename="note-lens.musicxml"'})


app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")


@app.get("/")
async def index():
    return FileResponse(ROOT / "web/index.html")
