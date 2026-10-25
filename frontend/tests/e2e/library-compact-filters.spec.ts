/**
 * Desktop filters share the browsing surface with the expanded tree and results.
 * Date fields must not cover chips or push the first model below a 1080p screen.
 */
import { expect, test } from "@playwright/test";
import { collectPageProblems, useMockApi } from "./_setup";

useMockApi();

test.describe("compact Library filters", () => {
  test.use({ viewport: { width: 1920, height: 1080 } });

  test("keeps loading feedback in the existing filter row", async ({ page }) => {
    const pending = Promise.withResolvers<void>();
    await page.route("**/api/v1/models/facets**", async (route) => {
      await pending.promise;
      await route.fulfill({
        json: {
          file_type: [{ value: "3mf", count: 7 }],
          material_type: [],
          slicer_name: [],
          printer_model: [],
          revision_status: [],
          print_outcome: [],
          storage: [],
          printed: [],
        },
      });
    });
    try {
      await page.goto("/?file_type=3mf");
      const filters = page.getByRole("main").getByRole("region", { name: "Filters", exact: true });
      const loading = filters.getByRole("status", { name: "Loading filter values…" });
      const uploaded = filters.getByRole("button", { name: "Uploaded", exact: true });
      await expect(loading).toBeVisible();
      await expect(filters.getByRole("checkbox", { name: "3MF", exact: true })).toBeChecked();
      await expect(uploaded).toBeEnabled();
      await expect(page.getByTitle("Remove File type: 3MF", { exact: true })).toBeVisible();
      await page.evaluate(() => document.fonts.ready);
      const before = await uploaded.boundingBox();
      const spinner = await loading.boundingBox();
      const panel = await filters.boundingBox();
      expect(before).not.toBeNull();
      expect(spinner).not.toBeNull();
      expect(panel).not.toBeNull();
      expect(
        Math.abs(spinner!.y + spinner!.height / 2 - before!.y - before!.height / 2),
      ).toBeLessThan(2);
      const similar = filters.getByRole("checkbox", { name: "Has similar candidates" });
      const checkboxBox = await similar.boundingBox();
      expect(checkboxBox).not.toBeNull();
      await page.mouse.move(
        checkboxBox!.x + checkboxBox!.width / 2,
        checkboxBox!.y + checkboxBox!.height / 2,
      );
      await page.mouse.down();
      pending.resolve();
      await expect(loading).toHaveCount(0);
      await page.mouse.up();
      await expect(similar).toBeChecked();
      await expect(page).toHaveURL(/has_similar_candidates=yes/);
      await expect(filters.getByRole("checkbox", { name: "3MF", exact: true })).toBeChecked();
      expect((await uploaded.boundingBox())!.y).toBe(before!.y);
      expect((await filters.boundingBox())!.height).toBe(panel!.height);
    } finally {
      pending.resolve();
    }
  });

  test("keeps expanded dates alongside the open tree on 1080p", async ({ page }) => {
    const problems = await collectPageProblems(page);
    await page.goto(
      "/?file_type=3mf&printed=no&storage=vault&uploaded_after=2026-01-01&uploaded_before=2026-12-31",
    );
    const main = page.getByRole("main");
    const filters = main.getByRole("region", { name: "Filters", exact: true });
    await expect(main).toHaveAttribute("aria-busy", "false");
    await expect(filters.getByRole("checkbox", { name: "3MF", exact: true })).toBeVisible();
    await expect(
      filters
        .getByRole("group", { name: "Printed", exact: true })
        .getByRole("button", { name: "No", exact: true }),
    ).toHaveAttribute("aria-pressed", "true");
    await expect(filters.getByLabel("After", { exact: true })).toHaveValue("2026-01-01");
    await expect(filters.getByLabel("Before", { exact: true })).toHaveValue("2026-12-31");
    await expect(
      page.locator("aside").getByRole("button", { name: "All Models", exact: true }),
    ).toBeVisible();
    await page
      .locator("aside")
      .getByRole("button", { name: "Expand", exact: true })
      .first()
      .click();
    await expect(
      page.locator("aside").getByRole("button", { name: "Collapse", exact: true }).first(),
    ).toBeVisible();
    await expect(page.getByTitle("Remove File type: 3MF", { exact: true })).toBeVisible();
    const model = main.locator("[data-library-entry]").first();
    await expect(model).toBeVisible();
    const panelBox = await filters.boundingBox();
    const modelBox = await model.boundingBox();
    expect(panelBox).not.toBeNull();
    expect(modelBox).not.toBeNull();
    expect(panelBox!.height).toBeLessThan(200);
    expect(modelBox!.y).toBeGreaterThan(panelBox!.y + panelBox!.height);
    expect(modelBox!.y + modelBox!.height).toBeLessThan(1080);
    await expect(page.getByRole("dialog")).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    await page.evaluate(async () => {
      await document.fonts.ready;
      await Promise.all(
        [...document.images]
          .filter(
            (image) => image.loading !== "lazy" || image.getBoundingClientRect().top < innerHeight,
          )
          .map((image) => image.decode().catch(() => {})),
      );
    });
    await page.screenshot({ path: "../reports/desktop-compact-filters-1080p.png" });
    expect(problems).toEqual([]);
  });

  test("keeps active chips when the inline controls are collapsed", async ({ page }) => {
    await page.goto("/?file_type=3mf&uploaded_after=2026-01-01");
    const filters = page.getByRole("main").getByRole("region", { name: "Filters", exact: true });
    await expect(filters).toBeVisible();
    await page
      .getByRole("button", { name: "Filters", exact: true })
      .filter({ visible: true })
      .click();
    await expect(filters).toHaveCount(0);
    await expect(page.getByTitle("Remove File type: 3MF", { exact: true })).toBeVisible();
    await expect(page).toHaveURL(/file_type=3mf/);
  });

  test("wraps inline fields within a narrow desktop surface", async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 768 });
    await page.goto("/?file_type=3mf&uploaded_after=2026-01-01&printed_after=2026-01-01");
    const filters = page.getByRole("main").getByRole("region", { name: "Filters", exact: true });
    await expect(filters.getByLabel("After", { exact: true })).toBeVisible();
    await expect(filters.getByLabel(/Finished on or after/)).toBeVisible();
    expect(await filters.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(
      true,
    );
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
  });

  test("retains the filter drawer on a phone", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    await page
      .getByRole("main")
      .getByRole("button", { name: "Filters", exact: true })
      .filter({ visible: true })
      .click();
    const drawer = page.getByRole("dialog", { name: "Filters", exact: true });
    await expect(drawer).toBeVisible();
    await expect(drawer.getByRole("button", { name: "Uploaded", exact: true })).toBeVisible();
  });
});
