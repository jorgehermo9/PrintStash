/*
 * The outliner: the library's folder tree, plus the tag and printer filters.
 *
 * The tree is derived from a flat list of collections, and *what it hides* is
 * the whole of its behaviour. Typing in the filter box narrows it to matching
 * names — but a match deep in the tree is useless unless every folder above it
 * stays visible too, so the ancestors of a hit are kept. Drop that and a search
 * finds nothing it can show.
 *
 * A tag or printer filter narrows it a different way: those come with an
 * already-filtered model list, so the tree collapses to the folders that
 * actually hold those models. The distinction matters because a text query also
 * matches folder *names* while a facet filter only ever matches models.
 *
 * Drag and drop is how models and folders are reorganised, and both directions
 * are destructive-adjacent: a model dropped on the wrong folder is a model
 * nobody finds again, and a folder dropped into its own descendant is a cycle.
 */

import "@testing-library/jest-dom/vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FilterSidebar, type FilterSidebarProps } from "@/components/filter-sidebar";
import { aCollection, aPrinter, aTag } from "@/test-support/factories";
import { renderApp } from "@/test-support/render";
import type { OutlinerModelRead } from "@/types";

const TREE = [
  aCollection({ id: 1, name: "Parts", path: "parts", parent_id: null }),
  aCollection({ id: 2, name: "Brackets", path: "parts/brackets", parent_id: 1 }),
  aCollection({ id: 3, name: "Toys", path: "toys", parent_id: null }),
];

function outlinerModel(over: Partial<OutlinerModelRead> = {}): OutlinerModelRead {
  // The tree groups by `collection` *path*, not by id — a model with only an id
  // is invisible to it, which is exactly the drift this fixture pins down.
  return { id: 1, name: "Benchy", collection: "parts", collection_id: 1, ...over };
}

