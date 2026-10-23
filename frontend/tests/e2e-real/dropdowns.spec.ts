/**
 * Dropdown placement is observable only in a browser with the real page layout.
 * Statistics and a view-only Model put their triggers near the phone screen's
 * left edge; every option must remain reachable without horizontal scrolling.
 */
import { test, expect, authBundleFor, authedContext } from "./helpers";
import { modelCard, uploadModel } from "./util";

test.describe("dropdown viewport placement", () => {
  test("keeps Statistics customization reachable on a phone", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/statistics");
    await page.getByRole("button", { name: "Customize", exact: true }).click();
    const panel = page.getByRole("dialog");
    await expect(panel).toHaveAttribute("data-state", "open");
    const bounds = await panel.boundingBox();
    expect(bounds).not.toBeNull();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
    const workload = panel.getByRole("checkbox", { name: "Printer workload" });
    await workload.uncheck();
    await page.keyboard.press("Escape");
    await page.reload();
    await page.getByRole("button", { name: "Customize", exact: true }).click();
    await expect(page.getByRole("checkbox", { name: "Printer workload" })).not.toBeChecked();
  });

  test("repositions an open dropdown after a viewport resize", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/statistics");
    await page.getByRole("button", { name: "Customize", exact: true }).click();
    await page.setViewportSize({ width: 390, height: 844 });
    const panel = page.getByRole("dialog");
    await expect(async () => {
      const bounds = await panel.boundingBox();
      expect(bounds).not.toBeNull();
      expect(bounds!.x).toBeGreaterThanOrEqual(0);
      expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
    }).toPass();
    await panel.getByRole("checkbox", { name: "Top collections" }).uncheck();
    await expect(panel.getByRole("checkbox", { name: "Top collections" })).not.toBeChecked();
  });

  test("keeps the final dropdown option reachable in a short viewport", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 260 });
    await page.goto("/statistics");
    await page.getByRole("button", { name: "Customize", exact: true }).click();
    const panel = page.getByRole("dialog");
    await expect(panel).toHaveAttribute("data-state", "open");
    const bounds = await panel.boundingBox();
    expect(bounds).not.toBeNull();
    expect(bounds!.y).toBeGreaterThanOrEqual(0);
    expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(260);
    await panel.getByRole("checkbox", { name: "Top collections" }).uncheck();
    await expect(panel.getByRole("checkbox", { name: "Top collections" })).not.toBeChecked();
  });

  test("keeps a member Model action menu inside the phone screen", async ({ page, browser }) => {
    const stamp = Date.now();
    const name = `dropdown-model-${stamp}`;
    const username = `dropdown-viewer-${stamp}`;
    const password = "test-password-123";
    const collectionResponse = await page.request.post("/api/v1/collections", {
      data: { name: `dropdown-collection-${stamp}` },
    });
    expect(collectionResponse.ok()).toBeTruthy();
    const collection: { id: number; name: string } = await collectionResponse.json();
    const userResponse = await page.request.post("/api/v1/admin/users", {
      data: { username, password },
    });
    expect(userResponse.ok()).toBeTruthy();
    const user: { id: number } = await userResponse.json();
    let modelId: string | undefined;
    try {
      await uploadModel(page, name, { collection: collection.name });
      await modelCard(page, name).click();
      await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
      modelId = new URL(page.url()).pathname.split("/").at(-1);
      const grant = await page.request.put(
        `/api/v1/collections/${collection.id}/permissions/${user.id}`,
        {
          data: { role: "view" },
        },
      );
      expect(grant.ok()).toBeTruthy();
      const member = await authedContext(browser, await authBundleFor(username, password));
      try {
        await member.page.setViewportSize({ width: 390, height: 844 });
        await member.page.goto(`/models/${modelId}`);
        const trigger = member.page.getByRole("button", { name: "Model actions" });
        await trigger.click();
        const panel = member.page.getByRole("menu");
        await expect(panel).toHaveAttribute("data-state", "open");
        const bounds = await panel.boundingBox();
        expect(bounds).not.toBeNull();
        expect(bounds!.x).toBeGreaterThanOrEqual(0);
        expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
        await expect(panel.getByRole("menuitem", { name: "Delete model" })).toBeVisible();
        await member.page.keyboard.press("Escape");
        await expect(trigger).toBeFocused();
      } finally {
        await member.context.close();
      }
    } finally {
      if (modelId) {
        expect((await page.request.delete(`/api/v1/models/${modelId}`)).ok()).toBeTruthy();
        expect((await page.request.delete(`/api/v1/models/${modelId}/purge`)).ok()).toBeTruthy();
      }
      expect((await page.request.delete(`/api/v1/collections/${collection.id}`)).ok()).toBeTruthy();
      expect((await page.request.delete(`/api/v1/admin/users/${user.id}`)).ok()).toBeTruthy();
    }
  });
});
