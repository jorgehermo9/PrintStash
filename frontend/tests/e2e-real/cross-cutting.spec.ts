/**
 * The things every page depends on: the theme, the version, and no console errors.
 *
 * These are the checks that catch a breakage nobody's feature test would: a theme that
 * does not survive a reload, a deployment reporting the wrong version, or a route that
 * renders but throws on the way. The last one is deliberately broad — an uncaught error
 * on any core route is a bug somewhere, and this is the only test that will see it.
 */
import { test, expect } from "./helpers";

test("theme toggle flips and persists across reload", async ({ page }) => {
  await page.goto("/");
  const isDark = () => page.evaluate(() => document.documentElement.classList.contains("dark"));
  const before = await isDark();

  await page.getByRole("button", { name: "Toggle theme" }).first().click();
  await expect.poll(isDark).toBe(!before);

  await page.reload();
  await expect.poll(isDark).toBe(!before);
});

// The public /health is liveness-only (no version, to limit disclosure); the
// version lives on /health/details, gated to admins.
test("health endpoint reports the app version", async ({ page }) => {
  const res = await page.request.get("/api/v1/health/details");
  expect(res.ok()).toBeTruthy();
  const body = await res.json();
  expect(body.status).toBe("ok");
  expect(body.version).toMatch(/^\d+\.\d+/);
});

test("core routes load without uncaught errors", async ({ page }) => {
  const crashes: string[] = [];
  page.on("pageerror", (e) => crashes.push(e.message));

  for (const route of ["/", "/profiles", "/printers", "/statistics", "/settings"]) {
    await page.goto(route);
    await page.waitForLoadState("networkidle");
  }
  expect(crashes).toEqual([]);
});
