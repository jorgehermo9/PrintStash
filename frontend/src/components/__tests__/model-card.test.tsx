import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ModelCard } from "@/components/model-card";
import type { ModelListItem } from "@/types";

vi.mock("@/lib/navigation", () => ({
  Link: ({ children, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a {...props}>{children}</a>
  ),
  useRouter: () => ({ prefetch: vi.fn() }),
}));

vi.mock("@/lib/use-authenticated-asset-url", () => ({
  useAuthenticatedAssetUrl: () => null,
}));

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

describe("model card revision badge", () => {
  it("shows revision status alongside a custom revision label", () => {
    render(<ModelCard model={model} />);

    expect(screen.getByLabelText("Revision status: Needs Test; label: a")).toHaveTextContent(
      "Needs Test·a",
    );
  });
});
