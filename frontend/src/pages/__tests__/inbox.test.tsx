import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { InboxItem } from "@/types";
import { I18nProvider } from "@/lib/i18n";
import InboxPage, { type InboxPageDeps } from "@/pages/inbox";

const listPendingImports = vi.fn<InboxPageDeps["listPendingImports"]>();
const retryPendingImport = vi.fn<InboxPageDeps["retryPendingImport"]>();
const deps: InboxPageDeps = { listPendingImports, retryPendingImport };

const pendingImport: InboxItem = {
  id: 1,
  owner_user_id: 1,
  source_kind: "url",
  source_url: "https://example.test/model",
  display_title: null,
  source_hostname: "printables.com",
  state: "review",
  manifest: { kind: "direct" },
  target_collection_id: null,
  requested_tags: [],
  background_job_id: null,
  resulting_model_id: null,
  results: [],
  error_code: null,
  retryable: false,
  attempt_count: 0,
  created_at: "2026-08-24T00:00:00Z",
  updated_at: "2026-08-24T00:00:00Z",
  completed_at: null,
  completion: null,
};

describe("InboxPage localization", () => {
  beforeEach(() => {
    localStorage.setItem("printstash.locale", "es");
    vi.mocked(listPendingImports).mockResolvedValue([pendingImport]);
  });

  it("localizes Inbox UI while preserving dynamic source data", async () => {
    render(
      <I18nProvider>
        <MemoryRouter>
          <InboxPage deps={deps} />
        </MemoryRouter>
      </I18nProvider>,
    );

    expect(
      await screen.findByRole("heading", { name: "Importaciones pendientes" }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("Necesita revisión")).toHaveLength(2);
    expect(screen.getByText("printables.com")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Revisar" })).toHaveAttribute("href", "/inbox/1");
  });

  it("shows partial and failed imports with a retry action", async () => {
    localStorage.setItem("printstash.locale", "en");
    const failedImport: InboxItem = {
      ...pendingImport,
      id: 2,
      state: "failed",
      retryable: true,
      completion: "partial",
    };
    vi.mocked(listPendingImports).mockResolvedValue([pendingImport, failedImport]);
    vi.mocked(retryPendingImport).mockResolvedValue(failedImport);

    render(
      <I18nProvider>
        <MemoryRouter>
          <InboxPage deps={deps} />
        </MemoryRouter>
      </I18nProvider>,
    );

    expect(await screen.findByText("Partial")).toBeInTheDocument();
    const retry = screen.getByRole("button", { name: "Retry" });
    await retry.click();
    expect(retryPendingImport).toHaveBeenCalledWith(2);
  });
});
