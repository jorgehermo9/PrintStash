/*
 * The vault, and the three requests it must get right.
 *
 * Sorting is server-owned: one cursor page, globally sorted. A client that
 * re-sorted the page it already has paginates a different order than the one it
 * displays, which surfaces as models appearing twice or not at all as the user
 * scrolls.
 *
 * The display choice survives a reload, because it is a preference and a
 * preference that resets is worse than no preference.
 *
 * Mobile skips the outliner request entirely. The outliner is not rendered on a
 * phone, so fetching its tree is a wasted round trip on the connection least able
 * to afford one.
 */
import { expect, test } from "@playwright/test";

import { useMockApi } from "./_setup";

useMockApi();

test.describe("vault route", () => {
  test("vault display choice survives reload", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Display" }).click();
    await page.getByRole("menuitem", { name: "List View" }).click();
    await expect(page.getByText("Thumb", { exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByText("Thumb", { exact: true })).toBeVisible();
  });

  test("vault sort requests one globally sorted cursor page", async ({ page }) => {
    const pageRequests: string[] = [];
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (url.pathname === "/api/v1/models/page") pageRequests.push(url.search);
    });
    await page.goto("/");
    await expect(page.getByText("skadis_kitchen-roll_screw").first()).toBeVisible();

    await page.getByRole("button", { name: "Sort models" }).click();
    await Promise.all([
      page.waitForRequest((request) => {
        const url = new URL(request.url());
        return (
          url.pathname === "/api/v1/models/page" && url.searchParams.get("sort") === "success-desc"
        );
      }),
      page.getByRole("menuitem", { name: "Best success rate" }).click(),
    ]);
    await expect(page.getByText("skadis_kitchen-roll_screw").first()).toBeVisible();
    await page.waitForTimeout(200);

    expect(
      pageRequests.filter((query) => new URLSearchParams(query).get("sort") === "success-desc"),
    ).toHaveLength(1);
  });

  test("mobile vault skips the desktop outliner request", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const outlinerRequests: string[] = [];
    page.on("request", (request) => {
      if (new URL(request.url()).pathname === "/api/v1/models/outliner") {
        outlinerRequests.push(request.url());
      }
    });

    await page.goto("/");
    await expect(page.getByText("skadis_kitchen-roll_screw").first()).toBeVisible();
    await page.waitForTimeout(200);
    expect(outlinerRequests).toEqual([]);
  });
});
