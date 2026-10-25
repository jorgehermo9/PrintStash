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

  test("desktop Library tools opens Similar models", async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto("/");
    const tools = page.getByRole("button", { name: "Library tools", exact: true });
    await tools.click();
    const similar = page.getByRole("menuitem", { name: "Similar models", exact: true });
    await expect(similar).toBeVisible();
    await expect(similar).toHaveAttribute("href", "/library/similar");
    await expect(page.locator('[aria-modal="true"]')).toHaveCount(0);
    await page.screenshot({ path: testInfo.outputPath("similar-entry-desktop.png") });
    await similar.click();
    await expect(page).toHaveURL(/\/library\/similar$/);
    await expect(page.getByRole("heading", { name: "Similar models", exact: true })).toBeVisible();
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
    await page.getByRole("button", { name: "My workspace" }).click();

    const pending = page.getByRole("menuitem", { name: "Pending" });
    await expect(pending).toHaveAttribute("href", "/inbox");
    await pending.click();
    await expect(page).toHaveURL(/\/inbox$/);
    await expect(page.getByRole("heading", { name: "Pending Imports" })).toBeVisible();

    await page.getByRole("button", { name: "My workspace" }).click();
    await expect(page.getByRole("menuitem", { name: "Pending" })).toHaveAttribute(
      "aria-current",
      "page",
    );

    await page.goto("/inbox/41");
    await expect(page).toHaveURL(/\/inbox\/41$/);
    await page.getByRole("button", { name: "My workspace" }).click();
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

for (const width of [1280, 390]) {
  test(`${width === 1280 ? "desktop" : "mobile"} navigation opens Builds`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "All Models" })).toBeVisible();
    if (width === 1280) {
      await page.getByRole("button", { name: "My workspace" }).click();
      await page.getByRole("menuitem", { name: "Builds", exact: true }).click();
    } else {
      await page.locator("nav").getByRole("button", { name: "More", exact: true }).click();
      await page
        .getByRole("dialog", { name: "More" })
        .getByRole("link", { name: "Builds", exact: true })
        .click();
      await expect(page.getByRole("dialog", { name: "More" })).toHaveCount(0);
    }
    await expect(page).toHaveURL(/\/builds$/);
    await expect(page.getByRole("heading", { name: "Builds", exact: true })).toBeVisible();
    await expect(page).toHaveTitle("Builds · PrintStash");
  });
}

