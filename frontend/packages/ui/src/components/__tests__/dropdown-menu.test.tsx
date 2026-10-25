/*
 * The anchored panel behind every menu in the product — the model card's actions,
 * the printer picker, the account menu.
 *
 * It is a floating overlay built by hand, so it owns three contracts no rendered
 * snapshot would check. Outside dismiss has to distinguish a click on the page from
 * a click on the panel's own contents, or the menu closes the instant you try to use
 * it. Focus has to enter the panel on open and come back to the trigger on Escape,
 * or a keyboard user is dropped at the top of the document. And the roving arrow keys
 * are the only way to reach the items at all, since none of them is a tab stop.
 *
 * `role` is what varies: a `menu`/`listbox` manages focus for its items, a `dialog`
 * deliberately does not — its content owns its own focus. Both paths are exercised
 * here because the difference is a silent one-line branch.
 */

import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DURATION } from "../../lib/overlay";
import { DropdownMenu } from "../dropdown-menu";
import { Modal } from "../modal";

const TRIGGER = (
  <button type="button" data-menu-trigger>
    Actions
  </button>
);

function MenuItems() {
  return (
    <>
      <button type="button" role="menuitem">
        Rename
      </button>
      <button type="button" role="menuitem">
        Duplicate
      </button>
      <button type="button" role="menuitem">
        Delete
      </button>
    </>
  );
}

function PermissionLimitedItems() {
  return (
    <>
      <button type="button" role="menuitem" disabled>
        Rename
      </button>
      <button type="button" role="menuitem">
        Similar models
      </button>
      <button type="button" role="menuitem" disabled>
        New collection
      </button>
      <button type="button" role="menuitem" aria-disabled="true">
        Pending imports
      </button>
      <button type="button" role="menuitemcheckbox" aria-checked="false">
        Select
      </button>
      <button type="button" role="menuitem" aria-disabled="true">
        Delete
      </button>
    </>
  );
}

