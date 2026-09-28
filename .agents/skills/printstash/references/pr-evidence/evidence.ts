// Shared by the PR evidence templates; copy it alongside them.
import type { BrowserContext, Page } from "@playwright/test";

import { test as realTest } from "../e2e-real/helpers";

export { expect } from "../e2e-real/helpers";

// The dev server mounts the React Query devtools button; keep it out of frame.
const HIDE_DEV_CHROME = `[aria-label="Open Tanstack query devtools"] { display: none !important; }`;
const FRAME = { width: 1280, height: 720 };

async function hideDevChrome(page: Page): Promise<void> {
  await page.addInitScript((css) => {
    document.addEventListener("DOMContentLoaded", () => {
      const style = document.createElement("style");
      style.textContent = css;
      document.head.append(style);
    });
  }, HIDE_DEV_CHROME);
}

export const test = realTest.extend<{ record: () => Promise<Page> }>({
  page: async ({ page }, use) => {
    await hideDevChrome(page);
    await use(page);
  },
  // Call after setup: returns a page signed in like `page` whose every frame
  // is recorded to flow.webm, so the video shows only the flow.
  record: async ({ browser, page }, use, testInfo) => {
    let context: BrowserContext | undefined;
    await use(async () => {
      context = await browser.newContext({
        viewport: FRAME,
        storageState: await page.context().storageState(),
        recordVideo: { dir: testInfo.outputPath("raw"), size: FRAME },
      });
      const recorded = await context.newPage();
      await hideDevChrome(recorded);
      return recorded;
    });
    if (!context) return;
    const pages = context.pages();
    await context.close();
    for (const recorded of pages) {
      const video = recorded.video();
      if (!video) throw new Error("recorded context produced no video");
      await video.saveAs(testInfo.outputPath("flow.webm"));
      await video.delete();
    }
  },
});

// Keep a state on screen long enough for a viewer to read it.
export async function hold(page: Page, ms = 1200): Promise<void> {
  await page.waitForTimeout(ms);
}

// A route can pass its first assertion while its content is still loading;
// capture only after the network is quiet.
export async function settle(page: Page): Promise<void> {
  await page.waitForLoadState("networkidle");
}
