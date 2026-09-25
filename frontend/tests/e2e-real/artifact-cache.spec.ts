/** Administrator cache controls persist policy without confusing clear with disable. */
import { test, expect } from "./helpers";

test.describe("remote Artifact cache", () => {
  test("manages cache policy through Settings", async ({ page }) => {
    await page.goto("/settings");
    await page.getByRole("button", { name: "Storage", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Remote file cache" })).toBeVisible();
    await expect(
      page.getByRole("checkbox", { name: "Enable remote Artifact cache" }),
    ).toBeVisible();
    await page.getByRole("tab", { name: "Cache limits" }).click();
    await page.getByRole("spinbutton", { name: "Maximum cached files" }).fill("125");
    await page.getByRole("spinbutton", { name: "Maximum cache size (GB)" }).fill("2.5");
    const saved = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/v1/config/artifact-cache") &&
        response.request().method() === "PUT",
    );
    await page.getByRole("button", { name: "Save cache settings" }).click();
    const saveResponse = await saved;
    expect(saveResponse.ok()).toBe(true);
    const savedPolicy = (await saveResponse.json()).policy;
    expect(savedPolicy.max_entries).toBe(125);
    expect(savedPolicy.max_bytes).toBe(2.5 * 1024 ** 3);
    await expect(page.getByText("Artifact cache settings updated.")).toBeVisible();
    await page.reload();
    await page.getByRole("button", { name: "Storage", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Remote file cache" })).toBeVisible();
    await page.getByRole("tab", { name: "Cache limits" }).click();
    await expect(page.getByRole("spinbutton", { name: "Maximum cached files" })).toHaveValue("125");
    await expect(page.getByRole("spinbutton", { name: "Maximum cache size (GB)" })).toHaveValue(
      "2.5",
    );
    await page.getByRole("button", { name: "Clear cached files" }).click();
    await expect(page.getByText(/0 MB cached/)).toBeVisible();
    await page.getByRole("button", { name: "Reset to environment defaults" }).click();
    await expect(page.getByRole("spinbutton", { name: "Maximum cached files" })).toHaveValue(
      "10000",
    );
    await expect(page.getByRole("spinbutton", { name: "Maximum cache size (GB)" })).toHaveValue(
      "10",
    );
  });
});
