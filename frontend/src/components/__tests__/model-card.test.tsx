/*
 * The revision badge on a model card, when the user has renamed the revision.
 *
 * A card can show two different things about the same G-code: its *status*
 * (known-good, needs testing) and the user's own *label* ("0.2mm draft"). Those
 * are independent, and showing only one of them is the failure — a card that
 * displays the custom label alone hides that the revision was never verified,
 * which is exactly the information somebody about to print it needs.
 */

import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { ModelCard } from "@/components/model-card";
import type { ModelListItem } from "@/types";

const model: ModelListItem = {
  id: 1,
  name: "Cam Holder v4",
  slug: "cam-holder-v4",
  collection: null,
  collection_id: null,
  source_url: null,
  effective_role: "admin",
  tags: [],
  thumbnail_url: null,
  file_count: 2,
  mesh_file_id: null,
  printer_presence: [],
  updated_at: "2026-07-13T12:00:00Z",
  print_summary: null,
  recommended_revision_status: "needs_test",
  recommended_revision_label: "a",
  starred: false,
};

describe("ModelCard", () => {
  it("shows revision status alongside a custom revision label", () => {
    // The card links to the model detail route and prefetches it on hover, so
    // it needs a real router; `thumbnail_url: null` keeps the thumbnail hook
    // from touching the network.
    render(
      <MemoryRouter>
        <ModelCard model={model} />
      </MemoryRouter>,
    );

    expect(screen.getByLabelText("Revision status: Needs Test; label: a")).toHaveTextContent(
      "Needs Test·a",
    );
  });
});
