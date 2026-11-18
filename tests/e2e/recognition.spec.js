import { test, expect } from "@playwright/test";
import { writeFile } from "node:fs/promises";

test("real photo → Audiveris → recognized pitches/rhythm → reviewed instrument playback", async ({ page }) => {
  test.skip(process.env.RUN_OMR !== "1", "Opt-in: requires the genuine Audiveris engine.");
  test.setTimeout(240_000);
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/");
  await expect(page.locator("#engine-status")).toContainText("READY");
  await page.locator("#try-sample").click();
  await page.locator("#recognize").click();
  // The browser polls the real API. No request interception or stored recognition results.
  await expect(page.locator("#recognition-results")).toBeVisible({ timeout: 210_000 });
  const href = await page.locator("#download-xml").getAttribute("href");
  const result = await page.request.get(href.replace("/musicxml", ""));
  const { score } = await result.json();
  await writeFile(test.info().outputPath("recognized-score.json"), JSON.stringify(score, null, 2));
  expect(score.events.map(note => note.midi)).toEqual([67, 69, 71, 72, 74, 71, 65, 66, 67, 60]);
  expect(score.events.map(note => note.rhythm)).toEqual(["1", "1", "1", "1", "2", "2", "1", "1", "1", "4"]);
  expect(score.events.map(note => note.time)).toEqual([0, 0.5, 1, 1.5, 2, 3, 4, 4.5, 5, 6]);
  expect(score.duration).toBe(8);
  expect(score.tempo).toBe(120);
  expect(score.warnings.some(warning => warning.includes("No starting tempo"))).toBe(false);
  expect(score.warnings.some(warning => warning.includes("confidence"))).toBe(true);
  await expect(page.locator("#play")).toBeDisabled();
  await page.locator("#reviewed").check();
  await expect(page.locator("#play")).toBeEnabled();
  for (const instrument of ["piano", "violin", "cello"]) {
    await page.locator(`[data-instrument="${instrument}"]`).click();
    await page.locator("#play").click();
    await expect(page.locator("#play-label")).toHaveText("Pause");
    await expect(page.locator("#active-note")).not.toHaveText("♪");
    await page.waitForTimeout(700);
    expect(Number(await page.locator("#seek").inputValue())).toBeGreaterThan(0.2);
    await page.locator("#stop").click();
    await expect(page.locator("#elapsed")).toHaveText("0:00");
  }
  await page.locator("#reviewed").uncheck();
  await expect(page.locator("#play")).toBeDisabled();
  expect(errors).toEqual([]);
});
