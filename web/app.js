import { ScorePlayer } from "./audio.js";

const $ = id => document.getElementById(id);
let photo = null;
let previewUrl = null;
let stream = null;
let cameraGeneration = 0;
let busy = false;
let score = null;
let engineAvailable = false;
let playPending = false;
let photoGeneration = 0;
const timeLabel = seconds => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;

function showError(message) {
  $("global-error").textContent = message;
  $("global-error").hidden = !message;
  if (message) $("global-error").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

const player = new ScorePlayer(updatePlayer, showError);

function updatePlayer(state) {
  $("elapsed").textContent = timeLabel(state.position / state.speed);
  $("total-time").textContent = timeLabel((state.score?.duration || 0) / state.speed);
  $("seek").value = state.position;
  $("play-label").textContent = state.playing ? "Pause" : "Play notes";
  $("play-icon").replaceChildren();
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", state.playing ? "M7 5h3v14H7zM14 5h3v14h-3z" : "m8 5 11 7-11 7z");
  $("play-icon").append(path);
  document.querySelector(".music-visual").classList.toggle("playing", state.playing);
  let active;
  if (state.playing && state.score) {
    // The short score excerpt is sufficient for the animation; playback uses every event.
    active = state.score.events.findLast(note => note.time <= state.position && note.time + note.duration > state.position);
  }
  $("active-note").textContent = active?.name || "♪";
}

function updateControls() {
  const allowed = !!score && $("reviewed").checked && !busy;
  $("play").disabled = !allowed || playPending;
  $("restart").disabled = !allowed;
  $("stop").disabled = !allowed;
  $("seek").disabled = !allowed;
  $("recognize").disabled = !photo || busy || !engineAvailable;
  for (const id of ["camera-open", "upload-open", "replace-photo", "try-sample"]) $(id).disabled = busy;
  $("scan-overlay").hidden = !busy;
}

function resetScore() {
  player.load(null);
  score = null;
  $("reviewed").checked = false;
  $("recognition-results").hidden = true;
  $("recognition-progress").textContent = "";
  $("recognition-panel").classList.remove("failed");
  $("score-label").textContent = "A LITTLE QUIET, FOR NOW";
  $("score-title").textContent = "Your music belongs here.";
  $("score-subtitle").textContent = "Read the photo, review the notes, then make it play.";
  $("seek").max = 1;
  $("step-recognize").classList.toggle("current", !!photo);
  $("step-play").classList.remove("current");
  updateControls();
}

async function setPhoto(file) {
  if (busy) return;
  const generation = ++photoGeneration;
  if (!file || !file.size) return showError("That photo is empty. Please choose another one.");
  if (file.size > 15 * 1024 * 1024) return showError("Choose a photo smaller than 15 MB.");
  if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
    return showError("Please choose JPEG, PNG or WebP. If your phone uses HEIC, export a JPEG copy first.");
  }
  const url = URL.createObjectURL(file);
  try {
    const image = new Image();
    image.src = url;
    await image.decode();
    if (image.naturalWidth * image.naturalHeight > 24_000_000) throw new Error("This photo exceeds 24 megapixels. Export a smaller image.");
    if (Math.min(image.naturalWidth, image.naturalHeight) < 300) throw new Error("Use a photo at least 300 pixels on each side so the notes are readable.");
  } catch (error) {
    URL.revokeObjectURL(url);
    if (generation !== photoGeneration) return;
    showError(error.message.startsWith("This photo") || error.message.startsWith("Use a photo") ? error.message : "This image could not be opened. Try another JPEG, PNG or WebP.");
    return;
  }
  if (generation !== photoGeneration || busy) {
    URL.revokeObjectURL(url);
    return;
  }
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  photo = file;
  previewUrl = url;
  $("photo-preview").src = url;
  $("photo-preview").hidden = false;
  $("photo-empty").hidden = true;
  $("replace-photo").hidden = false;
  showError("");
  resetScore();
}

for (const id of ["photo-file", "camera-file"]) {
  $(id).addEventListener("change", async event => {
    await setPhoto(event.target.files[0]);
    event.target.value = "";
  });
}
$("upload-open").onclick = $("replace-photo").onclick = () => $("photo-file").click();

function closeCamera() {
  cameraGeneration++;
  stream?.getTracks().forEach(track => track.stop());
  stream = null;
  $("camera-video").srcObject = null;
  $("camera-capture").disabled = true;
  if ($("camera-dialog").open) $("camera-dialog").close();
}

