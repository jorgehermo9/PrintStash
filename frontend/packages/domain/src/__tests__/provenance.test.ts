import { describe, expect, it } from "vitest";

import { formatInboxCompletion, provenanceOriginKey, type ProvenanceOrigin } from "../provenance";

describe("provenance semantics", () => {
  it("exposes the origin key without choosing a display language", () => {
    const origins: ProvenanceOrigin[] = ["confirmed", "inferred", "user"];
    expect(origins.map(provenanceOriginKey)).toEqual(origins);
  });

  it("distinguishes a complete import from a partial one", () => {
    expect(formatInboxCompletion("complete")).toBe("Complete");
    expect(formatInboxCompletion("partial")).toBe("Partial");
    expect(formatInboxCompletion(null)).toBe("In progress");
  });
});
