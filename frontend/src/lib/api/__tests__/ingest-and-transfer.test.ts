/**
 * Getting things *into* the library, and getting the whole library back out.
 *
 * Every ingest path here is multipart or a token exchange, and both shapes have a failure
 * mode a type checker cannot see. A multipart upload must send a real `FormData` — a
 * JSON-serialised object type-checks and arrives as an unparseable body — and a two-step
 * import must present its token back on exactly the endpoint that issued it, since the
 * token *is* the staged work.
 *
 * The two downloads are the only clients that drive the browser's own save machinery:
 * they honour the server's `Content-Disposition` filename, fall back to a sensible one,
 * and release the object URL afterwards. A leaked blob URL pins a whole library archive
 * in memory for the life of the tab.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  addGcodeRevision,
  deleteFileRevision,
  downloadLibraryArchive,
  downloadModelExport,
  getJobStatus,
  importLibraryArchive,
  ingestArchive,
  ingestModel,
  ingestOrca,
  inspectArchive,
  listIngestJobs,
  selectArchiveEntries,
  updateFileRevision,
} from "@/lib/api/models";
import { listNotificationDeliveries, testNotificationChannel } from "@/lib/api/notifications";
import { invalidateApiCache } from "@/lib/api/request";
import {
  createSavedView,
  deleteSavedView,
  listSavedViews,
  updateSavedView,
} from "@/lib/api/saved-views";

import { expectRequest, fetchMock, lastBody, lastCall, respondWith } from "./_wire";

function form(): FormData {
  const data = new FormData();
  data.append("file", new File(["x"], "part.gcode"));
  return data;
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  invalidateApiCache();
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("uploads", () => {
  it.each([
    ["a sliced G-code file", () => ingestOrca(form()), "/api/v1/ingest/orca"],
    ["a source mesh", () => ingestModel(form()), "/api/v1/ingest/model"],
    ["an archive", () => ingestArchive(form()), "/api/v1/ingest/archive"],
    ["an archive for inspection", () => inspectArchive(form()), "/api/v1/ingest/archive/inspect"],
    [
      "a library archive",
      () => importLibraryArchive(new File(["zip"], "library.zip")),
      "/api/v1/models/library-import",
    ],
    ["a G-code revision", () => addGcodeRevision(4, form()), "/api/v1/models/4/gcode-revisions"],
  ])("POSTs %s as multipart", async (_name, call, url) => {
    respondWith({ job_id: "abc", state: "pending" });

    await call();

    // A JSON-serialised object type-checks and arrives as an unparseable body.
    expectRequest(url, "POST");
    expect(lastCall().init.body).toBeInstanceOf(FormData);
  });
});

describe("ingest jobs", () => {
  it("reads one job fresh", async () => {
    respondWith({ job_id: "abc", state: "running" });

    await getJobStatus("abc");

    // Polling a cached job status never sees it finish.
    expectRequest("/api/v1/ingest/jobs/abc");
    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });

  it("lists jobs fresh", async () => {
    respondWith([]);

    await listIngestJobs();

    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });
});

describe("selectArchiveEntries", () => {
  it("presents the archive id back on its own endpoint", async () => {
    respondWith({ job_id: "abc", state: "pending" });

    await selectArchiveEntries("arch-1", { names: ["cube.stl"] });

    // The id *is* the staged work; a wrong path imports nothing and loses it.
    expectRequest("/api/v1/ingest/archive/arch-1/select", "POST");
    expect(lastBody()).toMatchObject({ names: ["cube.stl"] });
  });
});

describe("file revisions", () => {
  it("PATCHes a revision", async () => {
    respondWith({ id: 4 });

    await updateFileRevision(4, 9, { revision_status: "known_good" });

    expectRequest("/api/v1/models/4/files/9/revision", "PATCH");
  });

  it("DELETEs a revision", async () => {
    respondWith({ id: 4 });

    await deleteFileRevision(4, 9);

    expectRequest("/api/v1/models/4/files/9/revision", "DELETE");
  });
});

describe("downloads", () => {
  function stubBrowserSave() {
    const revoked: string[] = [];
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: () => "blob:x",
      revokeObjectURL: (url: string) => revoked.push(url),
    });
    const clicked: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(
      function (this: HTMLAnchorElement) {
        clicked.push(this.download);
      },
    );
    return { clicked, revoked };
  }

  it("uses the filename the server named for a model export", async () => {
    fetchMock.mockResolvedValue(
      new Response("json", {
        status: 200,
        headers: { "content-disposition": 'attachment; filename="named.json"' },
      }),
    );
    const { clicked } = stubBrowserSave();

    await downloadModelExport("json");

    expect(clicked).toEqual(["named.json"]);
  });

  it("falls back to a name derived from the format", async () => {
    fetchMock.mockResolvedValue(new Response("csv", { status: 200 }));
    const { clicked } = stubBrowserSave();

    await downloadModelExport("csv");

    expect(clicked).toEqual(["printstash-model-export.csv"]);
  });

  it("asks for the format the caller chose", async () => {
    fetchMock.mockResolvedValue(new Response("csv", { status: 200 }));
    stubBrowserSave();

    await downloadModelExport("csv");

    expect(lastCall().url).toBe("/api/v1/models/export?format=csv");
  });

  it("saves the library archive under its portable name", async () => {
    fetchMock.mockResolvedValue(new Response("zip", { status: 200 }));
    const { clicked } = stubBrowserSave();

    await downloadLibraryArchive();

    // A fixed name, because the archive is meant to be recognisable on another
    // machine.
    expect(clicked).toEqual(["printstash-library-v1.zip"]);
  });

  it("releases the object URL after saving", async () => {
    fetchMock.mockResolvedValue(new Response("zip", { status: 200 }));
    const { revoked } = stubBrowserSave();

    await downloadLibraryArchive();

    expect(revoked).toEqual(["blob:x"]);
  });
});

describe("saved views", () => {
  it("lists them fresh", async () => {
    respondWith([]);

    await listSavedViews();

    // A saved view added in another tab should show up here.
    expectRequest("/api/v1/saved-views");
    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });

  it("creates one from a name and the current filters", async () => {
    respondWith({ id: 1, name: "PETG" });

    await createSavedView("PETG", {
      direct: false,
      tag: [],
      favorites: false,
      material_type: ["PETG"],
    });

    expectRequest("/api/v1/saved-views", "POST");
    expect(lastBody()).toEqual({
      name: "PETG",
      filters: { direct: false, tag: [], favorites: false, material_type: ["PETG"] },
    });
  });

  it("PATCHes only what changed", async () => {
    respondWith({ id: 1, name: "Renamed" });

    await updateSavedView(1, { name: "Renamed" });

    expectRequest("/api/v1/saved-views/1", "PATCH");
  });

  it("deletes one", async () => {
    respondWith(null, 204);

    await deleteSavedView(1);

    expectRequest("/api/v1/saved-views/1", "DELETE");
  });
});

describe("notification deliveries", () => {
  it("sends a test through the channel's own endpoint", async () => {
    respondWith({ ok: true });

    await testNotificationChannel(1);

    expectRequest("/api/v1/notifications/channels/1/test", "POST");
  });

  it("asks for a default page of deliveries", async () => {
    respondWith([]);

    await listNotificationDeliveries();

    expectRequest("/api/v1/notifications/deliveries?limit=50");
  });

  it("asks for the page the caller wants", async () => {
    respondWith([]);

    await listNotificationDeliveries(5);

    expectRequest("/api/v1/notifications/deliveries?limit=5");
  });
});
