import { describe, expect, it } from "vitest";

import { buildBrowserCaptureMessage, type BrowserCaptureMessage } from "../capture-adapter.ts";
import { browserCaptureRoute } from "../capture-routing.ts";

describe("browser capture popup routing", () => {
  it("fails closed for a Printables source with inconsistent ready and zero-candidate metadata", () => {
    const capture = buildBrowserCaptureMessage({
      provider: "Printables",
      pageUrl: "https://www.printables.com/model/3161-3d-benchy/files",
      pageTitle: "3DBenchy",
    });
    const inconsistentCapture: BrowserCaptureMessage = {
      ...capture,
      state: "ready",
      candidates: [],
    };

    expect(browserCaptureRoute(inconsistentCapture)).toBe("manual_file");
  });

  it("uses the normalized source provider when deciding whether candidates are safe", () => {
    const capture = buildBrowserCaptureMessage({
      provider: "Printables",
      pageUrl: "https://www.printables.com/model/3161-3d-benchy/files",
      pageTitle: "3DBenchy",
      jsonLd: [
        JSON.stringify({
          distribution: [{ contentUrl: "https://media.printables.com/files/benchy.3mf" }],
        }),
      ],
    });

    expect(browserCaptureRoute(capture)).toBe("candidate_confirmation");
  });
});