/** Open the panel and let the entrance frames run, as a browser would. */
function openMenu(ui: React.ReactElement) {
  const result = render(ui);
  act(() => {
    vi.advanceTimersToNextFrame();
    vi.advanceTimersToNextFrame();
  });
  return result;
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("DropdownMenu", () => {
  describe("mounting", () => {
    it("renders the trigger while the menu is closed", () => {
      render(
        <DropdownMenu
          open={false}
          onOpenChange={vi.fn<(open: boolean) => void>()}
          trigger={TRIGGER}
        >
          <MenuItems />
        </DropdownMenu>,
      );

      expect(screen.getByRole("button", { name: "Actions" })).toBeInTheDocument();
      expect(screen.queryByRole("menu")).toBeNull();
    });

    it("renders its children under the requested role when open", () => {
      openMenu(
        <DropdownMenu
          open={true}
          onOpenChange={vi.fn<(open: boolean) => void>()}
          role="listbox"
          trigger={TRIGGER}
        >
          <button type="button" role="option">
            Only
          </button>
        </DropdownMenu>,
      );

      expect(screen.getByRole("listbox")).toContainElement(
        screen.getByRole("option", { name: "Only" }),
      );
    });

    it("exposes the provided accessible dialog name", () => {
      openMenu(
        <DropdownMenu
          open
          onOpenChange={vi.fn<(open: boolean) => void>()}
          trigger={TRIGGER}
          role="dialog"
          aria-label="Tags"
        >
          <input aria-label="Search tags" />
        </DropdownMenu>,
      );

      expect(screen.getByRole("dialog", { name: "Tags" })).toContainElement(
        screen.getByRole("textbox", { name: "Search tags" }),
      );
    });

    it("reaches its open state once the entrance frames have run", () => {
      openMenu(
        <DropdownMenu open={true} onOpenChange={vi.fn<(open: boolean) => void>()} trigger={TRIGGER}>
          <MenuItems />
        </DropdownMenu>,
      );

      expect(screen.getByRole("menu")).toHaveAttribute("data-state", "open");
    });

    it("keeps the panel mounted while it animates out", () => {
      const { rerender } = openMenu(
        <DropdownMenu open={true} onOpenChange={vi.fn<(open: boolean) => void>()} trigger={TRIGGER}>
          <MenuItems />
        </DropdownMenu>,
      );

      rerender(
        <DropdownMenu
          open={false}
          onOpenChange={vi.fn<(open: boolean) => void>()}
          trigger={TRIGGER}
        >
          <MenuItems />
        </DropdownMenu>,
      );

      expect(screen.getByRole("menu")).toHaveAttribute("data-state", "closed");
    });

    it("removes the panel once the exit transition ends", () => {
      const { rerender } = openMenu(
        <DropdownMenu open={true} onOpenChange={vi.fn<(open: boolean) => void>()} trigger={TRIGGER}>
          <MenuItems />
        </DropdownMenu>,
      );

      rerender(
        <DropdownMenu
          open={false}
          onOpenChange={vi.fn<(open: boolean) => void>()}
          trigger={TRIGGER}
        >
          <MenuItems />
        </DropdownMenu>,
      );
      act(() => {
        vi.advanceTimersByTime(DURATION.press);
      });

      expect(screen.queryByRole("menu")).toBeNull();
    });

    it("anchors to the trigger's right edge by default", () => {
      openMenu(
        <DropdownMenu open={true} onOpenChange={vi.fn<(open: boolean) => void>()} trigger={TRIGGER}>
          <MenuItems />
        </DropdownMenu>,
      );

      expect(screen.getByRole("menu")).toHaveClass("right-0", "origin-top-right");
    });

    it("anchors to the trigger's left edge when asked", () => {
      openMenu(
        <DropdownMenu
          open={true}
          onOpenChange={vi.fn<(open: boolean) => void>()}
          align="start"
          trigger={TRIGGER}
        >
          <MenuItems />
        </DropdownMenu>,
      );

      expect(screen.getByRole("menu")).toHaveClass("left-0", "origin-top-left");
    });
  });

  describe("dismissal", () => {
    it("closes when a pointer goes down outside it", () => {
      const onOpenChange = vi.fn<(open: boolean) => void>();
      openMenu(
        <DropdownMenu open={true} onOpenChange={onOpenChange} trigger={TRIGGER}>
          <MenuItems />
        </DropdownMenu>,
      );

      fireEvent.pointerDown(document.body);

      expect(onOpenChange).toHaveBeenCalledWith(false);
    });

    it("stays open when a pointer goes down on its own contents", () => {
      const onOpenChange = vi.fn<(open: boolean) => void>();
      openMenu(
        <DropdownMenu open={true} onOpenChange={onOpenChange} trigger={TRIGGER}>
          <MenuItems />
        </DropdownMenu>,
      );

      fireEvent.pointerDown(screen.getByRole("menuitem", { name: "Rename" }));

      expect(onOpenChange).not.toHaveBeenCalled();
    });

    it("ignores outside pointers while it is closed", () => {
      const onOpenChange = vi.fn<(open: boolean) => void>();
      render(
        <DropdownMenu open={false} onOpenChange={onOpenChange} trigger={TRIGGER}>
          <MenuItems />
        </DropdownMenu>,
      );

      fireEvent.pointerDown(document.body);

      expect(onOpenChange).not.toHaveBeenCalled();
    });

    it("closes on Escape", () => {
      const onOpenChange = vi.fn<(open: boolean) => void>();
      openMenu(
        <DropdownMenu open={true} onOpenChange={onOpenChange} trigger={TRIGGER}>
          <MenuItems />
        </DropdownMenu>,
      );

      fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });

      expect(onOpenChange).toHaveBeenCalledWith(false);
    });

    it("returns focus to the trigger on Escape", () => {
      openMenu(
        <DropdownMenu open={true} onOpenChange={vi.fn<(open: boolean) => void>()} trigger={TRIGGER}>
          <MenuItems />
        </DropdownMenu>,
      );

      fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });

      expect(screen.getByRole("button", { name: "Actions" })).toHaveFocus();
    });
  });

  describe("keyboard navigation", () => {
    it("focuses the first item when it opens", () => {
      openMenu(
        <DropdownMenu open={true} onOpenChange={vi.fn<(open: boolean) => void>()} trigger={TRIGGER}>
          <MenuItems />
        </DropdownMenu>,
      );

      expect(screen.getByRole("menuitem", { name: "Rename" })).toHaveFocus();
    });

    it("focuses the first enabled item when disabled actions precede it", () => {
      openMenu(
        <DropdownMenu open={true} onOpenChange={vi.fn<(open: boolean) => void>()} trigger={TRIGGER}>
          <PermissionLimitedItems />
        </DropdownMenu>,
      );

      expect(screen.getByRole("menuitem", { name: "Similar models" })).toHaveFocus();
    });

    it.each([
      {
        label: "ArrowDown",
        key: "ArrowDown",
        from: "Similar models",
        fromRole: "menuitem",
        to: "Select",
        toRole: "menuitemcheckbox",
      },
      {
        label: "ArrowUp",
        key: "ArrowUp",
        from: "Select",
        fromRole: "menuitemcheckbox",
        to: "Similar models",
        toRole: "menuitem",
      },
      {
        label: "Home",
        key: "Home",
        from: "Select",
        fromRole: "menuitemcheckbox",
        to: "Similar models",
        toRole: "menuitem",
      },
      {
        label: "End",
        key: "End",
        from: "Similar models",
        fromRole: "menuitem",
        to: "Select",
        toRole: "menuitemcheckbox",
      },
      {
        label: "ArrowDown wrapping",
        key: "ArrowDown",
        from: "Select",
        fromRole: "menuitemcheckbox",
        to: "Similar models",
        toRole: "menuitem",
      },
      {
        label: "ArrowUp wrapping",
        key: "ArrowUp",
        from: "Similar models",
        fromRole: "menuitem",
        to: "Select",
        toRole: "menuitemcheckbox",
      },
    ])("skips disabled actions with $label", ({ key, from, fromRole, to, toRole }) => {
      openMenu(
        <DropdownMenu open={true} onOpenChange={vi.fn<(open: boolean) => void>()} trigger={TRIGGER}>
          <PermissionLimitedItems />
        </DropdownMenu>,
      );
      screen.getByRole(fromRole, { name: from }).focus();

      fireEvent.keyDown(screen.getByRole("menu"), { key });

      expect(screen.getByRole(toRole, { name: to })).toHaveFocus();
    });

    it("leaves focus on the trigger when every action is disabled", () => {
      openMenu(
        <DropdownMenu open={true} onOpenChange={vi.fn<(open: boolean) => void>()} trigger={TRIGGER}>
          <button type="button" role="menuitem" disabled>
            Rename
          </button>
          <button type="button" role="menuitem" aria-disabled="true">
            Delete
          </button>
        </DropdownMenu>,
      );
      screen.getByRole("button", { name: "Actions" }).focus();

      const notCancelled = fireEvent.keyDown(screen.getByRole("button", { name: "Actions" }), {
        key: "ArrowDown",
      });

      expect(screen.getByRole("button", { name: "Actions" })).toHaveFocus();
      expect(notCancelled).toBe(true);
    });

    it("leaves focus alone for a dialog panel", () => {
      openMenu(
        <DropdownMenu
          open={true}
          onOpenChange={vi.fn<(open: boolean) => void>()}
          role="dialog"
          trigger={TRIGGER}
        >
          <button type="button" role="menuitem">
            Rename
          </button>
        </DropdownMenu>,
      );

      expect(screen.getByRole("menuitem", { name: "Rename" })).not.toHaveFocus();
    });

    it("moves to the next item on ArrowDown", () => {
      openMenu(
        <DropdownMenu open={true} onOpenChange={vi.fn<(open: boolean) => void>()} trigger={TRIGGER}>
          <MenuItems />
        </DropdownMenu>,
      );

      fireEvent.keyDown(screen.getByRole("menu"), { key: "ArrowDown" });

      expect(screen.getByRole("menuitem", { name: "Duplicate" })).toHaveFocus();
    });

    it("wraps past the last item to the first", () => {
      openMenu(
        <DropdownMenu open={true} onOpenChange={vi.fn<(open: boolean) => void>()} trigger={TRIGGER}>
          <MenuItems />
        </DropdownMenu>,
      );
      screen.getByRole("menuitem", { name: "Delete" }).focus();

      fireEvent.keyDown(screen.getByRole("menu"), { key: "ArrowDown" });

      expect(screen.getByRole("menuitem", { name: "Rename" })).toHaveFocus();
    });

    it("moves to the previous item on ArrowUp", () => {
      openMenu(
        <DropdownMenu open={true} onOpenChange={vi.fn<(open: boolean) => void>()} trigger={TRIGGER}>
          <MenuItems />
        </DropdownMenu>,
      );
      screen.getByRole("menuitem", { name: "Duplicate" }).focus();

      fireEvent.keyDown(screen.getByRole("menu"), { key: "ArrowUp" });

      expect(screen.getByRole("menuitem", { name: "Rename" })).toHaveFocus();
    });

    it("wraps before the first item to the last", () => {
      openMenu(
        <DropdownMenu open={true} onOpenChange={vi.fn<(open: boolean) => void>()} trigger={TRIGGER}>
          <MenuItems />
        </DropdownMenu>,
      );

      fireEvent.keyDown(screen.getByRole("menu"), { key: "ArrowUp" });

      expect(screen.getByRole("menuitem", { name: "Delete" })).toHaveFocus();
    });

    it("jumps to the first item on Home", () => {
      openMenu(
        <DropdownMenu open={true} onOpenChange={vi.fn<(open: boolean) => void>()} trigger={TRIGGER}>
          <MenuItems />
        </DropdownMenu>,
      );
      screen.getByRole("menuitem", { name: "Delete" }).focus();

      fireEvent.keyDown(screen.getByRole("menu"), { key: "Home" });

      expect(screen.getByRole("menuitem", { name: "Rename" })).toHaveFocus();
    });

    it("jumps to the last item on End", () => {
      openMenu(
        <DropdownMenu open={true} onOpenChange={vi.fn<(open: boolean) => void>()} trigger={TRIGGER}>
          <MenuItems />
        </DropdownMenu>,
      );

      fireEvent.keyDown(screen.getByRole("menu"), { key: "End" });

      expect(screen.getByRole("menuitem", { name: "Delete" })).toHaveFocus();
    });

    it("keeps an arrow key from scrolling the page", () => {
      openMenu(
        <DropdownMenu open={true} onOpenChange={vi.fn<(open: boolean) => void>()} trigger={TRIGGER}>
          <MenuItems />
        </DropdownMenu>,
      );

      const notCancelled = fireEvent.keyDown(screen.getByRole("menu"), { key: "ArrowDown" });

      expect(notCancelled).toBe(false);
    });

    it("leaves arrow keys to a dialog panel's own content", () => {
      openMenu(
        <DropdownMenu
          open={true}
          onOpenChange={vi.fn<(open: boolean) => void>()}
          role="dialog"
          trigger={TRIGGER}
        >
          <MenuItems />
        </DropdownMenu>,
      );

      const notCancelled = fireEvent.keyDown(screen.getByRole("dialog"), { key: "ArrowDown" });

      expect(notCancelled).toBe(true);
    });

    it("leaves arrow keys alone when the menu has no items", () => {
      openMenu(
        <DropdownMenu open={true} onOpenChange={vi.fn<(open: boolean) => void>()} trigger={TRIGGER}>
          <p>Nothing here yet.</p>
        </DropdownMenu>,
      );

      const notCancelled = fireEvent.keyDown(screen.getByRole("menu"), { key: "ArrowDown" });

      expect(notCancelled).toBe(true);
    });

    it("leaves keys it does not own to the browser", () => {
      const onOpenChange = vi.fn<(open: boolean) => void>();
      openMenu(
        <DropdownMenu open={true} onOpenChange={onOpenChange} trigger={TRIGGER}>
          <MenuItems />
        </DropdownMenu>,
      );

      const notCancelled = fireEvent.keyDown(screen.getByRole("menu"), { key: "a" });

      expect(notCancelled).toBe(true);
      expect(onOpenChange).not.toHaveBeenCalled();
    });
  });
  describe.each(["menuitemcheckbox", "menuitemradio"] as const)("a %s item", (role) => {
    it("takes focus when the menu opens", () => {
      // A menu whose only items are toggles would otherwise open with focus
      // nowhere, and a keyboard user has nothing to arrow from.
      openMenu(
        <DropdownMenu open={true} onOpenChange={vi.fn<(open: boolean) => void>()} trigger={TRIGGER}>
          <button type="button" role={role} aria-checked="false">
            Favorites
          </button>
        </DropdownMenu>,
      );

      expect(screen.getByRole(role, { name: "Favorites" })).toHaveFocus();
    });

    it("joins the arrow-key rotation", () => {
      openMenu(
        <DropdownMenu open={true} onOpenChange={vi.fn<(open: boolean) => void>()} trigger={TRIGGER}>
          <button type="button" role="menuitem">
            Rename
          </button>
          <button type="button" role={role} aria-checked="false">
            Favorites
          </button>
        </DropdownMenu>,
      );

      fireEvent.keyDown(screen.getByRole("menu"), { key: "ArrowDown" });

      expect(screen.getByRole(role, { name: "Favorites" })).toHaveFocus();
    });
  });

  describe("a dialog nested inside the panel", () => {
    /** A dialog-role panel holding a real dialog, as the saved-views picker does. */
    function withNestedDialog() {
      return (
        <DropdownMenu
          open={true}
          onOpenChange={vi.fn<(open: boolean) => void>()}
          role="dialog"
          trigger={TRIGGER}
        >
          <button type="button" role="menuitem">
            Saved views
          </button>
          <div role="dialog">
            <input aria-label="Find a saved view" />
          </div>
        </DropdownMenu>
      );
    }

    it("lets the nested dialog keep its own arrow keys", () => {
      // The picker types and navigates inside itself. If the panel also handled
      // the arrows it would move a roving focus behind the dialog, which the
      // user cannot see and did not ask for.
      openMenu(withNestedDialog());

      const stopped = !fireEvent.keyDown(screen.getByLabelText("Find a saved view"), {
        key: "ArrowDown",
      });

      expect(stopped).toBe(false);
    });

    it("leaves focus in the nested dialog", () => {
      openMenu(withNestedDialog());
      const search = screen.getByLabelText("Find a saved view");
      search.focus();

      fireEvent.keyDown(search, { key: "ArrowDown" });

      expect(search).toHaveFocus();
    });

    it("still closes the whole panel on Escape", () => {
      // Escape is the one key the panel keeps: a nested dialog that swallowed it
      // would leave the user with two layers and no way out of either.
      const onOpenChange = vi.fn<(open: boolean) => void>();
      openMenu(
        <DropdownMenu open={true} onOpenChange={onOpenChange} role="dialog" trigger={TRIGGER}>
          <div role="dialog">
            <input aria-label="Find a saved view" />
          </div>
        </DropdownMenu>,
      );

      fireEvent.keyDown(screen.getByLabelText("Find a saved view"), { key: "Escape" });

      expect(onOpenChange).toHaveBeenCalledWith(false);
    });

    it("ignores arrows aimed at a dialog panel with no dialog in it", () => {
      // `role="dialog"` on the panel is a hint about its contents, not a promise
      // that a `[role=dialog]` element exists — a panel of plain form fields is
      // the common case, and it must not crash reading one.
      openMenu(
        <DropdownMenu
          open={true}
          onOpenChange={vi.fn<(open: boolean) => void>()}
          role="dialog"
          trigger={TRIGGER}
        >
          <input aria-label="View name" />
        </DropdownMenu>,
      );

      const notCancelled = fireEvent.keyDown(screen.getByLabelText("View name"), {
        key: "ArrowDown",
      });

      expect(notCancelled).toBe(true);
    });
  });
});

