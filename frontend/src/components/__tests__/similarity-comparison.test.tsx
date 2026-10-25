/*
 * Two Models side by side, each in its own 3D pane.
 *
 * Opening a comparison must not download two meshes the reviewer did not ask
 * for: a pane whose source Artifact is over the automatic budget offers the
 * preview as an action instead, and the pane beside it is unaffected. The
 * measurement table never depends on either mesh.
 */
import "@testing-library/jest-dom/vitest";
import { screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { SimilarityComparison } from "@/components/similarity-comparison";
import { MESH_AUTO_LOAD_BYTES } from "@/lib/mesh-load-policy";
import { aModel, aRevision } from "@/test-support/factories";
import { json, renderApp, type RenderAppOptions } from "@/test-support/render";
import type { SimilarityCandidate } from "@/types/similarity";
import { aSimilarityCandidate } from "@/test-support/similarity";

const HASH_A = "a".repeat(64);
const HASH_B = "b".repeat(64);

function renderComparison(
  sizeA: number,
  sizeB: number,
  overrides: Partial<SimilarityCandidate> = {},
  options: RenderAppOptions & { evidence?: ReactNode } = {},
) {
  const { evidence, ...renderOptions } = options;
  const base = aSimilarityCandidate({
    summary: {
      transform: [
        [1, 0, 0, 0],
        [0, 1, 0, 0],
        [0, 0, 1, 0],
        [0, 0, 0, 1],
      ],
    },
    ...overrides,
  });
  const candidate = {
    ...base,
    primary_lineage_key: "whole",
    observations: [
      {
        lineage_key: "whole",
        id: 1,
        fingerprint_a_id: null,
        fingerprint_b_id: null,
        input_hash_a: HASH_A,
        input_hash_b: HASH_B,
        kind: "whole",
        multiplicity_a: 1,
        multiplicity_b: 1,
        evidence: base.summary,
      },
    ],
  };
  const mesh = (id: number, model_id: number, sha256: string, size_bytes: number) =>
    aRevision({
      id,
      model_id,
      file_type: "stl",
      original_filename: `${id}.stl`,
      sha256,
      size_bytes,
    });
  return renderApp(
    <SimilarityComparison
      candidate={candidate}
      evidence={evidence}
      models={[
        aModel({ id: 1, name: "Left", files: [mesh(11, 1, HASH_A, sizeA)] }),
        aModel({ id: 2, name: "Right", files: [mesh(12, 2, HASH_B, sizeB)] }),
      ]}
    />,
    {
      ...renderOptions,
      routes: {
        "GET /api/v1/models/1/print-jobs": json([]),
        "GET /api/v1/models/2/print-jobs": json([]),
        ...renderOptions.routes,
      },
    },
  );
}

describe("SimilarityComparison", () => {
  it("changes the preview layout with the selected view", async () => {
    const user = userEvent.setup();
    const view = renderComparison(MESH_AUTO_LOAD_BYTES + 1, MESH_AUTO_LOAD_BYTES + 1);
    expect(screen.getByRole("button", { name: "Side by side" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await user.click(screen.getByRole("button", { name: "Overlay meshes" }));
    expect(screen.getByRole("button", { name: "Overlay meshes" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getAllByRole("button", { name: "Load 3D preview" })).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "Side by side" }));
    expect(screen.getAllByRole("button", { name: "Load 3D preview" })).toHaveLength(2);
    expect(view.requests().filter((request) => request.url.endsWith("/stl"))).toEqual([]);
  });

  it("disables overlay without both comparison files", () => {
    renderApp(
      <SimilarityComparison candidate={aSimilarityCandidate()} models={[undefined, undefined]} />,
    );
    expect(screen.getByRole("button", { name: "Overlay meshes" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Side by side" })).toBeEnabled();
  });

  it("offers alignment adjustments directly beside the view controls", async () => {
    const user = userEvent.setup();
    renderComparison(MESH_AUTO_LOAD_BYTES + 1, MESH_AUTO_LOAD_BYTES + 1);
    expect(screen.getByRole("checkbox", { name: "Compensate scale" })).toBeVisible();
    expect(screen.getByRole("checkbox", { name: "Compensate reflection" })).toBeVisible();
    const scale = screen.getByRole("checkbox", { name: "Compensate scale" });
    await user.click(scale);
    expect(scale).toBeChecked();
    await user.click(screen.getByRole("checkbox", { name: "Compensate reflection" }));
    expect(screen.getByRole("checkbox", { name: "Compensate reflection" })).toBeChecked();
  });

  it("keeps stale alignment unavailable", () => {
    renderComparison(MESH_AUTO_LOAD_BYTES + 1, MESH_AUTO_LOAD_BYTES + 1, { freshness: "stale" });
    expect(screen.getByRole("checkbox", { name: "Compensate scale" })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "Compensate reflection" })).toBeDisabled();
  });

  it("shows every comparison measurement before loading previews", () => {
    renderComparison(MESH_AUTO_LOAD_BYTES + 1, MESH_AUTO_LOAD_BYTES + 1);
    const table = screen.getByRole("table", { name: "Measurements and print history" });
    expect(table).toBeVisible();
    expect(
      within(table)
        .getAllByRole("rowheader")
        .map((cell) => cell.textContent),
    ).toEqual([
      "Original dimensions",
      "Volume",
      "Surface area",
      "Triangles",
      "Watertight",
      "Format",
      "Size",
      "Source",
      "Revisions",
      "Has a known-good Revision",
      "Last print",
    ]);
    expect(within(table).getByRole("columnheader", { name: /A.*Bracket$/ })).toBeVisible();
    expect(within(table).getByRole("columnheader", { name: /B.*Bracket copy$/ })).toBeVisible();
  });

  it("keeps match evidence visible with the measurement table", () => {
    renderComparison(
      MESH_AUTO_LOAD_BYTES + 1,
      MESH_AUTO_LOAD_BYTES + 1,
      {},
      {
        evidence: <p>Verified from the original source files</p>,
      },
    );

    expect(screen.getByText("Verified from the original source files")).toBeVisible();
    expect(screen.getByRole("table", { name: "Measurements and print history" })).toBeVisible();
  });

  it("keeps measurements readable without comparison files", () => {
    renderApp(
      <SimilarityComparison candidate={aSimilarityCandidate()} models={[undefined, undefined]} />,
    );

    expect(screen.getByRole("table", { name: "Measurements and print history" })).toBeVisible();
    expect(screen.getAllByRole("rowheader")).toHaveLength(11);
    expect(screen.getAllByText("3D preview unavailable").length).toBeGreaterThan(0);
  });

  it("retries failed print history from the information area", async () => {
    const app = renderComparison(
      MESH_AUTO_LOAD_BYTES + 1,
      MESH_AUTO_LOAD_BYTES + 1,
      {},
      {
        routes: { "GET /api/v1/models/1/print-jobs": json({ detail: "unavailable" }, 503) },
      },
    );
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("A · Could not load similarity results");
    expect(screen.getByRole("table", { name: "Measurements and print history" })).toBeVisible();
    app.route({ "GET /api/v1/models/1/print-jobs": json([]) });
    await userEvent.click(within(alert).getByRole("button", { name: "Try again" }));

    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(
      app.requests().filter((request) => request.url.endsWith("/models/1/print-jobs")),
    ).toHaveLength(2);
  });

  describe("a pane over the mesh budget", () => {
    it("offers its preview as an action", () => {
      renderComparison(MESH_AUTO_LOAD_BYTES + 1, MESH_AUTO_LOAD_BYTES + 1);

      expect(screen.getAllByRole("button", { name: "Load 3D preview" })).toHaveLength(2);
    });

    it("leaves the pane beside it alone", () => {
      renderComparison(MESH_AUTO_LOAD_BYTES + 1, 1024);

      expect(screen.getAllByRole("button", { name: "Load 3D preview" })).toHaveLength(1);
    });

    it("requests no mesh", () => {
      const view = renderComparison(MESH_AUTO_LOAD_BYTES + 1, MESH_AUTO_LOAD_BYTES + 1);

      expect(view.requests().filter((request) => request.url.endsWith("/stl"))).toEqual([]);
    });
  });
});