function renderSidebar(over: Partial<FilterSidebarProps> = {}) {
  const handlers = {
    onCollectionChange: vi.fn<FilterSidebarProps["onCollectionChange"]>(),
    onTagsChange: vi.fn<FilterSidebarProps["onTagsChange"]>(),
    onPrinterChange: vi.fn<FilterSidebarProps["onPrinterChange"]>(),
    onPrinterPresenceChange: vi.fn<FilterSidebarProps["onPrinterPresenceChange"]>(),
    onCreateCollection: vi.fn<FilterSidebarProps["onCreateCollection"]>(),
    onMoveModel: vi.fn<NonNullable<FilterSidebarProps["onMoveModel"]>>(),
    onMoveCollection: vi.fn<NonNullable<FilterSidebarProps["onMoveCollection"]>>(),
    onDeleteCollection: vi.fn<NonNullable<FilterSidebarProps["onDeleteCollection"]>>(),
  };
  // Model leaves are links into the vault, so the tree needs a router even
  // though nothing here navigates.
  const result = renderApp(
    <FilterSidebar
      collections={TREE}
      models={[]}
      tags={[aTag()]}
      printers={[aPrinter({ id: 4, name: "Voron" })]}
      selectedCollection={null}
      selectedTags={[]}
      selectedPrinterId={null}
      selectedPrinterPresence={null}
      {...handlers}
      {...over}
    />,
  );
  return { ...result, ...handlers };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("FilterSidebar", () => {
  describe("the folder tree", () => {
    it("lists the root folders", () => {
      renderSidebar();

      expect(screen.getByText("Parts")).toBeInTheDocument();
      expect(screen.getByText("Toys")).toBeInTheDocument();
    });

    it("offers the whole library as a destination", () => {
      renderSidebar();

      expect(screen.getByLabelText("All Models")).toBeInTheDocument();
    });

    it("reports the folder the user chose", async () => {
      const user = userEvent.setup();
      const { onCollectionChange } = renderSidebar();

      await user.click(screen.getByText("Parts"));

      expect(onCollectionChange).toHaveBeenCalledWith("parts");
    });

    it("returns to the whole library from the root entry", async () => {
      const user = userEvent.setup();
      const { onCollectionChange } = renderSidebar({ selectedCollection: "parts" });

      await user.click(screen.getByLabelText("All Models"));

      expect(onCollectionChange).toHaveBeenCalledWith(null);
    });

    it("nests a child folder under its parent", () => {
      renderSidebar();

      expect(screen.getByText("Brackets")).toBeInTheDocument();
    });

    it("folds a branch away on request", async () => {
      // A deep library is unscannable fully expanded, so a parent has to be
      // collapsible without losing the selection inside it.
      const user = userEvent.setup();
      renderSidebar();

      await user.click(screen.getAllByRole("button", { name: "Collapse" })[0]);

      expect(screen.queryByText("Brackets")).toBeNull();
    });
  });

  describe("narrowing the tree by name", () => {
    /** The sidebar owns the filter box, so the query is typed rather than passed. */
    async function filterBy(user: ReturnType<typeof userEvent.setup>, term: string) {
      await user.type(screen.getByPlaceholderText("Filter outliner..."), term);
    }

    it("keeps a folder whose name matches", async () => {
      const user = userEvent.setup();
      renderSidebar();

      await filterBy(user, "brack");

      expect(screen.getByText("Brackets")).toBeInTheDocument();
    });

    it("keeps the ancestors of a match so it can be reached", async () => {
      // A hit nobody can navigate to is a hit nobody can use.
      const user = userEvent.setup();
      renderSidebar();

      await filterBy(user, "brack");

      expect(screen.getByText("Parts")).toBeInTheDocument();
    });

    it("drops a folder that matches nothing", async () => {
      const user = userEvent.setup();
      renderSidebar();

      await filterBy(user, "brack");

      expect(screen.queryByText("Toys")).toBeNull();
    });

    it("keeps a folder holding a matching model", async () => {
      const user = userEvent.setup();
      renderSidebar({ models: [outlinerModel()] });

      await filterBy(user, "benchy");

      expect(screen.getByText("Parts")).toBeInTheDocument();
    });
  });

  describe("narrowing the tree by facet", () => {
    it("keeps the folder holding a filtered model", () => {
      // A tag filter arrives with the model list already narrowed, so the tree
      // shows where those models actually live rather than the whole library.
      renderSidebar({ selectedTags: ["functional"], models: [outlinerModel()] });

      expect(screen.getAllByText("Parts").length).toBeGreaterThan(0);
    });

    it("drops a folder holding none of them", () => {
      renderSidebar({ selectedTags: ["functional"], models: [outlinerModel()] });

      expect(screen.queryByText("Toys")).toBeNull();
    });
  });

  describe("the printer filter", () => {
    it("offers every location by default", () => {
      renderSidebar();

      expect(screen.getByRole("button", { name: /Any location/ })).toBeInTheDocument();
    });

    it("reports a switch to models on no printer", async () => {
      const user = userEvent.setup();
      const { onPrinterPresenceChange } = renderSidebar();

      await user.click(screen.getByRole("button", { name: /Vault only/ }));

      expect(onPrinterPresenceChange).toHaveBeenCalledWith("none");
    });

    it("reports a switch to models on any printer", async () => {
      const user = userEvent.setup();
      const { onPrinterPresenceChange } = renderSidebar();

      await user.click(screen.getByRole("button", { name: /On a printer/ }));

      expect(onPrinterPresenceChange).toHaveBeenCalledWith("any");
    });

    it("hides the printer filter from someone who cannot see printers", () => {
      renderSidebar({ canViewPrinters: false });

      expect(screen.queryByRole("button", { name: /Any location/ })).toBeNull();
    });
  });

  describe("creating a folder", () => {
    it("asks the caller to open its form", async () => {
      const user = userEvent.setup();
      const { onCreateCollection } = renderSidebar();

      await user.click(screen.getByRole("button", { name: /New collection|Create Collection/i }));

      expect(onCreateCollection).toHaveBeenCalledTimes(1);
    });
  });

  describe("while the library is loading", () => {
    it("keeps the outliner usable rather than emptying", () => {
      // An empty sidebar and a loading one look identical, and the first reads
      // as "you have no collections".
      renderSidebar({ loading: true, collections: [] });

      expect(screen.getByPlaceholderText("Filter outliner...")).toBeInTheDocument();
    });
  });
});