$("camera-open").onclick = async () => {
  showError("");
  $("camera-status").textContent = "Opening your camera…";
  $("camera-capture").disabled = true;
  $("camera-dialog").showModal();
  const generation = ++cameraGeneration;
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    $("camera-status").textContent = "Live camera needs HTTPS (or localhost) and a supported browser. Use your phone’s native camera below, or upload a photo.";
    return;
  }
  try {
    const camera = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" }, width: { ideal: 2560 }, height: { ideal: 1920 } },
      audio: false,
    });
    if (generation !== cameraGeneration || !$("camera-dialog").open) {
      camera.getTracks().forEach(track => track.stop());
      return;
    }
    stream = camera;
    $("camera-video").srcObject = stream;
    await $("camera-video").play();
    if (generation !== cameraGeneration) return;
    $("camera-status").textContent = "All set. Keep the notes sharp and the page straight.";
    $("camera-capture").disabled = false;
  } catch (error) {
    if (generation !== cameraGeneration) return;
    stream?.getTracks().forEach(track => track.stop());
    stream = null;
    const messages = {
      NotAllowedError: "Camera permission was denied. Allow camera access in your browser’s site settings, or use the phone camera / upload option.",
      NotFoundError: "No camera was found. Upload a photo, or open this app on your phone.",
      NotReadableError: "Your camera is in use or unavailable. Close other camera apps, or upload a photo.",
      OverconstrainedError: "This camera cannot use the requested settings. Use the phone camera or upload a photo.",
    };
    $("camera-status").textContent = messages[error.name] || `The camera could not start (${error.name}). Use the phone camera or upload a photo.`;
  }
};
$("camera-close").onclick = closeCamera;
$("camera-dialog").addEventListener("cancel", event => { event.preventDefault(); closeCamera(); });
$("camera-dialog").addEventListener("close", closeCamera);
$("native-camera").onclick = () => { closeCamera(); $("camera-file").click(); };
$("camera-capture").onclick = async () => {
  const video = $("camera-video");
  if (!video.videoWidth || !video.videoHeight) return;
  $("camera-capture").disabled = true;
  const scale = Math.min(1, 4200 / Math.max(video.videoWidth, video.videoHeight));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(video.videoWidth * scale);
  canvas.height = Math.round(video.videoHeight * scale);
  canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
  const blob = await new Promise(resolve => canvas.toBlob(resolve, "image/jpeg", 0.95));
  closeCamera();
  if (!blob) return showError("The camera could not save this photo. Try the phone camera or upload option.");
  await setPhoto(new File([blob], "camera-photo.jpg", { type: "image/jpeg" }));
};
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    closeCamera();
    if (player.playing) player.pause();
  }
});
window.addEventListener("pagehide", () => { closeCamera(); player.stop(); });

const captureCard = document.querySelector(".capture-card");
for (const name of ["dragover", "dragleave", "drop"]) captureCard.addEventListener(name, event => {
  event.preventDefault();
  captureCard.classList.toggle("dragover", name === "dragover" && !busy);
  if (name === "drop" && !busy) setPhoto(event.dataTransfer.files[0]);
});

async function api(url, options = {}) {
  let response;
  try {
    response = await fetch(url, { ...options, signal: AbortSignal.timeout(30_000) });
  } catch {
    throw new Error("Could not reach this app’s server. Check your connection and try again. A recognition already submitted may still finish on the server.");
  }
  let body;
  try { body = await response.json(); } catch { throw new Error(`The server returned an unreadable response (${response.status}). Please try again.`); }
  if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : `The request failed (${response.status}). Please try another photo.`);
  return body;
}

function renderScore(result, jobId) {
  score = result;
  player.load(score);
  $("score-label").textContent = "YOUR SHEET, BROUGHT TO SOUND";
  $("score-title").textContent = score.title === "photo" ? "A new piece to discover." : score.title;
  $("score-subtitle").textContent = `${score.events.length} notes · ${score.measures} measures · ${score.tempo} BPM`;
  $("seek").max = score.duration;
  $("recognition-results").hidden = false;
  $("recognition-description").textContent = "Your photo has been read. Take a moment to check what we found.";
  $("score-stats").replaceChildren();
  for (const text of [
    `${score.events.length} pitched notes`, `${score.measures} measures (with repeats)`,
    `${score.parts.length} part${score.parts.length === 1 ? "" : "s"}`,
    `${score.tempo} BPM`, ...score.timeSignatures.map(t => `${t} time`),
  ]) {
    const badge = document.createElement("span");
    badge.className = "stat";
    badge.textContent = text;
    $("score-stats").append(badge);
  }
  $("warnings").replaceChildren();
  for (const warning of score.warnings) {
    const item = document.createElement("li");
    item.textContent = warning;
    $("warnings").append(item);
  }
  $("warning-count").textContent = `Recognition notes & limitations (${score.warnings.length})`;
  $("note-list").replaceChildren();
  const visible = score.events.slice(0, 160);
  for (const note of visible) {
    const chip = document.createElement("span");
    chip.className = "note-chip";
    chip.textContent = note.name;
    const detail = document.createElement("small");
    detail.textContent = `${note.rhythm} beat${note.rhythm === "1" ? "" : "s"} · ${note.time.toFixed(2)}s`;
    chip.append(detail);
    chip.title = `Measure ${note.measure} · ${note.part} · sounding MIDI ${note.midi}`;
    $("note-list").append(chip);
  }
  $("notes-count").textContent = score.events.length > 160 ? `(first 160 of ${score.events.length})` : `(${score.events.length})`;
  $("download-xml").href = `/api/jobs/${encodeURIComponent(jobId)}/musicxml`;
  $("step-play").classList.add("current");
  $("reviewed").checked = false;
}

