/**
 * The single-resource API clients: filaments, printer profiles, saved views, statistics,
 * documents, notifications, maintenance and shares.
 *
 * Each is a thin translation from a function call to one HTTP request, and that is
 * precisely why they need tests: a wrong path or verb still type-checks, still compiles,
 * and fails only against a running backend. So every case here asserts the request that
 * was made, not just the value that came back.
 *
 * Two patterns recur and both are deliberate. A **write is a POST/PATCH/PUT/DELETE to a
 * sub-resource**, never a flag on the parent, so each act is separately auditable. And a
 * read that answers "what is true right now" — a document being edited, an audit still
 * running, the shares currently granted — is fetched `fresh`, because a cached answer
 * there is a UI showing state the server has already moved past.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  createDocument,
  deleteDocument,
  getDocument,
  listDocuments,
  updateDocument,
  uploadDocument,
  uploadDocumentImage,
} from "@/lib/api/documents";
import {
  createFilamentProfile,
  deleteFilamentProfile,
  listFilamentProfiles,
  updateFilamentProfile,
} from "@/lib/api/filaments";
import {
  cancelVaultAudit,
  getLatestVaultAudit,
  getVaultAudit,
  ignoreAuditFinding,
  repairAuditFinding,
  startVaultAudit,
  verifyBackup,
} from "@/lib/api/maintenance";
import {
  createNotificationChannel,
  deleteNotificationChannel,
  getNotificationsSettings,
  setNotificationsEnabled,
  updateNotificationChannel,
} from "@/lib/api/notifications";
import {
  createPrinterProfile,
  deletePrinterProfile,
  listPrinterProfiles,
  updatePrinterProfile,
} from "@/lib/api/printer-profiles";
import { invalidateApiCache } from "@/lib/api/request";
import {
  createModelShare,
  listModelShares,
  revokeShare,
  sharedDownloadUrl,
  sharedGcodeUrl,
  sharedStlUrl,
  sharedThumbnailUrl,
} from "@/lib/api/share";
import { getPrintStatistics } from "@/lib/api/statistics";

import { expectRequest, fetchMock, lastBody, lastCall, lastForm, respondWith } from "./_wire";

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  invalidateApiCache();
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("filament profiles", () => {
  it("lists them", async () => {
    respondWith([{ id: 1, name: "PETG" }]);

    expect(await listFilamentProfiles()).toHaveLength(1);
    expectRequest("/api/v1/filament-profiles");
  });

  it("creates one", async () => {
    respondWith({ id: 1, name: "PETG" });

    await createFilamentProfile({ name: "PETG", material_type: "PETG" });

    expectRequest("/api/v1/filament-profiles", "POST");
    expect(lastBody()).toMatchObject({ name: "PETG" });
  });

  it("PATCHes only what changed", async () => {
    respondWith({ id: 1, name: "PETG" });

    await updateFilamentProfile(1, { cost_per_kg: 21 });

    expectRequest("/api/v1/filament-profiles/1", "PATCH");
    expect(lastBody()).toEqual({ cost_per_kg: 21 });
  });

  it("deletes one", async () => {
    respondWith(null, 204);

    await deleteFilamentProfile(1);

    expectRequest("/api/v1/filament-profiles/1", "DELETE");
  });
});

describe("printer profiles", () => {
  it("lists them", async () => {
    respondWith([]);

    await listPrinterProfiles();

    expectRequest("/api/v1/printer-profiles");
  });

  it("creates one", async () => {
    respondWith({ id: 1, name: "Voron" });

    await createPrinterProfile({ name: "Voron" });

    expectRequest("/api/v1/printer-profiles", "POST");
  });

  it("PATCHes only what changed", async () => {
    respondWith({ id: 1, name: "Voron" });

    await updatePrinterProfile(1, { name: "Voron 2.4" });

    expectRequest("/api/v1/printer-profiles/1", "PATCH");
  });

  it("deletes one", async () => {
    respondWith(null, 204);

    await deletePrinterProfile(1);

    expectRequest("/api/v1/printer-profiles/1", "DELETE");
  });
});

describe("print statistics", () => {
  it("asks for the window the caller chose", async () => {
    respondWith({ total_cost: 0 });

    await getPrintStatistics("90d");

    expectRequest("/api/v1/models/stats/prints?period=90d");
  });
});

describe("documents", () => {
  it("lists every document when no collection is named", async () => {
    respondWith([]);

    await listDocuments(null);

    expectRequest("/api/v1/documents");
  });

  it("filters by collection when one is named", async () => {
    respondWith([]);

    await listDocuments("functional/brackets");

    // The path is a user-typed string, so it has to survive URL encoding.
    expectRequest("/api/v1/documents?collection=functional%2Fbrackets");
  });

  it("reads one document fresh", async () => {
    respondWith({ id: 1, name: "Manual" });

    await getDocument(1);

    // A document someone is editing must not come from cache.
    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });

  it("creates a markdown document", async () => {
    respondWith({ id: 1, name: "Manual" });

    await createDocument({ name: "Manual", collection_id: null, body: "# Hi" });

    expectRequest("/api/v1/documents", "POST");
    expect(lastBody()).toMatchObject({ name: "Manual", body: "# Hi" });
  });

  it("uploads a binary document as multipart", async () => {
    respondWith({ id: 1, name: "Manual" });

    await uploadDocument(new File(["pdf"], "manual.pdf"), 3, "Manual");

    expectRequest("/api/v1/documents/upload", "POST");
    expect(() => lastForm()).not.toThrow();
  });

  it("carries the collection and name alongside the uploaded file", async () => {
    respondWith({ id: 1, name: "Manual" });

    await uploadDocument(new File(["pdf"], "manual.pdf"), 3, "Manual");

    const form = lastForm();
    expect(form.get("collection_id")).toBe("3");
    expect(form.get("name")).toBe("Manual");
  });

  it("leaves the collection out when the document belongs nowhere", async () => {
    respondWith({ id: 1, name: "Manual" });

    await uploadDocument(new File(["pdf"], "manual.pdf"), null);

    const form = lastForm();
    expect(form.get("collection_id")).toBeNull();
  });

  it("PUTs an edit", async () => {
    respondWith({ id: 1, name: "Manual" });

    await updateDocument(1, { body: "# Edited" });

    expectRequest("/api/v1/documents/1", "PUT");
  });

  it("deletes one", async () => {
    respondWith(null, 204);

    await deleteDocument(1);

    expectRequest("/api/v1/documents/1", "DELETE");
  });

  it("uploads an inline image to the document's own sub-resource", async () => {
    respondWith({ url: "/api/v1/documents/1/images/a.webp" });

    await uploadDocumentImage(1, new File(["png"], "a.png"));

    expectRequest("/api/v1/documents/1/images", "POST");
  });
});

describe("notifications", () => {
  it("reads the settings", async () => {
    respondWith({ enabled: false, channels: [] });

    await getNotificationsSettings();

    expectRequest("/api/v1/notifications");
  });

  it("PUTs the enabled flag", async () => {
    respondWith({ enabled: true });

    await setNotificationsEnabled(true);

    expectRequest("/api/v1/notifications", "PUT");
    expect(lastBody()).toEqual({ enabled: true });
  });

  it("creates a channel", async () => {
    respondWith({ id: 1, target: "webhook" });

    await createNotificationChannel({
      name: "Ops webhook",
      target: "webhook",
      config: { url: "https://hooks.test/x" },
      events: ["print_completed"],
    });

    expectRequest("/api/v1/notifications/channels", "POST");
  });

  it("PATCHes a channel", async () => {
    respondWith({ id: 1, target: "webhook" });

    await updateNotificationChannel(1, { enabled: false });

    expectRequest("/api/v1/notifications/channels/1", "PATCH");
  });

  it("deletes a channel", async () => {
    respondWith(null, 204);

    await deleteNotificationChannel(1);

    expectRequest("/api/v1/notifications/channels/1", "DELETE");
  });
});

describe("maintenance", () => {
  it("starts an audit in the mode the operator chose", async () => {
    respondWith({ id: 1, mode: "full" });

    await startVaultAudit("full");

    expectRequest("/api/v1/maintenance/audits", "POST");
    expect(lastBody()).toEqual({ mode: "full" });
  });

  it("reads the latest audit fresh", async () => {
    respondWith({ id: 1 });

    await getLatestVaultAudit();

    // A running audit's progress is the whole reason to poll it.
    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });

  it("reads one audit fresh", async () => {
    respondWith({ id: 1 });

    await getVaultAudit(1);

    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });

  it("cancels an audit through its own sub-resource", async () => {
    respondWith({ id: 1 });

    await cancelVaultAudit(1);

    expectRequest("/api/v1/maintenance/audits/1/cancel", "POST");
  });

  it("repairs a finding", async () => {
    respondWith({ id: 5 });

    await repairAuditFinding(5);

    expectRequest("/api/v1/maintenance/findings/5/repair", "POST");
  });

  it("ignores a finding", async () => {
    respondWith({ id: 5 });

    await ignoreAuditFinding(5);

    // Repair and ignore are different acts on the same finding, so they are
    // different endpoints rather than one with a mode flag.
    expectRequest("/api/v1/maintenance/findings/5/ignore", "POST");
  });

  it("verifies a backup by its id", async () => {
    respondWith({ valid: true });

    await verifyBackup("2026-01-01T00:00:00Z");

    expectRequest("/api/v1/backups/2026-01-01T00%3A00%3A00Z/verify", "POST");
  });
});

describe("shares", () => {
  it("creates a share link", async () => {
    respondWith({ id: 1, token: "abc" });

    await createModelShare(4, { expires_in_days: 7, allow_download: true });

    expectRequest("/api/v1/models/4/shares", "POST");
  });

  it("lists a model's shares fresh", async () => {
    respondWith([]);

    await listModelShares(4);

    // A revoked link that still shows is a link somebody thinks still works.
    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });

  it("revokes a share", async () => {
    respondWith(null, 204);

    await revokeShare(9);

    expectRequest("/api/v1/shares/9", "DELETE");
  });

  it("builds public URLs that carry the token rather than a login", () => {
    // These are handed to an <img>/<a>, which cannot send an Authorization
    // header, so the token has to be in the path.
    expect(sharedThumbnailUrl("abc")).toBe("/api/v1/share/abc/thumbnail");
    expect(sharedStlUrl("abc", 2)).toBe("/api/v1/share/abc/files/2/stl");
    expect(sharedDownloadUrl("abc", 2)).toBe("/api/v1/share/abc/files/2/download");
    expect(sharedGcodeUrl("abc", 2)).toBe("/api/v1/share/abc/files/2/gcode");
  });
});
