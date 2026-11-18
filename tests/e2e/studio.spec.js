import { test, expect } from "@playwright/test";

test("mobile photo selection stays local; instrument controls and camera failure are usable", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  let uploaded = false;
  page.on("request", request => { if (request.method() === "POST") uploaded = true; });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /Give your sheet music/ })).toBeVisible();
  await expect(page.locator("#play")).toBeDisabled();
  await page.locator("#try-sample").click();
  await expect(page.locator("#photo-preview")).toBeVisible();
  expect(uploaded).toBe(false);
  await expect(page.locator("#play")).toBeDisabled();
  await page.getByRole("button", { name: /Violin Bright/ }).click();
  await expect(page.locator('[data-instrument="violin"]')).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator('[data-instrument="piano"]')).toHaveAttribute("aria-pressed", "false");
  await page.locator("#camera-open").click();
  await expect(page.locator("#camera-status")).toContainText(/permission|No camera|unavailable/);
  await expect(page.locator("#native-camera")).toBeEnabled();
  await page.locator("#camera-close").click();
  await expect(page.locator("#camera-dialog")).not.toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("invalid images show an actionable error without fake notes", async ({ page }) => {
  await page.goto("/");
  await page.locator("#photo-file").setInputFiles({
    name: "broken.jpg", mimeType: "image/jpeg", buffer: Buffer.from("not a photograph"),
  });
  await expect(page.getByRole("alert")).toContainText("could not be opened");
  await expect(page.locator("#play")).toBeDisabled();
  await expect(page.locator("#recognition-results")).toBeHidden();
});

test("camera capture produces a local photo and releases the live track", async ({ page }) => {
  await page.addInitScript(() => {
    const canvas = document.createElement("canvas");
    canvas.width = 1200;
    canvas.height = 900;
    const context = canvas.getContext("2d");
    context.fillStyle = "white";
    context.fillRect(0, 0, canvas.width, canvas.height);
    const camera = canvas.captureStream(10);
    window.testCamera = camera;
    navigator.mediaDevices.getUserMedia = async () => camera;
  });
  let uploaded = false;
  page.on("request", request => { if (request.method() === "POST") uploaded = true; });
  await page.goto("/");
  await page.locator("#camera-open").click();
  await expect(page.locator("#camera-capture")).toBeEnabled();
  await page.locator("#camera-capture").click();
  await expect(page.locator("#photo-preview")).toBeVisible();
  await expect(page.locator("#camera-dialog")).toBeHidden();
  expect(await page.evaluate(() => window.testCamera.getTracks().every(track => track.readyState === "ended"))).toBe(true);
  expect(uploaded).toBe(false);
  await expect(page.locator("#play")).toBeDisabled();
});

test("all three instrument synthesizers render distinct, finite, audible waveforms", async ({ page }) => {
  await page.goto("/");
  const signals = await page.evaluate(async () => {
    const { synthesizeNote } = await import("/static/audio.js");
    const outputs = [];
    for (const instrument of ["piano", "violin", "cello"]) {
      const context = new OfflineAudioContext(1, 44_100, 44_100);
      synthesizeNote(context, context.destination, { midi: 60, gate: 0.9, velocity: 0.7 }, instrument, 0.01, 0.6);
      const buffer = (await context.startRendering()).getChannelData(0);
      outputs.push({
        finite: buffer.every(Number.isFinite),
        energy: buffer.reduce((sum, value) => sum + value * value, 0),
        fingerprint: Array.from(buffer.slice(5000, 5010)),
      });
    }
    return outputs;
  });
  for (const signal of signals) {
    expect(signal.finite).toBe(true);
    expect(signal.energy).toBeGreaterThan(0.1);
  }
  expect(signals[0].fingerprint).not.toEqual(signals[1].fingerprint);
  expect(signals[1].fingerprint).not.toEqual(signals[2].fingerprint);
});

test("stopping while audio activation is pending cannot start unreviewed playback", async ({ page }) => {
  await page.goto("/");
  const playing = await page.evaluate(async () => {
    const { ScorePlayer } = await import("/static/audio.js");
    const player = new ScorePlayer(() => {}, () => {});
    let activate;
    player.context = {
      state: "running",
      resume: () => new Promise(resolve => { activate = resolve; }),
    };
    player.load({ duration: 1, events: [{ time: 0, duration: 1 }] });
    const pending = player.play();
    player.stop();
    activate();
    await pending;
    return player.playing;
  });
  expect(playing).toBe(false);
});

test("studio fits narrow phones, tablets and desktop without horizontal overflow", async ({ page }) => {
  await page.goto("/");
  for (const width of [320, 390, 768, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    const heading = page.locator("#recognition-description");
    expect((await heading.boundingBox()).width).toBeGreaterThan(150);
  }
});
