# Note Lens

A mobile-friendly webapp that photographs **printed sheet music**, recognizes it with **real, self-hosted Audiveris OMR**, and plays the detected score with selectable synthesized **piano, violin, or cello**.

No API key, external recognition API, cloud model, analytics, remote fonts, or audio CDN is needed. The included sample is an original engraved melody, sent through the **same recognition pipeline as a photo**. There are no hardcoded recognition results.

## Run locally

Recommended host: **Ubuntu 24.04 x86_64**, Python **3.12+**, at least **2 CPU cores / 4 GB RAM** and about **1 GB free disk** for dependencies and temporary recognition files. Audiveris includes its own Java runtime. Node **20+** is only needed for browser tests and regenerating the sample, not for serving the app.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
bash scripts/setup-omr.sh
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

Open **http://localhost:8000** on the host. The installer downloads pinned, SHA-256-checked official Audiveris **5.11.0** (~78 MB) and Tesseract English legacy OCR data (~23 MB), then extracts them under the ignored `.tools/` directory. Internet is required for installation; photo recognition and playback run without an external service afterward.

On a minimal Ubuntu host, install native libraries first:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl libasound2t64 libbsd0 libx11-6 \
  libxau6 libxcb1 libxdmcp6 libxext6 libxi6 libxrender1 libxtst6 \
  zlib1g libfreetype6 fontconfig
```

For another OS/architecture, install an official compatible Audiveris **5.11** distribution manually. Set `AUDIVERIS_BIN` to its executable and `TESSDATA_PREFIX` to a directory containing a **legacy-compatible** `eng.traineddata` (the pinned file in `scripts/setup-omr.sh` works). `tessdata_fast` / `tessdata_best` are LSTM-only and are **not** substitutes: Audiveris uses Tesseract's legacy engine for text such as printed tempo markings. Other platforms have not been validated.

### Docker

The supplied image targets **linux/amd64**:

```bash
docker build --platform linux/amd64 -t note-lens .
docker run --rm --name note-lens -p 8000:8000 --memory=4g --cpus=2 note-lens
```

The container runs as a non-root user. Do not mount photo storage; intermediate files are temporary. Container image distribution must comply with Audiveris and its bundled dependencies' licenses (see below).

### Use from a phone

Live `getUserMedia` camera access needs a **secure context**: HTTPS, or localhost on that same device. `http://192.168…:8000` is **not** secure just because it is on your LAN. Put the app behind your trusted HTTPS reverse proxy or an HTTPS development port forward. Preserve `Host` and forward the original scheme; configure Uvicorn's `--forwarded-allow-ips` to **only the proxy's actual trusted IP**, rather than blindly trusting all clients.

The app requests the rear camera, never the microphone. Camera permission denial, missing/in-use cameras, insecure contexts, and interrupted audio have actionable messages. “Use phone camera instead” invokes a native `capture="environment"` file picker where supported; “Upload photo” always remains available. Some browsers ignore the capture hint. HEIC is not decoded: export JPEG/PNG/WebP first. Live tracks stop when the camera closes, after a capture, or when the page is hidden.

## Using the studio

1. **Take or upload a photo.** JPEG, PNG or WebP; maximum **15 MB**, **24 megapixels**, minimum **300 px per side**. Use a straight, evenly lit shot of complete printed staves, including clefs and key/time signatures. Previewing a photo does not upload it.
2. Press **Read the notes**. This explicitly sends the photo **only to this app's server**. Recognition runs asynchronously, one photo at a time; it can take **up to 3 minutes per photo**, plus queue time (two waiting photos maximum).
3. **Review the detected pitches, durations and warnings.** Download the actual recognized MusicXML for a complete inspection/correction in a notation editor. Acknowledge the review before playback is enabled.
4. Choose **piano, violin, or cello**, then tap **Play notes** to activate browser audio. Pause, stop, restart, seek, change speed (0.5–1.5×) and adjust volume. Playback pauses when the page is hidden. Check device volume/silent mode if sound is not audible.

Sounds are **Web Audio synthesis**, not recordings or a realistic sampled orchestra: piano uses a decaying harmonic envelope, strings use sustained harmonics and vibrato, with distinct violin/cello tone and attack. Switching instruments affects subsequently scheduled notes (within about 180 ms), preserves the score's pitches, and does not force notes into an instrument's real-world range.

## Notation support and honest limitations

Playback follows **exported MusicXML**, not guesses based on image filenames. Supported exported data includes:

| Performed | Notes |
| --- | --- |
| Pitches, clefs, key signatures, accidentals | Audiveris resolves these into written pitches; MusicXML chromatic/octave transposition is applied. A0–C8 only; microtones are skipped with a warning. |
| Rests, chords and multiple voices/parts | Timed from divisions, durations, backup and forward elements. All parts use the selected sound. |
| Dotted notes and tuplets | Actual exported durations are used, not just notehead type. |
| Ties and simple repeats | Ties join durations; simple repeat counts are capped at four passes. |
| Tempo and dynamics | Exported numeric tempo/metronome and dynamic markings; staccato, staccatissimo, tenuto and accents. |

