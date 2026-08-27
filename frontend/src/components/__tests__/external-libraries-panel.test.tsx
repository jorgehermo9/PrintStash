/*
 * The panel that tells an operator whether their NAS scan worked.
 *
 * A scan has three outcomes and the panel is the only place they are visible.
 * "OK" and "failed" are easy; the one that matters is *partial* — some files
 * indexed, some errored — because rendering it as a plain count is a green result
 * for a scan that half worked, and the operator never learns some of their models
 * are missing. So the partial case asserts both the counts and the warning.
 *
 * The panel also must not query the libraries API while the feature is disabled.
 * That is not an optimisation: on an installation whose operator never enabled
 * external libraries, the call is a 403 in the console on the settings page.
 */

import "@testing-library/jest-dom/vitest";
import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import {
  ExternalLibrariesPanel,
  type ExternalLibrariesApi,
} from "@/components/external-libraries-panel";
import type { ExternalLibrary, ExternalLibraryScanSummary } from "@/types";

function summary(over: Partial<ExternalLibraryScanSummary> = {}): ExternalLibraryScanSummary {
  return {
    added: 0,
    updated: 0,
    removed: 0,
    skipped: 0,
    errors: [],
    error: null,
    aborted: false,
    ...over,
  };
}

function library(over: Partial<ExternalLibrary> = {}): ExternalLibrary {
  return {
    id: 1,
    name: "NAS models",
    root_path: "/mnt/nas/3d",
    enabled: true,
    scan_interval_minutes: 60,
    scan_schedule: "0 * * * *",
    watch_mode: "auto",
    fs_kind: "local",
    watch_active: true,
    collection_mode: "mirror",
    target_collection_id: null,
    last_scanned_at: "2026-06-20T10:00:00Z",
    last_scan_status: "ok",
    last_scan_summary: summary(),
    ...over,
  };
}

/**
 * A stub of the panel's API port: the feature flag and library list answer
 * with the given values, and the mutations record calls without a network.
 */
function stubApi(enabled: boolean, libs: ExternalLibrary[]) {
  return {
    isFeatureEnabled: vi.fn<ExternalLibrariesApi["isFeatureEnabled"]>().mockResolvedValue(enabled),
    setFeatureEnabled: vi.fn<ExternalLibrariesApi["setFeatureEnabled"]>().mockResolvedValue(),
    list: vi.fn<ExternalLibrariesApi["list"]>().mockResolvedValue(libs),
    create: vi.fn<ExternalLibrariesApi["create"]>(),
    update: vi.fn<ExternalLibrariesApi["update"]>(),
    remove: vi.fn<ExternalLibrariesApi["remove"]>(),
    scan: vi.fn<ExternalLibrariesApi["scan"]>(),
    jobStatus: vi.fn<ExternalLibrariesApi["jobStatus"]>(),
  } satisfies ExternalLibrariesApi;
}

describe("ExternalLibrariesPanel", () => {
  it("shows the scan summary for an ok scan", async () => {
    const api = stubApi(true, [
      library({
        last_scan_status: "ok",
        last_scan_summary: summary({ added: 3, updated: 1, removed: 2 }),
      }),
    ]);
    render(<ExternalLibrariesPanel canEdit api={api} />);
    expect(await screen.findByText(/\+3 added · 1 updated · 2 removed/)).toBeInTheDocument();
  });

  it("surfaces the error message for a failed scan", async () => {
    const api = stubApi(true, [
      library({
        last_scan_status: "error",
        last_scan_summary: summary({ error: "root_empty_aborted", aborted: true }),
      }),
    ]);
    render(<ExternalLibrariesPanel canEdit api={api} />);
    expect(await screen.findByText("root_empty_aborted")).toBeInTheDocument();
  });

  // Regression: a PARTIAL scan (completed but some files failed to index) used to
  // render no summary at all — neither the counts nor the error indicator — so a
  // persistent per-file failure was silently hidden behind a non-green status.
  it("shows counts AND a warning for a partial scan", async () => {
    const api = stubApi(true, [
      library({
        last_scan_status: "partial",
        last_scan_summary: summary({
          added: 5,
          removed: 0,
          errors: ["/mnt/nas/3d/bad.stl: parse error"],
        }),
      }),
    ]);
    render(<ExternalLibrariesPanel canEdit api={api} />);
    expect(
      await screen.findByText(/\+5 added · 0 updated · 0 removed · 1 errors/),
    ).toBeInTheDocument();
    expect(screen.getByText(/some files could not be indexed/i)).toBeInTheDocument();
  });

  it("does not query libraries while the feature is disabled", async () => {
    const api = stubApi(false, []);
    render(<ExternalLibrariesPanel canEdit api={api} />);
    await waitFor(() => expect(api.isFeatureEnabled).toHaveBeenCalled());
    expect(api.list).not.toHaveBeenCalled();
    expect(screen.queryByText(/no libraries yet/i)).not.toBeInTheDocument();
  });
});
