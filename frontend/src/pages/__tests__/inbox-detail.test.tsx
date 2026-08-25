import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import InboxDetailPage, { type InboxDetailApi } from "@/pages/inbox-detail";
import { I18nProvider } from "@/lib/i18n";
import { defaultQueryApi, QueryApiProvider } from "@/lib/queries";
import type { InboxItem } from "@/types";

const api: InboxDetailApi = {
  dismissPendingImport: vi.fn<InboxDetailApi["dismissPendingImport"]>(),
  getPendingImport: vi.fn<InboxDetailApi["getPendingImport"]>(),
  importPendingImport: vi.fn<InboxDetailApi["importPendingImport"]>(),
  retryPendingImport: vi.fn<InboxDetailApi["retryPendingImport"]>(),
  updatePendingImport: vi.fn<InboxDetailApi["updatePendingImport"]>(),
};

const reviewItem: InboxItem = {
  id: 7,
  owner_user_id: 1,
  source_kind: "url",
  source_url: "https://example.test/model",
  display_title: "Calibration cube",
  source_hostname: "example.test",
  state: "review",
  manifest: {
    kind: "model_files",
    files: [{ id: "file-1", name: "cube.stl", size: 42, file_type: "stl" }],
    selected_ids: ["file-1"],
  },
  target_collection_id: null,
  requested_tags: [],
  background_job_id: "job-7",
  resulting_model_id: null,
  results: [],
  error_code: null,
  retryable: false,
  attempt_count: 1,
  created_at: "2026-08-24T00:00:00Z",
  updated_at: "2026-08-24T00:00:00Z",
  completed_at: null,
  completion: null,
};

function renderPage() {
  return render(
    <I18nProvider>
      <QueryClientProvider client={new QueryClient()}>
        <QueryApiProvider value={{ ...defaultQueryApi, listCollections: async () => [] }}>
          <MemoryRouter initialEntries={["/inbox/7"]}>
            <Routes>
              <Route path="/inbox/:id" element={<InboxDetailPage api={api} />} />
            </Routes>
          </MemoryRouter>
        </QueryApiProvider>
      </QueryClientProvider>
    </I18nProvider>,
  );
}

describe("InboxDetailPage", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    localStorage.setItem("printstash.locale", "en");
    vi.mocked(api.updatePendingImport).mockResolvedValue(reviewItem);
  });

  it("polls after an import response still says review, then stops at a terminal state", async () => {
    const completedItem: InboxItem = { ...reviewItem, state: "completed", completion: "complete" };
    vi.mocked(api.getPendingImport)
      .mockResolvedValueOnce(reviewItem)
      .mockResolvedValueOnce(completedItem);
    vi.mocked(api.importPendingImport).mockResolvedValue(reviewItem);
    renderPage();
    await act(async () => {
      await Promise.resolve();
    });

    fireEvent.click(screen.getByRole("button", { name: "Import selected" }));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(api.importPendingImport).toHaveBeenCalledWith(7, ["file-1"]);

    await waitFor(() => expect(api.getPendingImport).toHaveBeenCalledTimes(2), { timeout: 2_500 });

    await new Promise((resolve) => window.setTimeout(resolve, 1_600));
    expect(api.getPendingImport).toHaveBeenCalledTimes(2);
  });

  it("shows partial results and retries only failed files", async () => {
    const partialItem: InboxItem = {
      ...reviewItem,
      state: "completed",
      completion: "partial",
      results: [
        {
          id: 11,
          source_selection_id: "file-1",
          result_key: "file-1",
          original_filename: "cube.stl",
          state: "failed",
          model_id: null,
          file_id: null,
          provenance_source_id: null,
          error_code: "unsupported_mesh",
          retryable: true,
          created_at: "2026-08-24T00:00:00Z",
          updated_at: "2026-08-24T00:00:00Z",
        },
      ],
    };
    vi.mocked(api.getPendingImport).mockResolvedValue(partialItem);
    vi.mocked(api.retryPendingImport).mockResolvedValue(partialItem);

    renderPage();

    expect(await screen.findByText("Partial")).toBeInTheDocument();
    const retry = screen.getByRole("button", { name: "Retry failed files" });
    await userEvent.setup().click(retry);
    expect(api.retryPendingImport).toHaveBeenCalledWith(7);
  });

  it("renders unsafe source URLs as plain text and normalized safe URLs as links", async () => {
    vi.mocked(api.getPendingImport).mockResolvedValue({
      ...reviewItem,
      source_url: "javascript:alert(1)",
    });
    const { unmount } = renderPage();
    expect((await screen.findByText("javascript:alert(1)")).closest("a")).toBeNull();
    unmount();

    vi.mocked(api.getPendingImport).mockResolvedValue({
      ...reviewItem,
      source_url: "HTTPS://EXAMPLE.TEST/model",
    });
    renderPage();
    expect(await screen.findByRole("link", { name: /example\.test/i })).toHaveAttribute(
      "href",
      "https://example.test/model",
    );
  });

  it("localizes Inbox detail UI while preserving captured source and file values", async () => {
    localStorage.setItem("printstash.locale", "es");
    vi.mocked(api.getPendingImport).mockResolvedValue(reviewItem);

    renderPage();

    expect(await screen.findByText("Calibration cube")).toBeInTheDocument();
    expect(screen.getByText("Fuente")).toBeInTheDocument();
    expect(screen.getByText("Archivos para importar")).toBeInTheDocument();
    expect(screen.getByLabelText("Seleccionar cube.stl")).toBeChecked();
    expect(screen.getByText("Destino")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("separadas por comas")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Importar seleccionados" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Descartar captura" })).toBeInTheDocument();
    expect(screen.getByText("example.test")).toBeInTheDocument();
    expect(screen.getByText("cube.stl")).toBeInTheDocument();
  });

  it("keeps the destination selector visibly focused for keyboard users", async () => {
    vi.mocked(api.getPendingImport).mockResolvedValue(reviewItem);
    const user = userEvent.setup();
    renderPage();

    const destination = await screen.findByRole("combobox", { name: "Destination" });
    for (let step = 0; step < 20 && document.activeElement !== destination; step += 1) {
      await user.tab();
    }

    expect(document.activeElement).toBe(destination);
    expect(destination).toHaveClass(
      "focus-visible:ring-ring",
      "focus-visible:ring-offset-2",
      "ring-offset-background",
    );
  });
});
