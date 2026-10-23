/** Member Settings distinguish inaccessible administrator information from unfinished reads. */
import { expect, test } from "@playwright/test";
import { localeDefinitions } from "../../src/locales/catalogs";
import { aUser } from "../../src/test-support/account";
import { useMockApi } from "./_setup";

useMockApi();

test.describe("Member Settings presentation", () => {
  for (const [locale, definition] of Object.entries(localeDefinitions)) {
    test.describe(locale, () => {
      test.beforeEach(async ({ page }) => {
        const member = aUser({ id: 2, username: "member", is_superuser: false });
        await page.setViewportSize({ width: 390, height: 844 });
        await page.route("**/api/v1/auth/me", (route) => route.fulfill({ json: member }));
        await page.addInitScript(
          ({ locale, member }) => {
            localStorage.setItem("printstash.locale", locale);
            localStorage.setItem("printstash.user", JSON.stringify(member));
          },
          { locale, member },
        );
      });

      test("omits inaccessible system health for a member", async ({ page }) => {
        await page.goto("/settings?section=overview");

        const system = page.getByRole("group", {
          name: definition.messages["System"],
          exact: true,
        });
        await expect(
          system.getByText(definition.messages["Storage backend"], { exact: true }),
        ).toBeVisible();
        await expect(
          system.getByText(definition.messages["Vault version"], { exact: true }),
        ).toHaveCount(0);
        await expect(
          system.getByText(definition.messages["Database"], { exact: true }),
        ).toHaveCount(0);
        await expect(
          system.getByText(definition.messages["Loading..."], { exact: true }),
        ).toHaveCount(0);
      });

      test("explains member storage permissions in the selected language", async ({ page }) => {
        await page.goto("/settings?section=storage");

        await expect(
          page.getByText(definition.messages["settings.storageAdministratorRequired"], {
            exact: true,
          }),
        ).toBeVisible();
        await expect(
          page.getByText(definition.messages["Sign in to modify configuration."], { exact: true }),
        ).toHaveCount(0);
        await expect(
          page.getByRole("button", {
            name: definition.messages["Save configuration"],
            exact: true,
          }),
        ).toHaveCount(0);
      });
    });
  }
});
