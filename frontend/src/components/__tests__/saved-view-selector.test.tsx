import "@testing-library/jest-dom/vitest";
import { expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SavedViewSelector } from "@/components/saved-view-selector";
import type { SavedViewRead } from "@/types";

function view(id: number, name: string): SavedViewRead {
  return {
    id,
    name,
    filters: { direct: true, tag: [], favorites: false },
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

it("searches a long saved-view list and applies the chosen view", async () => {
  const user = userEvent.setup();
  const onSelect = vi.fn();
  const views = [view(1, "Ready to print"), view(2, "Needs supports"), view(3, "Favorites")];
  render(
    <SavedViewSelector
      views={views}
      activeId={null}
      onSelect={onSelect}
      onCreate={vi.fn()}
      onUpdate={vi.fn()}
      onRename={vi.fn()}
      onDuplicate={vi.fn()}
      onDelete={vi.fn()}
    />,
  );

  await user.click(screen.getByRole("button", { name: /saved views/i }));
  await user.type(screen.getByRole("textbox", { name: /find a saved view/i }), "support");
  expect(screen.queryByText("Ready to print")).not.toBeInTheDocument();
  await user.click(screen.getByText("Needs supports"));

  expect(onSelect).toHaveBeenCalledWith(views[1]);
});

it("updates and renames saved views from the selector", async () => {
  const user = userEvent.setup();
  const saved = view(1, "Workshop");
  const onUpdate = vi.fn().mockResolvedValue(undefined);
  const onRename = vi.fn().mockResolvedValue(undefined);
  render(
    <SavedViewSelector
      views={[saved]}
      activeId={1}
      onSelect={vi.fn()}
      onCreate={vi.fn()}
      onUpdate={onUpdate}
      onRename={onRename}
      onDuplicate={vi.fn()}
      onDelete={vi.fn()}
    />,
  );

  await user.click(screen.getByRole("button", { name: /workshop/i }));
  await user.click(screen.getByRole("button", { name: "Update Workshop" }));
  expect(onUpdate).toHaveBeenCalledWith(saved);
  await user.click(screen.getByRole("button", { name: "Rename Workshop" }));
  const input = screen.getByDisplayValue("Workshop");
  await user.clear(input);
  await user.type(input, "Daily prints");
  await user.click(
    screen
      .getByRole("dialog", { name: /rename saved view/i })
      .querySelector('button[type="submit"]')!,
  );
  expect(onRename).toHaveBeenCalledWith(saved, "Daily prints");
});

it("starts saving current filters from the selector", async () => {
  const user = userEvent.setup();
  const onCreate = vi.fn();
  render(
    <SavedViewSelector
      views={[]}
      activeId={null}
      onSelect={vi.fn()}
      onCreate={onCreate}
      onUpdate={vi.fn()}
      onRename={vi.fn()}
      onDuplicate={vi.fn()}
      onDelete={vi.fn()}
    />,
  );
  await user.click(screen.getByRole("button", { name: /saved views/i }));
  await user.click(screen.getByRole("button", { name: /save current view/i }));
  expect(onCreate).toHaveBeenCalledTimes(1);
});

it("marks an active saved view when current filters have changed", () => {
  const saved = view(1, "Workshop");
  render(
    <SavedViewSelector
      views={[saved]}
      activeId={1}
      modified
      onSelect={vi.fn()}
      onCreate={vi.fn()}
      onUpdate={vi.fn()}
      onRename={vi.fn()}
      onDuplicate={vi.fn()}
      onDelete={vi.fn()}
    />,
  );
  expect(screen.getByLabelText("Modified saved view")).toBeInTheDocument();
});
