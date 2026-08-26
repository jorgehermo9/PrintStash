import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { OverviewTab, type ModelMetaEditor } from "@/components/model-detail/overview-tab";
import type { ModelRead } from "@/types";

const editor: ModelMetaEditor = {
  collection: "",
  setCollection: () => {},
  catOpen: false,
  setCatOpen: () => {},
  collections: [],
  description: "",
  setDescription: () => {},
  sourceUrl: "",
  setSourceUrl: () => {},
  tagInput: "",
  setTagInput: () => {},
  tags: [],
  setTags: () => {},
  toggleTag: () => {},
  createTag: () => {},
  deleteTag: () => {},
  filteredTags: [],
  canCreate: false,
};

const model: ModelRead = {
  id: 1,
  name: "Calibration cube",
  slug: "calibration-cube",
  hash: "hash",
  collection: null,
  collection_id: null,
  description: null,
  source_url: null,
  effective_role: "admin",
  tags: [],
  thumbnail_url: null,
  created_at: "2026-08-24T00:00:00Z",
  updated_at: "2026-08-24T00:00:00Z",
  files: [],
  starred: false,
};

function renderOverview(sourceUrl: string) {
  return render(
    <OverviewTab
      model={{ ...model, source_url: sourceUrl }}
      editing={false}
      editor={editor}
      recommendedFile={null}
      hasGcode={false}
      revisionSaving={null}
      onSend={() => {}}
      canSend={false}
      onCompare={() => {}}
      onMark={() => {}}
      onAddRevision={() => {}}
    />,
  );
}

describe("OverviewTab legacy source link", () => {
  it("renders a normalized safe HTTP(S) source URL as a link", () => {
    renderOverview("HTTPS://EXAMPLE.TEST/cube");

    expect(screen.getByRole("link", { name: "Source model" })).toHaveAttribute(
      "href",
      "https://example.test/cube",
    );
  });

  it("does not render unsafe source URLs as links", () => {
    renderOverview("https://user:secret@example.test/cube");

    expect(screen.queryByRole("link", { name: "Source model" })).not.toBeInTheDocument();
  });
});
