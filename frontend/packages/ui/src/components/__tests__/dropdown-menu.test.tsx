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
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DURATION } from "../../lib/overlay";
import { DropdownMenu } from "../dropdown-menu";

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
});
