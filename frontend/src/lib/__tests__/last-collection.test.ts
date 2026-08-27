/*
 * Putting the user back where they were when they click "Vault".
 *
 * The remembered value is a collection *path*, and the href built from it goes
 * into the router — so encoding is the whole risk. A path with nesting and
 * spaces (`Parts/Cable Clips`) that is not encoded produces a URL that resolves
 * to something else or to nothing, and the user lands on an empty page having
 * asked to go back to their models.
 *
 * The root is stored as "no collection" rather than as an empty path, because an
 * empty path round-tripped through the href builder is the one value that would
 * silently mean "the root" in one place and "a collection named nothing" in
 * another.
 */

import { afterEach, describe, expect, it } from "vitest";

import {
  LAST_COLLECTION_STORAGE_KEY,
  lastVaultHref,
  readLastCollection,
  rememberLastCollection,
} from "@/lib/last-collection";

afterEach(() => {
  window.localStorage.removeItem(LAST_COLLECTION_STORAGE_KEY);
});

describe("readLastCollection", () => {
  it("returns null and the root href when nothing is stored", () => {
    expect(readLastCollection()).toBeNull();
    expect(lastVaultHref()).toBe("/");
  });

  it("remembers a collection path and builds a restoring href", () => {
    rememberLastCollection("spoolers");
    expect(readLastCollection()).toBe("spoolers");
    expect(lastVaultHref()).toBe("/?c=spoolers");
  });

  it("encodes paths with nesting and spaces", () => {
    rememberLastCollection("spoolers/old prints");
    expect(lastVaultHref()).toBe("/?c=spoolers%2Fold%20prints");
  });

  it("clears the remembered collection at the root", () => {
    rememberLastCollection("spoolers");
    rememberLastCollection(null);
    expect(readLastCollection()).toBeNull();
    expect(lastVaultHref()).toBe("/");
  });
});
