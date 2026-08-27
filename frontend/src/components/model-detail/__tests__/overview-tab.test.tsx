/*
 * The legacy source link on the overview tab, and the scheme check on it.
 *
 * `source_url` on a Model is the pre-provenance field: a URL a user pasted or an
 * older release scraped, stored with no validation at all. Rendering it as an
 * anchor without checking the scheme is stored XSS — a `javascript:` URL that
 * fires when somebody clicks through to where their model came from.
 *
 * Safe URLs are normalized rather than passed through, so the two cases here are
 * the whole contract: `http`/`https` become links, everything else becomes text.
 */

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

describe("OverviewTab", () => {
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
