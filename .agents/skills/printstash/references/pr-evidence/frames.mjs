// Sample frames from a recorded video so they can be looked at before attaching.
// Usage (from frontend/): node tests/pr-evidence/frames.mjs <video.webm> <out-dir>
import { chromium } from "@playwright/test";

const [video, outDir] = process.argv.slice(2);
if (!video || !outDir) throw new Error("usage: frames.mjs <video.webm> <out-dir>");

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
await page.goto(`file://${video}`);
const duration = await page.evaluate(async () => {
  const player = document.querySelector("video");
  player.pause();
  if (player.readyState < 1) await new Promise((resolve) => (player.onloadedmetadata = resolve));
  // Recorded WebM carries no duration header until it has been seeked to the end.
  if (!Number.isFinite(player.duration)) {
    player.currentTime = 1e9;
    await new Promise((resolve) => (player.onseeked = resolve));
  }
  return player.duration;
});
for (const fraction of [0.1, 0.5, 0.95]) {
  await page.evaluate(async (time) => {
    const player = document.querySelector("video");
    player.currentTime = time;
    await new Promise((resolve) => (player.onseeked = resolve));
  }, duration * fraction);
  await page.screenshot({ path: `${outDir}/frame-${Math.round(fraction * 100)}.png` });
}
console.log(`${video}: ${duration.toFixed(1)}s, frames in ${outDir}`);
await browser.close();
