// PR evidence template: screenshots of a changed state.
// Copy with evidence.ts to frontend/tests/pr-evidence/, then replace the steps.
import { modelCard, uploadModel } from "../e2e-real/util";
import { expect, settle, test } from "./evidence";

test("model detail page", async ({ page }, testInfo) => {
  const name = `evidence-${Date.now()}`;
  await uploadModel(page, name, { mesh: true });

  await modelCard(page, name).click();
  await expect(page.getByRole("heading", { name })).toBeVisible();
  await settle(page);

  // The whole page for context, then the changed region up close.
  await page.screenshot({
    path: testInfo.outputPath("01-page.png"),
    fullPage: true,
    animations: "disabled",
  });
  await page
    .getByRole("main")
    .screenshot({ path: testInfo.outputPath("02-main.png"), animations: "disabled" });
});
