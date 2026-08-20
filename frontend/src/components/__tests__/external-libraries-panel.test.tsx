import "@testing-library/jest-dom/vitest";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import { ExternalLibrariesPanel } from "@/components/external-libraries-panel";
import type { ExternalLibrary, ExternalLibraryScanSummary } from "@/types";

// Mock the API surface the panel consumes.
vi.mock("@/lib/api", () => ({
  createExternalLibrary: vi.fn(),
  deleteExternalLibrary: vi.fn(),
  getJobStatus: vi.fn(),
  getVaultConfig: vi.fn(),
  listExternalLibraries: vi.fn(),
  scanExternalLibrary: vi.fn(),
  updateExternalLibrary: vi.fn(),
  updateVaultConfig: vi.fn(),
}));
vi.mock("@/lib/toast", () => ({
  toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

import * as api from "@/lib/api";

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

function mockConfig(enabled: boolean, libs: ExternalLibrary[]) {
  (api.getVaultConfig as ReturnType<typeof vi.fn>).mockResolvedValue({
    external_libraries_enabled: enabled,
  });
  (api.listExternalLibraries as ReturnType<typeof vi.fn>).mockResolvedValue(libs);
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("ExternalLibrariesPanel", () => {
  it("shows the scan summary for an ok scan", async () => {
    mockConfig(true, [
      library({
        last_scan_status: "ok",
        last_scan_summary: summary({ added: 3, updated: 1, removed: 2 }),
      }),
    ]);
    render(<ExternalLibrariesPanel canEdit />);
    expect(await screen.findByText(/\+3 added · 1 updated · 2 removed/)).toBeInTheDocument();
  });

  it("surfaces the error message for a failed scan", async () => {
    mockConfig(true, [
      library({
        last_scan_status: "error",
        last_scan_summary: summary({ error: "root_empty_aborted", aborted: true }),
      }),
    ]);
    render(<ExternalLibrariesPanel canEdit />);
    expect(await screen.findByText("root_empty_aborted")).toBeInTheDocument();
  });

  // Regression: a PARTIAL scan (completed but some files failed to index) used to
  // render no summary at all — neither the counts nor the error indicator — so a
  // persistent per-file failure was silently hidden behind a non-green status.
  it("shows counts AND a warning for a partial scan", async () => {
    mockConfig(true, [
      library({
        last_scan_status: "partial",
        last_scan_summary: summary({
          added: 5,
          removed: 0,
          errors: ["/mnt/nas/3d/bad.stl: parse error"],
        }),
      }),
    ]);
    render(<ExternalLibrariesPanel canEdit />);
    expect(
      await screen.findByText(/\+5 added · 0 updated · 0 removed · 1 errors/),
    ).toBeInTheDocument();
    expect(screen.getByText(/some files could not be indexed/i)).toBeInTheDocument();
  });

  it("does not query libraries while the feature is disabled", async () => {
    mockConfig(false, []);
    render(<ExternalLibrariesPanel canEdit />);
    await waitFor(() => expect(api.getVaultConfig as ReturnType<typeof vi.fn>).toHaveBeenCalled());
    expect(api.listExternalLibraries).not.toHaveBeenCalled();
    expect(screen.queryByText(/no libraries yet/i)).not.toBeInTheDocument();
  });
});
