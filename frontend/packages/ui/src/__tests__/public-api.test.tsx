import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Button, ConfirmModal, Modal, cn } from "../index";

describe("@printstash/ui public API", () => {
  it("exports class merging and the shared button primitive", () => {
    render(<Button className="px-8">Save</Button>);

    expect(screen.getByRole("button", { name: "Save" })).toHaveClass("px-8");
    expect(cn("px-2", "px-4")).toBe("px-4");
  });

  it("uses an injected modal close label", () => {
    render(
      <Modal open onClose={() => {}} closeLabel="Dismiss dialog" title="Edit">
        Contents
      </Modal>,
    );

    expect(
      screen.getByRole("button", { name: "Dismiss dialog" }),
    ).toBeInTheDocument();
  });

  it("uses injected confirmation labels and preserves actions", () => {
    const onClose = vi.fn();
    const onConfirm = vi.fn();
    render(
      <ConfirmModal
        open
        onClose={onClose}
        onConfirm={onConfirm}
        title="Remove model?"
        description="This cannot be undone."
        closeLabel="Dismiss dialog"
        cancelLabel="Keep model"
        confirmLabel="Remove model"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Keep model" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove model" }));

    expect(onClose).toHaveBeenCalledOnce();
    expect(onConfirm).toHaveBeenCalledOnce();
  });
});
