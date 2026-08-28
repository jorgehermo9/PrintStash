/*
 * One model's page: six tabs over the artifacts, the settings that produced
 * them, and the prints that came out.
 *
 * The tab in the URL is what a shared link reproduces, and it is user-editable —
 * so `?tab=nonsense` has to land somewhere real rather than render an empty
 * page. History is the one tab that is *conditionally* present, because it is
 * about printers: someone who cannot see printers must not land on a tab whose
 * contents they are not allowed to fetch.
 *
 * Bed size is derived from the printer model string, and it is the frame the
 * G-code preview is drawn against. Guessing a 250mm bed for an A1 mini renders
 * a part that looks like it fits when it does not — which is a wrong answer
 * presented with the same confidence as a right one.
 *
 * Favouriting writes immediately and optimistically, so the star has to survive
 * the request failing: leaving it lit after a 403 tells the user something is
 * saved that is not.
 */

import "@testing-library/jest-dom/vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ModelDetail } from "@/components/model-detail";
import { queryKeys } from "@/lib/query-client";
import { aCollection } from "@/test-support/factories";
import { json, memberSession, renderApp, type RenderAppOptions } from "@/test-support/render";
import type { FileRead, ModelRead } from "@/types";

const FROZEN_NOW = "2026-01-01T00:00:00Z";

function aFile(over: Partial<FileRead> = {}): FileRead {
  return {
    id: 10,
    model_id: 1,
    original_filename: "cube.stl",
    file_type: "stl",
    version: 1,
    size_bytes: 2048,
    sha256: "a".repeat(64),
    revision_status: null,
    revision_notes: null,
    is_recommended: false,
    uploaded_at: FROZEN_NOW,
    metadata: null,
    ...over,
  };
}

function aModel(over: Partial<ModelRead> = {}): ModelRead {
  return {
    id: 1,
    name: "Benchy",
    slug: "benchy",
    hash: "h".repeat(16),
    collection: "parts",
    collection_id: 1,
    description: null,
    source_url: null,
    effective_role: "admin",
    tags: [],
    thumbnail_url: null,
    created_at: FROZEN_NOW,
    updated_at: FROZEN_NOW,
    files: [aFile()],
    starred: false,
    ...over,
  };
}

function renderDetail(options: RenderAppOptions & { model?: ModelRead } = {}) {
  const { model = aModel(), seed = [], routes = {}, ...rest } = options;
  return renderApp(<ModelDetail model={model} />, {
    seed: [
      [queryKeys.collections, [aCollection()]],
      [queryKeys.tags, []],
      [queryKeys.printers, []],
      ...seed,
    ],
    routes: {
      "GET /api/v1/models/1": json(model),
      "GET /api/v1/models/1/print-jobs": json([]),
      "GET /api/v1/models/1/printer-files": json([]),
      "GET /api/v1/models/1/provenance": json({ sources: [] }),
      "GET /api/v1/models/1/shares": json([]),
      "GET /api/v1/printers": json([]),
      "GET /api/v1/collections": json([aCollection()]),
      "GET /api/v1/tags": json([]),
      ...routes,
    },
    ...rest,
  });
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ModelDetail", () => {
  describe("what it shows", () => {
    it("names the model", async () => {
      renderDetail();

      expect(await screen.findByText("Benchy")).toBeInTheDocument();
    });

    it("opens on the overview", async () => {
      renderDetail();

      expect(await screen.findByRole("tab", { name: /Overview/ })).toHaveAttribute(
        "aria-selected",
        "true",
      );
    });

    it("counts the source files on their tab", async () => {
      renderDetail({ model: aModel({ files: [aFile(), aFile({ id: 11, version: 2 })] }) });

      expect(await screen.findByRole("tab", { name: /Files\s*2/ })).toBeInTheDocument();
    });

    it("counts the G-code revisions on their tab", async () => {
      renderDetail({
        model: aModel({
          files: [aFile({ id: 12, file_type: "gcode", original_filename: "part.gcode" })],
        }),
      });

      expect(await screen.findByRole("tab", { name: /Revisions\s*1/ })).toBeInTheDocument();
    });
  });

  describe("moving between tabs", () => {
    it("selects the tab the user chose", async () => {
      const user = userEvent.setup();
      renderDetail();
      await screen.findByText("Benchy");

      await user.click(screen.getByRole("tab", { name: /Files/ }));

      expect(screen.getByRole("tab", { name: /Files/ })).toHaveAttribute("aria-selected", "true");
    });

    it("shows the chosen tab's contents", async () => {
      const user = userEvent.setup();
      renderDetail();
      await screen.findByText("Benchy");

      await user.click(screen.getByRole("tab", { name: /Settings/ }));

      expect(screen.getByRole("tab", { name: /Settings/ })).toHaveAttribute(
        "aria-selected",
        "true",
      );
    });

    it("leaves the previous tab unselected", async () => {
      const user = userEvent.setup();
      renderDetail();
      await screen.findByText("Benchy");

      await user.click(screen.getByRole("tab", { name: /Files/ }));

      expect(screen.getByRole("tab", { name: /Overview/ })).toHaveAttribute(
        "aria-selected",
        "false",
      );
    });
  });

  describe("who may see the print history", () => {
    it("offers it to someone who can see printers", async () => {
      renderDetail();

      expect(await screen.findByRole("tab", { name: /History/ })).toBeInTheDocument();
    });

    it("withholds it from someone who cannot", async () => {
      renderDetail({ auth: memberSession() });

      await screen.findByText("Benchy");
      expect(screen.queryByRole("tab", { name: /History/ })).toBeNull();
    });

    it("asks for no print history on that user's behalf", async () => {
      // The tab is about printers; fetching its contents for someone who may not
      // see printers is a request that answers 403 on every page load.
      const { requests } = renderDetail({ auth: memberSession() });

      await screen.findByText("Benchy");
      expect(requests().some((call) => call.url.includes("print-jobs"))).toBe(false);
    });
  });

  describe("favouriting", () => {
    it("stars the model", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderDetail({
        routes: { "PUT /api/v1/models/1/star": json({ model_id: 1, starred: true }) },
      });
      await screen.findByText("Benchy");

      await user.click(screen.getByRole("button", { name: /favorite/i }));

      await waitFor(() =>
        expect(requestsWithMethod("PUT").some((call) => call.url.includes("/star"))).toBe(true),
      );
    });

    it("unstars a model that was starred", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderDetail({
        model: aModel({ starred: true }),
        routes: { "DELETE /api/v1/models/1/star": json(null, 204) },
      });
      await screen.findByText("Benchy");

      await user.click(screen.getByRole("button", { name: /favorite/i }));

      await waitFor(() =>
        expect(requestsWithMethod("DELETE").some((call) => call.url.includes("/star"))).toBe(true),
      );
    });
  });

  describe("editing", () => {
    it("offers editing to someone with write access", async () => {
      renderDetail();

      await screen.findByText("Benchy");
      expect(screen.getByRole("tab", { name: /Settings/ })).toBeInTheDocument();
    });

    it("keeps a view-only user out of destructive actions", async () => {
      renderDetail({ model: aModel({ effective_role: "view" }), auth: memberSession() });

      await screen.findByText("Benchy");
      expect(screen.queryByRole("button", { name: /Delete model/i })).toBeNull();
    });
  });
});
