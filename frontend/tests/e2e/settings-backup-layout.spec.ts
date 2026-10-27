/** Backup card titles and translated actions remain readable at phone and desktop widths. */
import { expect, test } from "@playwright/test";
import { localeDefinitions } from "../../src/locales/catalogs";
import { useMockApi } from "./_setup";

useMockApi();

test.describe("Backup responsive layout", () => {
  for (const [locale, definition] of Object.entries(localeDefinitions)) {
    for (const viewport of [
      { width: 320, height: 844, theme: "dark" },
      { width: 390, height: 844, theme: "light" },
      { width: 1440, height: 1000, theme: "light" },
    ]) {
      for (const card of [
        {
          title: definition.messages["settings.backupRetentionTitle"],
          action: definition.messages["settings.backupRetentionSave"],
        },
        {
          title: definition.messages["settings.backupPolicyTitle"],
          action: definition.messages["settings.backupPolicySave"],
        },
        {
          title: definition.messages["Manual backup"],
          action: definition.messages[" Backup now"].trim(),
        },
      ]) {
        test.describe(locale + " " + viewport.width + "px " + card.title, () => {
          test.beforeEach(async ({ page }) => {
            await page.setViewportSize(viewport);
            await page.addInitScript(
              ({ locale, theme }) => {
                localStorage.setItem("printstash.locale", locale);
                localStorage.setItem("printstash.theme", theme);
              },
              { locale, theme: viewport.theme },
            );
            await page.goto("/settings?section=backup");
            await expect(page.getByRole("group", { name: card.title, exact: true })).toBeVisible();
          });

          test("keeps backup card headings clear of actions", async ({ page }) => {
            const group = page.getByRole("group", { name: card.title, exact: true });
            const heading = group.getByRole("heading", { name: card.title, exact: true });
            await heading.scrollIntoViewIfNeeded();

            const title = await heading.boundingBox();
            const action = await group
              .getByRole("button", { name: card.action, exact: true })
              .boundingBox();
            expect(title).not.toBeNull();
            expect(action).not.toBeNull();
            expect(
              title!.y + title!.height <= action!.y || title!.x + title!.width <= action!.x,
            ).toBe(true);
            expect(
              await heading.evaluate((element) => element.scrollWidth <= element.clientWidth + 1),
            ).toBe(true);
          });

          test("contains translated backup action labels", async ({ page }) => {
            const group = page.getByRole("group", { name: card.title, exact: true });
            const button = group.getByRole("button", { name: card.action, exact: true });
            await button.scrollIntoViewIfNeeded();

            const box = await button.boundingBox();
            const frame = await group.boundingBox();
            expect(box).not.toBeNull();
            expect(frame).not.toBeNull();
            expect(box!.x).toBeGreaterThanOrEqual(frame!.x);
            expect(box!.x + box!.width).toBeLessThanOrEqual(frame!.x + frame!.width);
            expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width);
            expect(
              await button.evaluate((element) => element.scrollWidth <= element.clientWidth + 1),
            ).toBe(true);
          });
        });
      }
    }
  }
});
