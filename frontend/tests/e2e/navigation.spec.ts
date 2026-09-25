/*
 * "Pending Imports" is reachable, in both navigation shells, on nested routes.
 *
 * Desktop puts it in the profile menu and mobile in the bottom bar — two
 * components with two lists, so a route added to one is missing on the platform
 * nobody tested. It is also the screen a user goes to when an import is waiting
 * for them, which means an unreachable entry leaves the import sitting there.
 *
 * The nested-route half is the active-state prefix match: an inbox *detail* page
 * must still mark Pending as current, or the user appears to have navigated out
 * of the section they are looking at.
 */
import { expect, test } from "@playwright/test";

import { useMockApi } from "./_setup";

useMockApi();

test.describe("app navigation", () => {
  test("keeps the desktop header focused on search", async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto("/");
    await expect(page.locator("header").getByRole("link", { name: "Similar models" })).toHaveCount(
      0,
    );
    await expect(page.locator("header").getByRole("searchbox")).toBeVisible();
  });

  test("desktop library keeps Similar models in Model detail", async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto("/");
    await expect(page.getByRole("link", { name: "Similar models", exact: true })).toHaveCount(0);
    await page.goto("/models/1");
    const similar = page.getByRole("tab", { name: "Similar", exact: true });
    await expect(similar).toBeVisible();
    await similar.click();
    await expect(similar).toHaveAttribute("aria-selected", "true");
    await page.screenshot({ path: testInfo.outputPath("similar-entry-desktop.png") });
  });

  test("mobile tab opens Similar models", async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "All Models" })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("similar-entry-mobile.png") });

    await page.getByRole("link", { name: "Similar", exact: true }).click();

    await expect(page).toHaveURL(/\/library\/similar$/);
    await expect(page.getByRole("heading", { name: "Similar models" })).toBeVisible();
  });

  test("desktop navigation reaches Pending Imports and marks nested routes active", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto("/");
    await page.getByRole("button", { name: "tester" }).click();

    const pending = page.getByRole("menuitem", { name: "Pending" });
    await expect(pending).toHaveAttribute("href", "/inbox");
    await pending.click();
    await expect(page).toHaveURL(/\/inbox$/);
    await expect(page.getByRole("heading", { name: "Pending Imports" })).toBeVisible();

    await page.getByRole("button", { name: "tester" }).click();
    await expect(page.getByRole("menuitem", { name: "Pending" })).toHaveAttribute(
      "aria-current",
      "page",
    );

    await page.goto("/inbox/41");
    await expect(page).toHaveURL(/\/inbox\/41$/);
    await page.getByRole("button", { name: "tester" }).click();
    await expect(page.getByRole("menuitem", { name: "Pending" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  test("mobile navigation reaches Pending Imports and stays active on detail routes", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");

    const pending = page.getByRole("link", { name: "Pending" });
    await expect(pending).toHaveAttribute("href", "/inbox");
    await pending.click();
    await expect(page).toHaveURL(/\/inbox$/);
    await expect(page.getByRole("heading", { name: "Pending Imports" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Pending" })).toHaveAttribute(
      "aria-current",
      "page",
    );

    await page.goto("/inbox/41");
    await expect(page.getByRole("link", { name: "Pending" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });
});