describe("DropdownMenu portals", () => {
  function Picker() {
    const [open, setOpen] = useState(true);
    const [chosen, setChosen] = useState(false);
    return (
      <>
        <DropdownMenu
          portal
          open={open}
          onOpenChange={setOpen}
          role="dialog"
          trigger={
            <button data-menu-trigger aria-expanded={open} onClick={() => setOpen(!open)}>
              Choose
            </button>
          }
        >
          <input aria-label="Find choice" />
          <button onClick={() => setChosen(true)}>
            {chosen ? "Selected choice" : "Select choice"}
          </button>
        </DropdownMenu>
        <button>Following control</button>
      </>
    );
  }

  it.each([
    { label: "display none", style: "display: none" },
    { label: "visibility hidden", style: "visibility: hidden" },
  ])("dismisses a portaled picker when its ancestor becomes $label on resize", ({ style }) => {
    const { container } = openMenu(<Picker />);
    container.setAttribute("style", style);

    fireEvent(window, new Event("resize"));
    act(() => vi.advanceTimersByTime(DURATION.press));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("keeps a zero-sized visible anchor open on resize", () => {
    openMenu(<Picker />);

    fireEvent(window, new Event("resize"));
    act(() => vi.advanceTimersByTime(DURATION.press));

    expect(screen.getByRole("dialog")).toBeVisible();
    expect(screen.getByRole("button", { name: "Choose" })).toHaveAttribute("aria-expanded", "true");
  });

  it("keeps a portaled choice open during pointer selection", () => {
    openMenu(<Picker />);
    const choice = screen.getByRole("button", { name: "Select choice" });
    fireEvent.pointerDown(choice);
    fireEvent.click(choice);
    expect(screen.getByRole("button", { name: "Selected choice" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Choose" })).toHaveAttribute("aria-expanded", "true");
  });

  it("dismisses a portaled picker after an outside pointer", () => {
    openMenu(<Picker />);
    fireEvent.pointerDown(screen.getByRole("button", { name: "Following control" }));
    expect(screen.getByRole("button", { name: "Choose" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("restores portaled trigger focus on Escape", () => {
    openMenu(<Picker />);
    screen.getByRole("textbox", { name: "Find choice" }).focus();
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Find choice" }), { key: "Escape" });
    expect(screen.getByRole("button", { name: "Choose" })).toHaveFocus();
    expect(screen.getByRole("button", { name: "Choose" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("enters portaled fields from the trigger with Tab", async () => {
    vi.useRealTimers();
    const user = userEvent.setup();
    render(<Picker />);
    screen.getByRole("button", { name: "Choose" }).focus();
    await user.tab();
    expect(screen.getByRole("textbox", { name: "Find choice" })).toHaveFocus();
  });

  it("returns to the trigger with Shift Tab from the first portaled field", async () => {
    vi.useRealTimers();
    const user = userEvent.setup();
    render(<Picker />);
    screen.getByRole("textbox", { name: "Find choice" }).focus();
    await user.tab({ shift: true });
    expect(screen.getByRole("button", { name: "Choose" })).toHaveFocus();
    expect(screen.getByRole("button", { name: "Choose" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("continues to the following control with Tab from the last portaled field", async () => {
    vi.useRealTimers();
    const user = userEvent.setup();
    render(<Picker />);
    screen.getByRole("button", { name: "Select choice" }).focus();
    await user.tab();
    expect(screen.getByRole("button", { name: "Following control" })).toHaveFocus();
    expect(screen.getByRole("button", { name: "Choose" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  function NestedPickers() {
    const [open, setOpen] = useState(true);
    const [child, setChild] = useState(true);
    return (
      <DropdownMenu
        open={open}
        onOpenChange={setOpen}
        trigger={
          <button data-menu-trigger aria-expanded={open}>
            Parent
          </button>
        }
      >
        <DropdownMenu
          portal
          open={child}
          onOpenChange={setChild}
          role="dialog"
          trigger={
            <button role="menuitem" data-menu-trigger aria-expanded={child}>
              Child
            </button>
          }
        >
          <input aria-label="Nested choice" />
        </DropdownMenu>
      </DropdownMenu>
    );
  }

  it("keeps the parent open during nested portal interaction", () => {
    openMenu(<NestedPickers />);
    fireEvent.pointerDown(screen.getByRole("textbox", { name: "Nested choice" }));
    expect(screen.getByRole("button", { name: "Parent" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("menuitem", { name: "Child" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });

  it("dismisses the complete nested menu tree on an outside pointer", () => {
    openMenu(<NestedPickers />);
    fireEvent.pointerDown(document.body);
    expect(screen.getByRole("button", { name: "Parent" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(screen.getByRole("menuitem", { name: "Child" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("leaves the parent open when Escape closes a nested portal", () => {
    openMenu(<NestedPickers />);
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Nested choice" }), { key: "Escape" });
    expect(screen.getByRole("menuitem", { name: "Child" })).toHaveFocus();
    expect(screen.getByRole("menuitem", { name: "Child" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(screen.getByRole("button", { name: "Parent" })).toHaveAttribute("aria-expanded", "true");
  });

  it("leaves its enclosing modal open when Escape closes the picker", () => {
    function Form() {
      const [open, setOpen] = useState(true);
      return (
        <Modal open={open} onClose={() => setOpen(false)} title="Form" closeLabel="Close">
          <Picker />
        </Modal>
      );
    }
    openMenu(<Form />);
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Find choice" }), { key: "Escape" });
    expect(screen.getByRole("dialog", { name: "Form" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Choose" })).toHaveFocus();
  });

  it("continues within the enclosing modal after the last portaled field", async () => {
    vi.useRealTimers();
    const user = userEvent.setup();
    render(
      <Modal open onClose={() => {}} title="Form" closeLabel="Close">
        <Picker />
      </Modal>,
    );
    screen.getByRole("button", { name: "Select choice" }).focus();
    await user.tab();
    expect(screen.getByRole("button", { name: "Following control" })).toHaveFocus();
  });
});
