/**
 * The deployment-level API clients: first-run setup, vault config, health, and backups.
 *
 * Setup and config are the two endpoints that decide where a whole library lives, so what
 * matters here is that a read of the *current* deployment state is never cached: health
 * details and the release check answer "is this install healthy right now", and a stale
 * answer sends an operator looking for a problem that is already fixed — or worse, not
 * looking for one that is not.
 *
 * The backup download is the odd one out and is worth stating: it is the only client that
 * drives the browser's own download machinery, so it has to honour the server's
 * `Content-Disposition` filename and fall back to a sensible one when the header is
 * missing, then release the object URL it created.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createBackup, downloadBackup, listBackups, restoreBackup } from "@/lib/api/backup";
import {
  completeSetup,
  getHealthDetails,
  getLatestRelease,
  getSetupStatus,
  getVaultConfig,
  rebuildModelThumbnails,
  updateVaultConfig,
} from "@/lib/api/config";
import { invalidateApiCache } from "@/lib/api/request";

import { expectRequest, fetchMock, lastBody, lastCall, respondWith } from "./_wire";

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  invalidateApiCache();
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("setup", () => {
  it("reads whether the deployment has been set up", async () => {
    respondWith({ needs_setup: true });

    await getSetupStatus();

    expectRequest("/api/v1/setup/status");
  });

  it("POSTs the first-run answers", async () => {
    respondWith({ access_token: "token" });

    await completeSetup({
      setup_token: "token",
      username: "alice",
      password: "Password123",
    });

    expectRequest("/api/v1/setup", "POST");
    expect(lastBody()).toMatchObject({ username: "alice" });
  });
});

describe("vault config", () => {
  it("reads it", async () => {
    respondWith({ storage_backend: "local" });

    await getVaultConfig();

    expectRequest("/api/v1/config");
  });

  it("PUTs a change", async () => {
    respondWith({ storage_backend: "s3" });

    await updateVaultConfig({ storage_backend: "s3" });

    expectRequest("/api/v1/config", "PUT");
    expect(lastBody()).toEqual({ storage_backend: "s3" });
  });
});

describe("health", () => {
  it("reads the details without caching them", async () => {
    respondWith({ status: "ok" });

    await getHealthDetails();

    // A stale health answer sends an operator looking for a problem that is
    // already fixed, or not looking for one that is not.
    expectRequest("/api/v1/health/details");
    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });

  it("reads the cached release status by default", async () => {
    respondWith({ status: "up_to_date", update_available: false });

    await getLatestRelease();

    expectRequest("/api/v1/health/releases/latest");
  });

  it("forces a re-check when the operator asks for one", async () => {
    respondWith({ status: "up_to_date", update_available: false });

    await getLatestRelease(true);

    // The server caches this to stay off GitHub's rate limit; the flag is how
    // the "check now" button gets past it.
    expectRequest("/api/v1/health/releases/latest?refresh=true");
  });
});

describe("rebuildModelThumbnails", () => {
  it("asks for a forced rebuild", async () => {
    respondWith({ job_id: "abc", state: "pending" });

    await rebuildModelThumbnails();

    expectRequest("/api/v1/files/thumbnails/rebuild?force=true", "POST");
  });
});

describe("backups", () => {
  it("creates one", async () => {
    respondWith({ backup_id: "b1" });

    await createBackup();

    expectRequest("/api/v1/backups", "POST");
  });

  it("lists them", async () => {
    respondWith([]);

    await listBackups();

    expectRequest("/api/v1/backups");
  });

  it("restores one by id", async () => {
    respondWith({ backup_id: "b1", restored_files: 3 });

    await restoreBackup("2026-01-01T00:00:00Z");

    // The id is a timestamp, so it has to survive URL encoding.
    expectRequest("/api/v1/backups/2026-01-01T00%3A00%3A00Z/restore", "POST");
  });
});

describe("downloadBackup", () => {
  function stubDownload() {
    const created: string[] = [];
    const revoked: string[] = [];
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: () => {
        created.push("blob:x");
        return "blob:x";
      },
      revokeObjectURL: (url: string) => revoked.push(url),
    });
    return { created, revoked };
  }

  it("uses the filename the server named", async () => {
    fetchMock.mockResolvedValue(
      new Response("archive", {
        status: 200,
        headers: { "content-disposition": 'attachment; filename="printstash-b1.tar.gz"' },
      }),
    );
    stubDownload();
    const clicked: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(
      function (this: HTMLAnchorElement) {
        clicked.push(this.download);
      },
    );

    await downloadBackup("b1");

    expect(clicked).toEqual(["printstash-b1.tar.gz"]);
  });

  it("falls back to a sensible filename when the server names none", async () => {
    fetchMock.mockResolvedValue(new Response("archive", { status: 200 }));
    stubDownload();
    const clicked: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(
      function (this: HTMLAnchorElement) {
        clicked.push(this.download);
      },
    );

    await downloadBackup("b1");

    expect(clicked).toEqual(["printstash-backup-b1.tar.gz"]);
  });

  it("releases the object URL it created", async () => {
    fetchMock.mockResolvedValue(new Response("archive", { status: 200 }));
    const { revoked } = stubDownload();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    await downloadBackup("b1");

    // A leaked blob URL pins the whole archive in memory for the tab's life.
    expect(revoked).toEqual(["blob:x"]);
  });

  it("leaves no anchor behind in the document", async () => {
    fetchMock.mockResolvedValue(new Response("archive", { status: 200 }));
    stubDownload();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    await downloadBackup("b1");

    expect(document.querySelectorAll("a[download]")).toHaveLength(0);
  });
});
