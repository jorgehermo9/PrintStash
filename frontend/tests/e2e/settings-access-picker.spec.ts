/** A collection access picker must stay legible and selectable beyond the Settings card edge. */
import { expect, test } from "@playwright/test";
import { localeDefinitions } from "../../src/locales/catalogs";
import { aUser } from "../../src/test-support/account";
import { aCollectionNode, aCollectionPage } from "../../src/test-support/factories";
import { collectPageProblems, useMockApi } from "./_setup";

useMockApi();

declare global {
  interface Window {
    __printstashSettingsWindowErrors: string[];
  }
}

const collections = Array.from({ length: 12 }, (_, index) =>
  aCollectionNode({
    id: index + 1,
    name: `Workshop ${index + 1}`,
    path: `workshop-${index + 1}`,
    display_path: `Workshop ${index + 1}`,
  }),
);

const viewports = [
  { width: 320, height: 844, theme: "dark" },
  { width: 390, height: 844, theme: "light" },
  { width: 1440, height: 1000, theme: "dark" },
  { width: 1440, height: 1000, theme: "light" },
];

test.describe("Settings collection access picker", () => {
  for (const [locale, definition] of Object.entries(localeDefinitions)) {
    for (const viewport of viewports) {
      test.describe(`${locale} ${viewport.width} ${viewport.theme}`, () => {
        test.beforeEach(async ({ page }) => {
          await page.setViewportSize(viewport);
          await page.addInitScript(
            ({ locale, theme }) => {
              // Vite handles these errors before Playwright's pageerror event.
              window.__printstashSettingsWindowErrors = [];
              window.addEventListener("error", (event) => {
                window.__printstashSettingsWindowErrors.push(event.message);
              });
              localStorage.setItem("printstash.locale", locale);
              localStorage.setItem("printstash.theme", theme);
            },
            { locale, theme: viewport.theme },
          );
          await page.route("**/api/v1/admin/users", (route) =>
            route.fulfill({ json: [aUser({ id: 2, username: "maker", is_superuser: false })] }),
          );
          await page.route("**/api/v1/collections/search?*", (route) =>
            route.fulfill({ json: aCollectionPage(collections) }),
          );
          await page.route("**/api/v1/collections/*/permissions", (route) =>
            route.fulfill({ json: [] }),
          );
        });

        test("keeps the collection picker opaque and reachable", async ({ page }) => {
          const problems = await collectPageProblems(page);
          await page.goto("/settings?section=access");
          await page
            .getByRole("button", { name: definition.messages["Select collection"], exact: true })
            .click();
          const picker = page.getByRole("dialog");
          const lastOption = picker.getByRole("option", { name: /Workshop 12/ });
          await lastOption.scrollIntoViewIfNeeded();
          await expect(picker).toHaveCSS("opacity", "1");

          const box = await picker.boundingBox();
          expect(box).not.toBeNull();
          expect(box!.x).toBeGreaterThanOrEqual(0);
          expect(box!.y).toBeGreaterThanOrEqual(0);
          expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width);
          expect(box!.y + box!.height).toBeLessThanOrEqual(viewport.height);
          const backgroundAlpha = await picker.evaluate((element) => {
            const canvas = document.createElement("canvas");
            canvas.width = canvas.height = 1;
            const context = canvas.getContext("2d")!;
            context.fillStyle = getComputedStyle(element).backgroundColor;
            context.fillRect(0, 0, 1, 1);
            return context.getImageData(0, 0, 1, 1).data[3];
          });
          expect(backgroundAlpha).toBe(255);
          const search = picker.getByRole("textbox", {
            name: definition.messages["Find destination"],
            exact: true,
          });
          expect(
            await search.evaluate((element) => {
              const rect = element.getBoundingClientRect();
              return element.contains(
                document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2),
              );
            }),
          ).toBe(true);
          expect(
            await lastOption.evaluate((element) => {
              const rect = element.getBoundingClientRect();
              return element.contains(
                document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2),
              );
            }),
          ).toBe(true);
          await page.screenshot({
            path: test.info().outputPath("access-picker.png"),
            animations: "disabled",
          });
          expect(await page.evaluate(() => window.__printstashSettingsWindowErrors)).toEqual([]);
          expect(problems).toEqual([]);
        });

        test("applies the selected collection to the grant form", async ({ page }) => {
          const problems = await collectPageProblems(page);
          await page.goto("/settings?section=access");
          const card = page.getByRole("group", {
            name: definition.messages["Collection access"],
            exact: true,
          });
          await card
            .getByRole("combobox", { name: new RegExp("^" + definition.messages["User"]) })
            .selectOption("2");
          await card
            .getByRole("button", { name: definition.messages["Select collection"], exact: true })
            .click();
          await page
            .getByRole("dialog")
            .getByRole("option", { name: /Workshop 12/ })
            .click();

          await expect(page.getByRole("dialog")).toHaveCount(0);
          await expect(
            card.getByRole("button", { name: "Workshop 12", exact: true }),
          ).toBeVisible();
          await expect(
            card.getByRole("combobox", { name: new RegExp("^" + definition.messages["Role"]) }),
          ).toBeEnabled();
          await expect(
            card.getByRole("button", { name: definition.messages["Grant"], exact: true }),
          ).toBeEnabled();
          expect(await page.evaluate(() => window.__printstashSettingsWindowErrors)).toEqual([]);
          expect(problems).toEqual([]);
        });
      });
    }
  }
});
