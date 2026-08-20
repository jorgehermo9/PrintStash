import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ingestUrl, selectCollectionMembers, selectModelFiles } from "@/lib/api/models";
import { invalidateApiCache } from "@/lib/api/request";

/**
 * Pin the collection / multi-file import API client to the exact wire contract
 * the backend ingest router expects: paths, verbs, and request bodies. A drift
 * here silently breaks the URL-import review flows.
 */

function jsonResponse(data: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => data,
    text: async () => JSON.stringify(data),
    headers: new Headers({ "content-type": "application/json" }),
  } as unknown as Response;
}

const fetchMock = vi.fn();

function lastCall() {
  const call = fetchMock.mock.calls.at(-1)!;
  return { url: call[0] as string, init: call[1] as RequestInit };
}

const queued = { job_id: "job-1", state: "pending", message: "ingestion queued" };

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  invalidateApiCache();
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ingestUrl", () => {
  it("POSTs the review flag for collection imports", async () => {
    fetchMock.mockResolvedValue(jsonResponse(queued));

    await ingestUrl({
      url: "https://www.printables.com/@u/collections/3525050",
      review: true,
    });

    const { url, init } = lastCall();
    expect(url).toBe("/api/v1/ingest/url");
    expect(init).toMatchObject({ method: "POST" });
    expect(JSON.parse(init.body as string)).toMatchObject({
      url: "https://www.printables.com/@u/collections/3525050",
      review: true,
    });
  });
});

describe("selectModelFiles", () => {
  it("POSTs the chosen file ids to the files token endpoint", async () => {
    fetchMock.mockResolvedValue(jsonResponse(queued));

    await selectModelFiles("tok-files", {
      file_ids: ["10", "11"],
      collection: "Cats",
    });

    const { url, init } = lastCall();
    expect(url).toBe("/api/v1/ingest/url/files/tok-files/select");
    expect(init).toMatchObject({ method: "POST" });
    expect(JSON.parse(init.body as string)).toEqual({
      file_ids: ["10", "11"],
      collection: "Cats",
    });
  });
});

describe("selectCollectionMembers", () => {
  it("POSTs the chosen member ids to the collection token endpoint", async () => {
    fetchMock.mockResolvedValue(jsonResponse(queued));

    await selectCollectionMembers("tok-coll", { member_ids: ["1", "2"] });

    const { url, init } = lastCall();
    expect(url).toBe("/api/v1/ingest/collection/tok-coll/select");
    expect(init).toMatchObject({ method: "POST" });
    expect(JSON.parse(init.body as string)).toEqual({ member_ids: ["1", "2"] });
  });
});
