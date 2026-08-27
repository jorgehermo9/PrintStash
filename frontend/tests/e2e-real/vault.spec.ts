/**
 * Finding a model in a library that has grown too big to scroll.
 *
 * Search, filter, and the empty state — the three outcomes of looking for something. The
 * empty state is a real assertion rather than an afterthought: a search that silently
 * renders nothing looks identical to one that is still loading, and a user cannot tell
 * whether their model is missing or the page is broken.
 */
import { test, expect } from "./helpers";
import { modelCard, uploadGcodeModel, uploadModel } from "./util";

test.describe("vault", () => {
  test("search filters the library; list/grid toggle keeps the model visible", async ({ page }) => {
    const name = `e2e-vault-${Date.now()}`;
    await uploadGcodeModel(page, name);

    // Search narrows the grid to the matching model and reflects in the URL.
    await page.getByPlaceholder("Search PrintStash...").fill(name);
    await expect(page).toHaveURL(/[?&]q=/);
    await expect(modelCard(page, name)).toBeVisible();

    // List / grid toggle (title-labelled buttons) both keep the result.
    await page.getByRole("button", { name: "Display" }).click();
    await page.getByRole("menuitem", { name: "List View" }).click();
    await expect(modelCard(page, name)).toBeVisible();
    await page.getByRole("button", { name: "Display" }).click();
    await page.getByRole("menuitem", { name: "Grid View" }).click();
    await expect(modelCard(page, name)).toBeVisible();
  });

  test("the tag filter narrows the grid to tagged models", async ({ page }) => {
    const stamp = Date.now();
    const tag = `e2e-filter-${stamp}`;
    const tagged = `e2e-tagged-${stamp}`;
    const plain = `e2e-plain-${stamp}`;

    await uploadModel(page, tagged, { tag });
    await uploadGcodeModel(page, plain);

    // Click the tag chip in the sidebar; the grid keeps the tagged model and
    // drops the untagged one.
    await page.goto("/");
    await page.getByRole("button", { name: tag }).click();
    await expect(modelCard(page, tagged)).toBeVisible();
    await expect(modelCard(page, plain)).toHaveCount(0);
  });

  test("a meshless search term yields the empty state", async ({ page }) => {
    await page.goto("/");
    await page.getByPlaceholder("Search PrintStash...").fill(`no-such-model-${Date.now()}`);
    await expect(page).toHaveURL(/[?&]q=/);
    await expect(page.locator('a[href^="/models/"]')).toHaveCount(0);
  });
});
