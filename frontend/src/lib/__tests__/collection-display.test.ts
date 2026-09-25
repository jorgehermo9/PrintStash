/** Collection slugs are navigation identities; labels follow stored names. */
import { describe, expect, it } from "vitest";
import { collectionDisplayPath } from "../collection-display";
import { aCollection } from "@/test-support/factories";

describe("collectionDisplayPath", () => {
  it("shows original folder names in a nested collection", () => {
    const collections = [
      aCollection({ id: 1, name: "Testing", path: "testing" }),
      aCollection({
        id: 2,
        name: "My Parts",
        path: "testing/my-parts",
        parent_id: 1,
      }),
    ];
    expect(collectionDisplayPath(collections, "testing/my-parts")).toBe("Testing/My Parts");
  });

  it("does not show an unknown slug path", () => {
    expect(collectionDisplayPath([], "testing/my-parts")).toBeNull();
  });
});
