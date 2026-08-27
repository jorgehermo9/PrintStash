/**
 * The models API client: browsing the library, editing it, and emptying its trash.
 *
 * The filter query is the interesting half. Every facet the grid offers becomes a query
 * parameter, and the multi-value ones — tags, file types, materials, slicers, printer
 * models, revision statuses, print outcomes, storage — are **repeated keys**, not
 * comma-joined strings, because the backend reads them as lists and a joined string
 * silently filters for one tag literally named `a,b`. That translation is invisible in
 * TypeScript and only fails against a running server, so it is pinned here.
 *
 * An empty filter set must also produce a bare path with no trailing `?`, or every cache
 * key in the app doubles.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  batchDeleteModels,
  batchMoveModels,
  batchSetRevisionLabels,
  batchTagModels,
  deleteModel,
  getArtifactOutcomes,
  getModel,
  getModelFacets,
  getModelPrintJobs,
  getModelPrinterFiles,
  getVaultStats,
  listModelPage,
  listModels,
  listOutlinerModels,
  listTrash,
  purgeExpiredTrash,
  purgeModel,
  restoreModel,
  starModel,
  unstarModel,
  updateModel,
} from "@/lib/api/models";
import { invalidateApiCache } from "@/lib/api/request";

import { expectRequest, fetchMock, lastBody, lastCall, respondWith } from "./_wire";

type ListParams = NonNullable<Parameters<typeof listModels>[0]>;

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  invalidateApiCache();
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("listModels", () => {
  it("asks for the whole library when nothing is filtered", async () => {
    respondWith([]);

    await listModels();

    // A bare path, not a trailing "?": otherwise every cache key doubles.
    expectRequest("/api/v1/models");
  });

  it("carries the single-value filters", async () => {
    respondWith([]);

    await listModels({ collection: "functional", q: "bracket", limit: 10, offset: 20 });

    const { url } = lastCall();
    expect(url).toContain("collection=functional");
    expect(url).toContain("q=bracket");
    expect(url).toContain("limit=10");
    expect(url).toContain("offset=20");
  });

  it("repeats a key for each tag rather than joining them", async () => {
    respondWith([]);

    await listModels({ tag: ["functional", "bracket"] });

    // `tag=a,b` would filter for one tag literally named "a,b".
    expect(lastCall().url).toContain("tag=functional&tag=bracket");
  });

  it.each([
    "file_type",
    "material_type",
    "slicer_name",
    "printer_model",
    "revision_status",
    "print_outcome",
    "storage",
  ])("repeats a key for each %s", async (key) => {
    respondWith([]);

    // SAFETY: `key` comes from the literal list above, every entry of which is
    // a multi-value filter declared on `ListParams` as `string[]`.
    await listModels({ [key]: ["one", "two"] } as ListParams);

    expect(lastCall().url).toContain(`${key}=one&${key}=two`);
  });

  it("sends the boolean filters as flags the backend recognises", async () => {
    respondWith([]);

    await listModels({ direct: true, favorites: true, printed: false });

    const { url } = lastCall();
    expect(url).toContain("direct=true");
    expect(url).toContain("favorites=true");
    expect(url).toContain("printed=false");
  });

  it("carries the printer filters", async () => {
    respondWith([]);

    await listModels({ printer_id: 3, printer_presence: "none" });

    const { url } = lastCall();
    expect(url).toContain("printer_id=3");
    expect(url).toContain("printer_presence=none");
  });

  it("carries the upload date window", async () => {
    respondWith([]);

    await listModels({
      uploaded_after: "2026-01-01",
      uploaded_before: "2026-02-01",
    });

    const { url } = lastCall();
    expect(url).toContain("uploaded_after=2026-01-01");
    expect(url).toContain("uploaded_before=2026-02-01");
  });
});

describe("listModelPage", () => {
  it("asks for the first page when no cursor is held", async () => {
    respondWith({ items: [], total: 0, next_cursor: null });

    await listModelPage();

    expectRequest("/api/v1/models/page");
  });

  it("carries the sort and the cursor together", async () => {
    respondWith({ items: [], total: 0, next_cursor: null });

    await listModelPage({ sort: "name-asc", cursor: "abc" });

    // The cursor is only meaningful under the sort it was issued for, so both
    // travel on every request.
    const { url } = lastCall();
    expect(url).toContain("sort=name-asc");
    expect(url).toContain("cursor=abc");
  });
});

describe("listOutlinerModels", () => {
  it("asks the outliner endpoint", async () => {
    respondWith([]);

    await listOutlinerModels();

    expectRequest("/api/v1/models/outliner");
  });

  it("carries the filters the tree supports", async () => {
    respondWith([]);

    await listOutlinerModels({ tag: ["functional"] });

    expect(lastCall().url).toContain("tag=functional");
  });
});

describe("getModelFacets", () => {
  it("asks the facets endpoint", async () => {
    respondWith({});

    await getModelFacets();

    expectRequest("/api/v1/models/facets");
  });

  it("repeats a key for each multi-value filter", async () => {
    respondWith({});

    await getModelFacets({ tag: ["a", "b"], file_type: ["stl", "gcode"] });

    // Facet counts must be computed under the same filters as the grid, or the
    // numbers do not match what the user is looking at.
    const { url } = lastCall();
    expect(url).toContain("tag=a&tag=b");
    expect(url).toContain("file_type=stl&file_type=gcode");
  });

  it("carries the same single-value filters as the listing", async () => {
    respondWith({});

    await getModelFacets({
      collection: "functional",
      direct: true,
      q: "bracket",
      printer_id: 3,
      printer_presence: "any",
      favorites: true,
      printed: true,
      uploaded_after: "2026-01-01",
      uploaded_before: "2026-02-01",
    });

    const { url } = lastCall();
    expect(url).toContain("collection=functional");
    expect(url).toContain("printer_presence=any");
    expect(url).toContain("uploaded_before=2026-02-01");
  });
});

describe("one model", () => {
  it("reads it", async () => {
    respondWith({ id: 1 });

    await getModel(1);

    expectRequest("/api/v1/models/1");
  });

  it("PATCHes only what changed", async () => {
    respondWith({ id: 1 });

    await updateModel(1, { name: "Renamed" });

    expectRequest("/api/v1/models/1", "PATCH");
    expect(lastBody()).toEqual({ name: "Renamed" });
  });

  it("trashes it", async () => {
    respondWith(null, 204);

    await deleteModel(1);

    expectRequest("/api/v1/models/1", "DELETE");
  });

  it("stars it", async () => {
    respondWith({ model_id: 1, starred: true });

    await starModel(1);

    expectRequest("/api/v1/models/1/star", "PUT");
  });

  it("unstars it", async () => {
    respondWith({ model_id: 1, starred: false });

    await unstarModel(1);

    expectRequest("/api/v1/models/1/star", "DELETE");
  });
});

describe("model sub-resources", () => {
  it("lists the printers holding its revisions", async () => {
    respondWith([]);

    await getModelPrinterFiles(1);

    expectRequest("/api/v1/models/1/printer-files");
  });

  it("lists its print history", async () => {
    respondWith([]);

    await getModelPrintJobs(1);

    expectRequest("/api/v1/models/1/print-jobs");
  });

  it("compares several revisions in one request", async () => {
    respondWith([]);

    await getArtifactOutcomes(1, [2, 3]);

    // One round trip for the comparison table the UI renders.
    expect(lastCall().url).toContain("file_id=2&file_id=3");
  });
});

describe("batch actions", () => {
  it("moves several models", async () => {
    respondWith({ succeeded_ids: [] });

    await batchMoveModels([1, 2], "functional");

    expectRequest("/api/v1/models/batch/move", "POST");
    expect(lastBody()).toEqual({ model_ids: [1, 2], collection: "functional" });
  });

  it("adds and removes tags in one request", async () => {
    respondWith({ succeeded_ids: [] });

    await batchTagModels([1], ["new"], ["old"]);

    // One request, so the whole change is atomic on the server.
    expect(lastBody()).toEqual({ model_ids: [1], add: ["new"], remove: ["old"] });
  });

  it("PATCHes revision labels", async () => {
    respondWith({ succeeded_ids: [] });

    await batchSetRevisionLabels([4], "PETG fast");

    expectRequest("/api/v1/models/batch/revision-labels", "PATCH");
    expect(lastBody()).toEqual({ file_ids: [4], revision_label: "PETG fast" });
  });

  it("clears revision labels with an explicit null", async () => {
    respondWith({ succeeded_ids: [] });

    await batchSetRevisionLabels([4], null);

    // `null` rather than an omitted key: "clear it" and "leave it" are
    // different requests.
    expect(lastBody()).toEqual({ file_ids: [4], revision_label: null });
  });

  it("trashes several models", async () => {
    respondWith({ succeeded_ids: [] });

    await batchDeleteModels([1, 2]);

    expectRequest("/api/v1/models/batch/delete", "POST");
  });
});

describe("trash", () => {
  it("lists what is in it", async () => {
    respondWith([]);

    await listTrash();

    expectRequest("/api/v1/models/trash");
  });

  it("restores a model", async () => {
    respondWith({ id: 1 });

    await restoreModel(1);

    expectRequest("/api/v1/models/1/restore", "POST");
  });

  it("purges one model", async () => {
    respondWith({ purged_model_ids: [1], purged_count: 1 });

    await purgeModel(1);

    expectRequest("/api/v1/models/1/purge", "DELETE");
  });

  it("purges everything past its retention", async () => {
    respondWith({ purged_model_ids: [], purged_count: 0 });

    await purgeExpiredTrash();

    expectRequest("/api/v1/models/trash/expired", "DELETE");
  });
});

describe("getVaultStats", () => {
  it("reads the library summary", async () => {
    respondWith({ model_count: 0 });

    await getVaultStats();

    expectRequest("/api/v1/models/stats");
  });
});
