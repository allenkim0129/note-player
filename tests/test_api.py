import io
from pathlib import Path
import subprocess
import time

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from app import main
from app.musicxml import parse_score
from tests.test_musicxml import ATTRIBUTES, note, score_xml


def photo():
    buffer = io.BytesIO()
    Image.new("RGB", (800, 600), "white").save(buffer, "JPEG")
    return buffer.getvalue()


@pytest.fixture
def client(monkeypatch):
    main.jobs.clear()
    monkeypatch.setattr(main, "engine_path", lambda: "/fake/Audiveris")
    monkeypatch.setattr(main, "tessdata_path", lambda: "/fake/tessdata")
    with TestClient(main.app) as client:
        yield client
    main.jobs.clear()


def test_upload_poll_download_and_retention(client, monkeypatch):
    xml = score_xml(f"<measure>{ATTRIBUTES}{note()}</measure>")
    monkeypatch.setattr(main, "recognize", lambda data: (parse_score(xml), xml))
    result = client.post("/api/recognize", files={"photo": ("photo.jpg", photo(), "image/jpeg")})
    assert result.status_code == 202
    job_id = result.json()["id"]
    for _ in range(50):
        job = client.get(f"/api/jobs/{job_id}")
        if job.json()["status"] == "done":
            break
        time.sleep(0.01)
    assert job.json()["score"]["events"][0]["midi"] == 60
    assert job.headers["cache-control"] == "no-store"
    assert client.get(f"/api/jobs/{job_id}/musicxml").content == xml
    main.jobs[job_id].created -= main.JOB_TTL + 1
    assert client.get(f"/api/jobs/{job_id}").status_code == 404


def test_bad_files_missing_engine_and_cross_origin_are_explicit(client, monkeypatch):
    assert client.post("/api/recognize", files={"photo": ("bad.jpg", b"not an image")}).status_code == 422
    buffer = io.BytesIO()
    Image.new("RGB", (500, 500)).save(buffer, "GIF")
    assert client.post("/api/recognize", files={"photo": ("bad.gif", buffer.getvalue())}).status_code == 415
    assert client.post("/api/recognize", files={"photo": ("a.jpg", photo())},
                       headers={"Origin": "https://other.example"}).status_code == 403
    monkeypatch.setattr(main, "engine_path", lambda: None)
    assert client.get("/api/health").json()["recognitionAvailable"] is False
    response = client.post("/api/recognize", files={"photo": ("photo.jpg", photo())})
    assert response.status_code == 503
    assert "setup-omr" in response.json()["detail"]


def test_queue_is_bounded_and_failures_are_not_success(client):
    for i in range(3):
        main.jobs[str(i)] = main.Job(created=time.time())
    assert client.post("/api/recognize", files={"photo": ("photo.jpg", photo())}).status_code == 429
    main.jobs["failed"] = main.Job(created=time.time(), status="error", error="No staff was found.")
    assert client.get("/api/jobs/failed").json()["score"] is None
    assert client.get("/api/jobs/failed").json()["error"] == "No staff was found."
    assert client.get("/api/jobs/failed/musicxml").status_code == 409


@pytest.mark.parametrize("fails", [False, True])
def test_recognizer_cleans_temporary_photos_on_success_and_failure(monkeypatch, fails):
    folders = []
    monkeypatch.setattr(main, "engine_path", lambda: "/fake/Audiveris")
    monkeypatch.setattr(main, "tessdata_path", lambda: "/fake/tessdata")

    def run(command, **kwargs):
        image = Path(command[-1])
        folders.append(image.parent)
        assert image.exists()
        assert kwargs["env"]["XDG_CACHE_HOME"].startswith(str(image.parent))
        assert kwargs["env"]["HOME"] == str(image.parent)
        if fails:
            raise subprocess.TimeoutExpired(command, 180)
        output = Path(command[command.index("-output") + 1])
        output.mkdir()
        (output / "photo.musicxml").write_bytes(score_xml(f"<measure>{ATTRIBUTES}{note()}</measure>"))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(main.subprocess, "run", run)
    if fails:
        with pytest.raises(RuntimeError, match="three minutes"):
            main.recognize(photo())
    else:
        assert main.recognize(photo())[0]["events"][0]["midi"] == 60
    assert folders and all(not folder.exists() for folder in folders)


def test_upload_limits_apply_to_headers_and_chunked_bodies(client):
    response = client.post("/api/recognize", content=b"x", headers={"Content-Length": str(main.MAX_UPLOAD + 100_000)})
    assert response.status_code == 413
    boundary = "photo-boundary"

    def stream():
        yield f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="a.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'.encode()
        for _ in range(17):
            yield b"x" * 1024 * 1024
        yield f"\r\n--{boundary}--\r\n".encode()

    response = client.post("/api/recognize", content=stream(),
                           headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    assert response.status_code == 413
    assert not main.jobs


@pytest.mark.parametrize("engine_message", [
    "WARN  [] | No reliable beam height found, guessed value: 9\n",
    "WARN  [] | TesseractOrder. Could not initialize TessBaseAPI languages: eng in legacy mode\n",
])
def test_success_exit_does_not_hide_engine_uncertainty_or_broken_ocr(monkeypatch, engine_message):
    monkeypatch.setattr(main, "engine_path", lambda: "/fake/Audiveris")
    monkeypatch.setattr(main, "tessdata_path", lambda: "/fake/tessdata")

    def run(command, **kwargs):
        kwargs["stdout"].write(engine_message.encode())
        output = Path(command[command.index("-output") + 1])
        output.mkdir()
        (output / "photo.musicxml").write_bytes(score_xml(f"<measure>{ATTRIBUTES}{note()}</measure>"))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(main.subprocess, "run", run)
    if "TessBaseAPI" in engine_message:
        with pytest.raises(RuntimeError, match="Text recognition could not start"):
            main.recognize(photo())
    else:
        score, _ = main.recognize(photo())
        assert any("note beams" in warning for warning in score["warnings"])
        assert any("processing warning" in warning for warning in score["warnings"])
