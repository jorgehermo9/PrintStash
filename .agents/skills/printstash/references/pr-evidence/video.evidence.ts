// PR evidence template: a recorded flow.
// Copy with evidence.ts to frontend/tests/pr-evidence/, then replace the steps.
import { modelCard, uploadModel } from "../e2e-real/util";
import { expect, hold, settle, test } from "./evidence";

// slowMo paces every action so a viewer can follow the flow.
test.use({ launchOptions: { slowMo: 400 } });

test("search the library", async ({ page, record }) => {
  const name = `evidence-${Date.now()}`;
  // Setup on the unrecorded page.
  await uploadModel(page, name, { mesh: true });

  const flow = await record();
  await flow.goto("/");
  await settle(flow);
  await hold(flow);

  await flow
    .getByRole("searchbox", { name: "Search library" })
    .pressSequentially(name, { delay: 80 });
  await expect(modelCard(flow, name)).toBeVisible();
  await hold(flow);

  await modelCard(flow, name).click();
  await expect(flow.getByRole("heading", { name })).toBeVisible();
  await settle(flow);
  // The video ends when the page closes; hold the final state.
  await hold(flow, 2000);
});
