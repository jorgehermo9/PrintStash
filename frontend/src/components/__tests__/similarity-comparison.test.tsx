/*
 * Two Models side by side, each in its own 3D pane.
 *
 * Opening a comparison must not download two meshes the reviewer did not ask
 * for: a pane whose source Artifact is over the automatic budget offers the
 * preview as an action instead, and the pane beside it is unaffected. The
 * measurement table never depends on either mesh.
 */
import "@testing-library/jest-dom/vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { SimilarityComparison } from "@/components/similarity-comparison";
import { MESH_AUTO_LOAD_BYTES } from "@/lib/mesh-load-policy";
import { aModel, aRevision } from "@/test-support/factories";
import { json, renderApp } from "@/test-support/render";
import type { SimilarityCandidate } from "@/types/similarity";
import { aSimilarityCandidate } from "@/test-support/similarity";

const HASH_A = "a".repeat(64);
const HASH_B = "b".repeat(64);

function renderComparison(
  sizeA: number,
  sizeB: number,
  overrides: Partial<SimilarityCandidate> = {},
) {
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
      models={[
        aModel({ id: 1, name: "Left", files: [mesh(11, 1, HASH_A, sizeA)] }),
        aModel({ id: 2, name: "Right", files: [mesh(12, 2, HASH_B, sizeB)] }),
      ]}
    />,
    {
      routes: {
        "GET /api/v1/models/1/print-jobs": json([]),
        "GET /api/v1/models/2/print-jobs": json([]),
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

  it("offers alignment adjustments on demand", async () => {
    const user = userEvent.setup();
    renderComparison(MESH_AUTO_LOAD_BYTES + 1, MESH_AUTO_LOAD_BYTES + 1);
    expect(screen.getByRole("checkbox", { name: "Compensate scale" })).not.toBeVisible();
    await user.click(screen.getByText("Alignment options"));
    const scale = screen.getByRole("checkbox", { name: "Compensate scale" });
    await user.click(scale);
    expect(scale).toBeChecked();
    await user.click(screen.getByRole("checkbox", { name: "Compensate reflection" }));
    expect(screen.getByRole("checkbox", { name: "Compensate reflection" })).toBeChecked();
  });

  it("keeps stale alignment unavailable", async () => {
    const user = userEvent.setup();
    renderComparison(MESH_AUTO_LOAD_BYTES + 1, MESH_AUTO_LOAD_BYTES + 1, { freshness: "stale" });
    await user.click(screen.getByText("Alignment options"));
    expect(screen.getByRole("checkbox", { name: "Compensate scale" })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "Compensate reflection" })).toBeDisabled();
  });

  it("keeps detailed measurements collapsed until requested", async () => {
    const user = userEvent.setup();
    renderComparison(MESH_AUTO_LOAD_BYTES + 1, MESH_AUTO_LOAD_BYTES + 1);
    expect(screen.getByRole("table")).not.toBeVisible();
    expect(screen.getAllByText(/STL/).some((element) => element.closest("details") === null)).toBe(
      true,
    );
    await user.click(screen.getByText("Measurements and print history"));
    expect(screen.getByRole("table")).toBeVisible();
    expect(screen.getByRole("rowheader", { name: "Volume" })).toBeVisible();
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
