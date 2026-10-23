/** Settings integration fields keep visible labels usable by keyboard and assistive technology. */
import { expect, test } from "@playwright/test";
import { localeDefinitions } from "../../src/locales/catalogs";
import type { NotificationsSettings, SpoolmanStatus } from "../../src/types";
import { collectPageProblems, useMockApi } from "./_setup";

useMockApi();

const notifications = {
  edit_epoch: "a".repeat(32),
  edit_version: 1,
  enabled: true,
  channels: [],
} satisfies NotificationsSettings;

const spoolman = {
  edit_epoch: "a".repeat(32),
  edit_version: 1,
  enabled: true,
  base_url: "http://spoolman.test:7912",
  has_api_key: false,
  write_enabled: false,
  write_force: false,
  connected: true,
  version: "0.18.0",
  error: null,
  native_hook_detected: false,
} satisfies SpoolmanStatus;

test.describe("Settings field labels", () => {
  for (const [locale, definition] of Object.entries(localeDefinitions)) {
    test.describe(locale, () => {
      test.beforeEach(async ({ page }) => {
        await page.setViewportSize({ width: 390, height: 844 });
        await page.route("**/api/v1/notifications", (route) =>
          route.fulfill({ json: notifications }),
        );
        await page.route("**/api/v1/notifications/deliveries?*", (route) =>
          route.fulfill({ json: [] }),
        );
        await page.route("**/api/v1/spoolman", (route) => route.fulfill({ json: spoolman }));
        await page.addInitScript((locale) => {
          localStorage.setItem("printstash.locale", locale);
        }, locale);
      });

      test("operates notification fields by their visible labels", async ({ page }) => {
        const problems = await collectPageProblems(page);
        await page.goto("/settings?section=notifications");
        await page.getByRole("button", { name: definition.messages["Add channel"] }).click();

        const name = page.getByRole("textbox", { name: definition.messages["Name"], exact: true });
        await name.fill("Workshop alerts");
        const type = page.getByRole("combobox", { name: definition.messages["Type"], exact: true });
        await page.getByText(definition.messages["Type"], { exact: true }).click();
        await expect(type).toBeFocused();
        await type.selectOption("telegram");
        await expect(
          page.getByLabel(definition.messages["Bot token"], { exact: true }),
        ).toBeVisible();
        await expect(
          page.getByLabel(definition.messages["Chat ID"], { exact: true }),
        ).toBeVisible();
        await expect(
          page.getByRole("group", { name: definition.messages["Events"], exact: true }),
        ).toBeVisible();
        await expect(
          page.getByRole("group", { name: definition.messages["Printers"], exact: true }),
        ).toBeVisible();
        expect(problems).toEqual([]);
      });

      test("operates Spoolman fields by their visible labels", async ({ page }) => {
        const problems = await collectPageProblems(page);
        await page.goto("/settings?section=spoolman");

        const baseUrl = page.getByLabel(definition.messages["Base URL"], { exact: true });
        await page.getByText(definition.messages["Base URL"], { exact: true }).click();
        await expect(baseUrl).toBeFocused();
        await baseUrl.fill("http://spoolman.test:7912");
        const keyLabel = definition.messages["API key "] + definition.messages["(optional)"];
        await expect(page.getByLabel(keyLabel, { exact: true })).toBeVisible();
        expect(problems).toEqual([]);
      });
    });
  }
});
