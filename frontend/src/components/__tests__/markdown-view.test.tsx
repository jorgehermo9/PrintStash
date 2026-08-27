/**
 * MarkdownView is the shared renderer for document previews and collection
 * READMEs. These tests protect GFM table semantics while keeping the existing
 * link customization and sanitization boundary intact.
 */
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MarkdownView } from "@/components/markdown-view";

describe("MarkdownView", () => {
  it("renders a standard GFM pipe table with semantic headers and cells", () => {
    render(
      <MarkdownView
        source={`| Part | Material |
| --- | --- |
| A | PLA |
| B | PETG |`}
      />,
    );

    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getAllByRole("columnheader")).toHaveLength(2);
    expect(screen.getByRole("columnheader", { name: "Part" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Material" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "A" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "PLA" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "B" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "PETG" })).toBeInTheDocument();
  });

  it("keeps ordinary markdown links on the secure custom renderer", () => {
    render(<MarkdownView source="Read the [assembly guide](https://example.test/guide)." />);

    expect(screen.getByRole("link", { name: "assembly guide" })).toHaveAttribute(
      "href",
      "https://example.test/guide",
    );
    expect(screen.getByRole("link", { name: "assembly guide" })).toHaveAttribute(
      "target",
      "_blank",
    );
    expect(screen.getByRole("link", { name: "assembly guide" })).toHaveAttribute(
      "rel",
      "noopener noreferrer nofollow",
    );
  });

  it("sanitizes raw HTML and script content", () => {
    render(<MarkdownView source={'<script>alert("unsafe")</script>\n\nSafe text'} />);

    expect(document.querySelector("script")).not.toBeInTheDocument();
    expect(screen.getByText("Safe text")).toBeInTheDocument();
  });
});