**Not fully performed:** handwritten music, tablature, percussion, cue/grace notes, ornaments/trills/tremolo, pedal, fermatas, hairpin ramps, slur phrasing, breath pauses, ottava lines, playing techniques, alternate endings, D.C./D.S./coda navigation, and free-text instructions. Detected unsupported data produces warnings. Volta sections play in written order rather than inventing repeat traversal. There is no automatic perspective correction, manual note editor, or guarantee that every unsupported symbol will be detected.

**OMR is fallible.** A missed symbol may never enter the exported MusicXML, so the app cannot warn about every mistake. Audiveris does **not** provide a calibrated confidence percentage; the app never invents one. Every result is labeled unverified and requires a review acknowledgment. Missing starting tempo is explicitly reported and defaults to **100 BPM**. Inconsistent/incomplete measures produce warnings or an error. Bad photos fail visibly; sample notes are never substituted. Use the export to correct uncertain scores in a dedicated editor.

The integrated fixture exercises an original, clean, four-measure G-major score with natural/sharp accidentals, a rest, quarter/half/whole durations and a printed **120 BPM** marking. It verifies exact recognized pitches, starts and durations, then actual Web Audio playback in each instrument. This verifies the integration, **not** arbitrary-photo accuracy. Browser automation covers narrow mobile layout and camera success/failure/cleanup; physical iOS/Android camera and speaker hardware still require device testing.

## Privacy, deployment and operational limits

- Preview/camera and synthesized audio stay in the browser. Photo upload occurs only on **Read the notes** to the same-origin server. The server calls a local Audiveris subprocess, with **no third-party photo-recognition service**. Engine/model downloads happen during setup, not per photo.
- Photos are EXIF-oriented and rewritten without metadata. Each recognition has a private temporary directory; image, score intermediates, engine caches, OCR scratch data and engine logs are removed after the process succeeds, fails or times out. A hard host/container crash can leave OS temporary files: use OS temp cleanup or ephemeral containers/temporary storage for crash recovery. The in-memory image copy is released when its bounded queued job finishes.
- Only parsed notes and recognized MusicXML remain in server memory, for at most **30 minutes** (cleaned every minute), or until restart. Unguessable job URLs act as bearer links; do not share them. No database or photo history is maintained. Closing the page does not cancel a queued/running job.
- Run **one Uvicorn worker / one app instance**: the queue and results are in-process, not shared across replicas. Recognition has a 180-second subprocess timeout, a 1.5 GB Java heap cap and two active Java processors. Scores are limited to 20,000 note events and 20 minutes.
- This is a **single-user/private-hosting starting point**, not a hardened public upload service. There are no accounts or per-user quotas. For public access add authentication, request/concurrency/rate limits, an HTTPS proxy with a **16 MB body limit**, container-level CPU/memory/tmp-disk limits and monitoring. The app bounds streamed request bodies too. Do not log request bodies at your proxy. Do not expose it directly to the internet as-is.
- The health endpoint `/api/health` reports whether the executable and English OCR data are installed. It does not run recognition or prove native libraries/models are healthy; use the real integration test below for that.

## Development and verification

```bash
.venv/bin/pip install -r requirements-dev.txt
npm ci
npx playwright install chromium
.venv/bin/python -m pytest -q
npm test
npm run test:integration
```

`npm test` covers mobile layout, local-only photo selection, camera fallback/capture cleanup, invalid images, and non-silent/distinct offline audio rendering. `npm run test:integration` starts the real Python server, uploads `web/sample-score.png`, runs the actual installed Audiveris process, checks the exact recognition result and review gate, and plays all three sounds. The OMR test is skipped by default, **not mocked**. It needs the setup above and up to four minutes. CI runs both suites with the real engine.

If Chrome is already installed, prefix browser commands with `PLAYWRIGHT_CHANNEL=chrome` instead of downloading Chromium. `npm run sample` regenerates the original sample photo with development-only **abcjs** engraving; the generated PNG is committed, so runtime has no Node/abcjs dependency.

Architecture: same-origin **FastAPI** JSON/job API + plain **HTML/CSS/ES modules**, local **Audiveris → compressed MusicXML → bounded performance events**, and browser **Web Audio** synthesis. No frontend build is required.

## Third-party software

- [Audiveris 5.11.0](https://github.com/Audiveris/audiveris/tree/5.11.0): **AGPL-3.0-or-later**, with its own bundled component licenses. Downloaded separately by the installer; not vendored into this Git repository. Preserve its notices and comply with the applicable source/network-source obligations when modifying, hosting or redistributing it. [Corresponding release source](https://github.com/Audiveris/audiveris/archive/refs/tags/5.11.0.tar.gz).
- [Tesseract English data](https://github.com/tesseract-ocr/tessdata/tree/ced78752cc61322fb554c280d13360b35b8684e4): **Apache-2.0**. Pinned to a legacy-compatible model; its hash and revision are recorded in the installer.
- [abcjs](https://github.com/paulrosen/abcjs): **MIT**, development-only original sample engraving. The sample melody and its generated image were created for this app.
- Runtime Python dependencies and Playwright retain their respective upstream licenses. No third-party score photographs, instrument samples, or proprietary assets are included.