test.describe("desktop workspace quick access", () => {
  test("opens workspace beside its trigger with the Library tree still usable", async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto(
      "/?file_type=3mf&printed=no&storage=vault&uploaded_after=2026-01-01&uploaded_before=2026-12-31",
    );
    await expect(page.getByRole("heading", { name: "All Models" })).toBeVisible();
    await expect(page.getByRole("main")).toHaveAttribute("aria-busy", "false");
    await expect(page.getByRole("region", { name: "Filters", exact: true })).toBeVisible();
    await page
      .locator("aside")
      .getByRole("button", { name: "Expand", exact: true })
      .first()
      .click();
    await expect(
      page.locator("aside").getByRole("button", { name: "Collapse", exact: true }).first(),
    ).toBeVisible();
    const quickAccess = page.getByRole("navigation", { name: "Quick access" });
    await expect(quickAccess.getByRole("link", { name: "Printers" })).toBeVisible();
    const trigger = page.getByRole("button", { name: "My workspace", exact: true });
    await trigger.click();
    const menu = page.getByRole("menu");
    await expect(menu.getByRole("menuitem", { name: "Builds", exact: true })).toBeVisible();
    await expect(menu.getByRole("menuitem", { name: "Statistics", exact: true })).toBeVisible();
    await expect(menu.getByRole("menuitem", { name: "Printers" })).toHaveCount(0);
    await expect(page.locator('[aria-modal="true"]')).toHaveCount(0);
    const triggerBox = await trigger.boundingBox();
    const menuBox = await menu.boundingBox();
    expect(triggerBox).not.toBeNull();
    expect(menuBox).not.toBeNull();
    if (triggerBox && menuBox) {
      expect(menuBox.y).toBeGreaterThanOrEqual(triggerBox.y + triggerBox.height);
      expect(menuBox.y - triggerBox.y - triggerBox.height).toBeLessThanOrEqual(10);
      expect(menuBox.x + menuBox.width).toBeLessThanOrEqual(1920);
    }
    await page.evaluate(async () => {
      await document.fonts.ready;
      await Promise.all(
        [...document.images]
          .filter((image) => image.getBoundingClientRect().top < innerHeight)
          .map((image) => image.decode()),
      );
    });
    await page.screenshot({ path: testInfo.outputPath("workspace-library-1080p.png") });
    await page.screenshot({ path: "../reports/desktop-workspace-1080p.png" });
    await quickAccess.getByRole("link", { name: "Printers" }).click();
    await expect(page).toHaveURL(/\/printers$/);
    await trigger.click();
    await expect(page.getByRole("menuitem", { name: "Printers" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });
});

test.describe("desktop Saved views quick access", () => {
  test("opens saved views above the fixed footer", async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto("/");
    await expect(page.getByRole("main")).toHaveAttribute("aria-busy", "false");
    const trigger = page
      .getByRole("navigation", { name: "Quick access" })
      .getByRole("button", { name: "Saved views", exact: true });
    await trigger.click();
    const panel = page.getByRole("dialog");
    await expect(panel.getByRole("textbox", { name: "Find a saved view" })).toBeVisible();
    const triggerBox = await trigger.boundingBox();
    const favoriteBox = await page
      .getByRole("navigation", { name: "Quick access" })
      .getByRole("button", { name: "Favorites", exact: true })
      .boundingBox();
    const panelBox = await panel.boundingBox();
    expect(triggerBox).not.toBeNull();
    expect(panelBox).not.toBeNull();
    expect(panelBox!.y + panelBox!.height).toBeLessThanOrEqual(triggerBox!.y);
    expect(panelBox!.x).toBeGreaterThanOrEqual(0);
    expect(Math.abs(panelBox!.x - triggerBox!.x)).toBeLessThanOrEqual(1);
    expect(favoriteBox).not.toBeNull();
    expect(Math.abs(triggerBox!.width - favoriteBox!.width)).toBeLessThanOrEqual(1);
    expect(panelBox!.x + panelBox!.width).toBeLessThanOrEqual(1920);
    await expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(panelBox!.y).toBeGreaterThanOrEqual(64);
    await page.keyboard.press("Escape");
    await expect(panel).toHaveCount(0);
    await expect(trigger).toBeFocused();
  });
});

test.describe("language dropdown", () => {
  test("frames language choices beside the header trigger", async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto("/");
    await expect(page.getByRole("main")).toHaveAttribute("aria-busy", "false");
    const trigger = page.getByRole("button", { name: "Language: English", exact: true });
    await trigger.click();
    const menu = page.getByRole("menu", { name: "Language", exact: true });
    await expect(menu.getByRole("menuitemradio", { name: "English", exact: true })).toBeFocused();
    const surface = await menu.evaluate((element) => {
      const style = getComputedStyle(element);
      return {
        background: style.backgroundColor,
        border: parseFloat(style.borderTopWidth),
        shadow: style.boxShadow,
      };
    });
    expect(surface.background).not.toBe("rgba(0, 0, 0, 0)");
    expect(surface.border).toBeGreaterThan(0);
    expect(surface.shadow).not.toBe("none");
    const triggerBox = await trigger.boundingBox();
    const menuBox = await menu.boundingBox();
    expect(triggerBox).not.toBeNull();
    expect(menuBox).not.toBeNull();
    expect(menuBox!.y).toBeGreaterThanOrEqual(triggerBox!.y + triggerBox!.height);
    expect(menuBox!.y - triggerBox!.y - triggerBox!.height).toBeLessThanOrEqual(10);
    expect(
      Math.abs(menuBox!.x + menuBox!.width - triggerBox!.x - triggerBox!.width),
    ).toBeLessThanOrEqual(1);
    await page.screenshot({
      path: testInfo.outputPath("language-menu-1080p.png"),
      animations: "disabled",
    });
    await page.keyboard.press("ArrowDown");
    await expect(menu.getByRole("menuitemradio", { name: "Español", exact: true })).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("button", { name: "Idioma: Español", exact: true })).toBeFocused();
    await expect(menu).toHaveCount(0);
  });
});