$("recognize").onclick = async () => {
  if (!photo || busy) return;
  showError("");
  resetScore();
  busy = true;
  updateControls();
  $("recognize-label").textContent = "Reading your music…";
  $("recognition-progress").textContent = "Sending your photo to this app’s server. No third-party recognition service is used.";
  try {
    const data = new FormData();
    data.append("photo", photo);
    const job = await api("/api/recognize", { method: "POST", body: data });
    const started = Date.now();
    while (true) {
      const result = await api(`/api/jobs/${encodeURIComponent(job.id)}`);
      if (result.status === "done") {
        renderScore(result.score, job.id);
        $("recognition-progress").textContent = "Recognition finished. Review the notes and limitations below to enable playback.";
        $("recognition-panel").scrollIntoView({ behavior: "smooth", block: "nearest" });
        break;
      }
      if (result.status === "error") throw new Error(result.error);
      const elapsed = Math.round((Date.now() - started) / 1000);
      $("recognition-progress").textContent = result.status === "queued"
        ? `Your photo is queued for self-hosted recognition (${elapsed}s). Up to two photos may be ahead of yours.`
        : `Audiveris is finding staves, notes and rhythms (${elapsed}s). This can take up to three minutes. Keep this page open.`;
      if (Date.now() - started > 10 * 60 * 1000) throw new Error("The server took too long to respond. Please retry recognition.");
      await new Promise(resolve => setTimeout(resolve, 1500));
    }
  } catch (error) {
    showError(error.message);
    $("recognition-progress").textContent = "The photo could not be read. No substitute or sample notes have been used.";
    $("recognition-panel").classList.add("failed");
  } finally {
    busy = false;
    $("recognize-label").textContent = "Read the notes";
    updateControls();
  }
};

$("reviewed").onchange = () => {
  if (!$("reviewed").checked) player.stop();
  updateControls();
};
$("play").onclick = async () => {
  if (playPending) return;
  if (player.playing) return player.pause();
  playPending = true;
  updateControls();
  showError("");
  try { await player.play(); } catch (error) { showError(error.message); }
  finally { playPending = false; updateControls(); }
};
$("stop").onclick = () => player.stop();
$("restart").onclick = () => player.seek(0).catch(error => showError(error.message));
$("seek").oninput = event => player.seek(Number(event.target.value)).catch(error => showError(error.message));
$("speed").oninput = event => {
  const value = Number(event.target.value);
  $("speed-value").textContent = `${Number(value.toFixed(2))}×`;
  player.setSpeed(value).then(() => updatePlayer(player)).catch(error => showError(error.message));
};
$("volume").oninput = event => {
  $("volume-value").textContent = `${event.target.value}%`;
  player.setVolume(Number(event.target.value) / 100);
};
for (const button of document.querySelectorAll(".instrument")) button.onclick = () => {
  player.instrument = button.dataset.instrument;
  for (const item of document.querySelectorAll(".instrument")) {
    item.classList.toggle("selected", item === button);
    item.setAttribute("aria-pressed", String(item === button));
  }
};
$("try-sample").onclick = async () => {
  showError("");
  try {
    const response = await fetch("/static/sample-score.png");
    if (!response.ok) throw new Error("The sample photo could not be loaded. Please upload your own sheet music.");
    await setPhoto(new File([await response.blob()], "a-little-melody.png", { type: "image/png" }));
  } catch (error) { showError(error.message); }
};
for (const id of ["help-open", "privacy-open"]) $(id).onclick = () => $("help-dialog").showModal();
$("help-close").onclick = () => $("help-dialog").close();

api("/api/health").then(status => {
  engineAvailable = status.recognitionAvailable;
  $("engine-status").textContent = engineAvailable ? "SELF-HOSTED OMR · READY" : "RECOGNITION SETUP NEEDED";
  if (!engineAvailable) {
    $("recognition-progress").textContent = "The server operator needs to install Audiveris. See README.md and run scripts/setup-omr.sh. Photo preview is available; recognition is not yet configured.";
  }
  updateControls();
}).catch(error => {
  $("engine-status").textContent = "SERVER UNAVAILABLE";
  showError(error.message);
});
