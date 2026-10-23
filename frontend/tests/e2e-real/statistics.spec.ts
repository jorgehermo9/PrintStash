import { test, expect } from "./helpers";

test("continues from empty Statistics to printer management", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/statistics");
  await page.getByRole("button", { name: "All time", exact: true }).click();
  await expect(
    page.getByText("No completed prints in this period.", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(
      "Statistics use completed print jobs. Review the selected period or manage your printers.",
      { exact: true },
    ),
  ).toBeVisible();

  await page.getByRole("link", { name: "Manage printers", exact: true }).click();

  await expect(page).toHaveURL(/\/printers$/);
  await expect(page.getByRole("heading", { name: "Printers", exact: true })).toBeVisible();
});
