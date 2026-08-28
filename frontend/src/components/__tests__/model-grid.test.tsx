/*
 * The vault: the page a user spends nearly all of their time on.
 *
 * It is the one screen that owns the whole filter state, and that state lives in
 * the URL rather than in React — a shared link, a bookmark, and the back button
 * all have to reproduce exactly what the person who sent it was looking at. So
 * the tests here drive the URL and assert on the request the grid made, because
 * "the filter is applied" and "the filter reached the server" are different
 * claims and only the second one is what the user sees.
 *
 * The URL is also user-editable, which makes it untrusted input. `?file_type=nonsense`
 * must be dropped rather than forwarded, or the grid asks the API for a value it
 * will reject and the user gets an error page for a typo.
 *
 * Collections are a tree rendered from a flat list, and the two derivations over
 * it — the children of the selected folder, and the breadcrumb trail back to the
 * root — are what make navigation possible at all. A breadcrumb that loses a
 * level strands the user in a folder they cannot leave.
 */

import "@testing-library/jest-dom/vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ModelBrowser } from "@/components/model-grid";
import { queryKeys } from "@/lib/query-client";
import type { CollectionRead, ModelListItem, TagRead } from "@/types";
import { aModelListItem } from "@/test-support/factories";
import { json, memberSession, renderApp, type RenderAppOptions } from "@/test-support/render";

function aCollection(override: Partial<CollectionRead> = {}): CollectionRead {
  return {
    id: 1,
    name: "Parts",
    slug: "parts",
    path: "parts",
    parent_id: null,
    model_count: 2,
    effective_role: "admin",
    ...override,
  };
}

function aTag(override: Partial<TagRead> = {}): TagRead {
  return { id: 1, name: "functional", slug: "functional", model_count: 3, ...override };
}

const EMPTY_FACETS = {
  file_type: [],
  material_type: [],
  slicer_name: [],
  printer_model: [],
  revision_status: [],
  print_outcome: [],
  storage: [],
};

function renderVault(
  options: RenderAppOptions & {
    models?: ModelListItem[];
    collections?: CollectionRead[];
    tags?: TagRead[];
  } = {},
) {
  const { models = [], collections = [], tags = [], seed = [], routes = {}, ...rest } = options;
  return renderApp(<ModelBrowser />, {
    seed: [
      [queryKeys.collections, collections],
      [queryKeys.tags, tags],
      [queryKeys.vaultStats, { model_count: models.length, file_count: 0, total_size_bytes: 0 }],
      ...seed,
    ],
    routes: {
      "GET /api/v1/models/facets": json(EMPTY_FACETS),
      "GET /api/v1/models/page": json({ items: models, total: models.length, next_cursor: null }),
      "GET /api/v1/models/outliner": json([]),
      "GET /api/v1/models": json(models),
      "GET /api/v1/saved-views": json([]),
      "GET /api/v1/documents": json([]),
      "GET /api/v1/collections": json(collections),
      "GET /api/v1/tags": json(tags),
      ...routes,
    },
    ...rest,
  });
}

/**
 * The query string of the last *page* request — the one that fetches the grid.
 * The facets and outliner calls share the `/api/v1/models` prefix and carry a
 * different parameter set, so matching the prefix alone reads the wrong request.
 */
function lastModelsQuery(requests: () => { method: string; url: string }[]): URLSearchParams {
  const url = requests()
    .filter((call) => call.method === "GET" && call.url.startsWith("/api/v1/models/page"))
    .at(-1)?.url;
  return new URLSearchParams(url?.split("?")[1] ?? "");
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ModelBrowser", () => {
  describe("listing", () => {
    it("renders a card for every model", async () => {
      renderVault({
        models: [
          aModelListItem({ id: 1, name: "Benchy" }),
          aModelListItem({ id: 2, name: "Cube" }),
        ],
      });

      expect(await screen.findByText("Benchy")).toBeInTheDocument();
      expect(screen.getByText("Cube")).toBeInTheDocument();
    });

    it("offers the empty state when the library has nothing in it", async () => {
      renderVault();

      expect(await screen.findByText("No models found")).toBeInTheDocument();
    });
  });

  describe("filters carried in the URL", () => {
    it("forwards the collection the URL selects", async () => {
      // `?c=` rather than `?collection=`: the short form is what the vault links
      // and the remembered-folder href both write.
      const { requests } = renderVault({ at: "/?c=parts", collections: [aCollection()] });

      await waitFor(() => expect(lastModelsQuery(requests).get("collection")).toBe("parts"));
    });

    it("forwards every tag the URL repeats", async () => {
      const { requests } = renderVault({ at: "/?tag=functional&tag=bracket" });

      await waitFor(() =>
        expect(lastModelsQuery(requests).getAll("tag")).toEqual(["functional", "bracket"]),
      );
    });

    it("forwards a recognised structured filter", async () => {
      const { requests } = renderVault({ at: "/?file_type=stl&file_type=3mf" });

      await waitFor(() =>
        expect(lastModelsQuery(requests).getAll("file_type")).toEqual(["stl", "3mf"]),
      );
    });

    it("drops a structured filter value the API does not accept", async () => {
      // The URL is user-editable, so an unknown value must never be forwarded —
      // the API would reject it and the user would see an error for a typo.
      const { requests } = renderVault({ at: "/?file_type=nonsense&file_type=stl" });

      await waitFor(() => expect(lastModelsQuery(requests).getAll("file_type")).toEqual(["stl"]));
    });

    it("forwards the favourites flag", async () => {
      const { requests } = renderVault({ at: "/?favorites=true" });

      await waitFor(() => expect(lastModelsQuery(requests).get("favorites")).toBe("true"));
    });

    it("forwards a search term", async () => {
      const { requests } = renderVault({ at: "/?q=bracket" });

      await waitFor(() => expect(lastModelsQuery(requests).get("q")).toBe("bracket"));
    });
  });

  describe("collection navigation", () => {
    it("asks the API only for what is directly in the selected folder", async () => {
      // The grid shows one level; descendants are reached by navigating into
      // them, which is what keeps a deep library from loading everything at once.
      const { requests } = renderVault({
        at: "/?c=parts",
        collections: [
          aCollection({ id: 1, name: "Parts", path: "parts" }),
          aCollection({ id: 2, name: "Brackets", path: "parts/brackets", parent_id: 1 }),
        ],
      });

      await waitFor(() => expect(lastModelsQuery(requests).get("direct")).toBe("true"));
    });

    it("offers the child folders of the selected one", async () => {
      renderVault({
        at: "/?c=parts",
        collections: [
          aCollection({ id: 1, name: "Parts", path: "parts" }),
          aCollection({ id: 2, name: "Brackets", path: "parts/brackets", parent_id: 1 }),
        ],
      });

      expect(await screen.findAllByText("Brackets")).not.toHaveLength(0);
    });

    it("traces a breadcrumb back to the root", async () => {
      renderVault({
        at: "/?c=parts/brackets",
        collections: [
          aCollection({ id: 1, name: "Parts", path: "parts" }),
          aCollection({ id: 2, name: "Brackets", path: "parts/brackets", parent_id: 1 }),
        ],
      });

      // Every level of the path has to be reachable, or the user is stranded in
      // a folder with no way back up.
      await waitFor(() => {
        const labels = screen.getAllByRole("button").map((button) => button.textContent);
        expect(labels).toEqual(expect.arrayContaining([expect.stringContaining("Parts")]));
        expect(labels).toEqual(expect.arrayContaining([expect.stringContaining("Brackets")]));
      });
    });
  });

  describe("display preferences", () => {
    it("starts in the grid the user last chose", async () => {
      window.localStorage.setItem("ps-vault-view", "list");

      renderVault({ models: [aModelListItem({ name: "Benchy" })] });

      expect(await screen.findByText("Name")).toBeInTheDocument();
    });

    it("remembers a switch to the list view", async () => {
      const user = userEvent.setup();
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });
      await screen.findByText("Benchy");

      await user.click(screen.getByRole("button", { name: /Display/ }));
      await user.click(screen.getByRole("menuitem", { name: "List View" }));

      expect(window.localStorage.getItem("ps-vault-view")).toBe("list");
    });

    it("remembers a sort choice", async () => {
      const user = userEvent.setup();
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });
      await screen.findByText("Benchy");

      await user.click(screen.getByRole("button", { name: "Sort models" }));
      await user.click(screen.getByRole("menuitem", { name: "Name A–Z" }));

      expect(window.localStorage.getItem("ps-vault-sort")).toBe("name-asc");
    });

    it("falls back to the newest sort when storage holds something unknown", async () => {
      window.localStorage.setItem("ps-vault-sort", "not-a-sort");

      renderVault({ models: [aModelListItem({ name: "Benchy" })] });

      await screen.findByText("Benchy");
      expect(screen.getByRole("button", { name: "Sort models" })).toHaveTextContent("Newest");
    });
  });

  describe("the documents tab", () => {
    it("opens on the documents tab when the URL asks for it", async () => {
      renderVault({ at: "/?v=docs" });

      expect(await screen.findByRole("button", { name: "Documents" })).toBeInTheDocument();
    });
  });

  describe("permissions", () => {
    it("disables uploading for a signed-out visitor", async () => {
      // The control stays visible and says why, rather than vanishing — a missing
      // button reads as a broken page, a disabled one reads as "sign in".
      renderVault({ auth: memberSession({ user: null }) });

      await waitFor(() => {
        const upload = screen.getByRole("button", { name: "Upload" });
        expect(upload).toBeDisabled();
        expect(upload).toHaveAttribute("title", expect.stringContaining("Sign in"));
      });
    });

    it("offers uploading to a signed-in user", async () => {
      renderVault();

      expect(await screen.findByRole("button", { name: /Upload/ })).toBeEnabled();
    });
  });

  describe("selection", () => {
    it("enters select mode on request", async () => {
      const user = userEvent.setup();
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });
      await screen.findByText("Benchy");

      await user.click(screen.getByRole("button", { name: /Select/ }));

      expect(screen.getByRole("button", { name: /Done/ })).toBeInTheDocument();
    });

    it("counts what the user selected", async () => {
      const user = userEvent.setup();
      renderVault({
        models: [
          aModelListItem({ id: 1, name: "Benchy" }),
          aModelListItem({ id: 2, name: "Cube" }),
        ],
      });
      await screen.findByText("Benchy");
      await user.click(screen.getByRole("button", { name: /Select/ }));

      await user.click(screen.getAllByRole("checkbox")[0]);

      // The count renders in both the desktop toolbar and the mobile bar.
      expect(await screen.findAllByText(/1 selected/)).not.toHaveLength(0);
    });
  });

  describe("recent folders", () => {
    it("ignores a stored list that is not an array", async () => {
      // The value is a UI convenience written by this component, but a user can
      // edit it — a crash here would take the whole vault page down.
      window.localStorage.setItem("ps-recent-folders", '{"not":"an array"}');

      renderVault({ models: [aModelListItem({ name: "Benchy" })] });

      expect(await screen.findByText("Benchy")).toBeInTheDocument();
    });

    it("ignores a stored list that is not JSON", async () => {
      window.localStorage.setItem("ps-recent-folders", "broken");

      renderVault({ models: [aModelListItem({ name: "Benchy" })] });

      expect(await screen.findByText("Benchy")).toBeInTheDocument();
    });
  });

  describe("upload deep link", () => {
    it("opens the upload dialog for ?upload=1", async () => {
      renderVault({ at: "/?upload=1" });

      expect(await screen.findByRole("dialog")).toBeInTheDocument();
    });
  });

  describe("tag filtering", () => {
    it("keeps a tag from the URL in the active filters", async () => {
      renderVault({
        at: "/?tag=functional",
        models: [aModelListItem({ name: "Benchy" })],
        tags: [aTag({ name: "functional", slug: "functional" })],
      });

      await screen.findByText("Benchy");
      // The chip is the only way back out of a tag that arrived in the URL, so
      // losing it strands the user in a filtered view they cannot widen.
      expect(screen.getByRole("button", { name: /Clear all/ })).toBeInTheDocument();
    });
  });

  describe("collection permissions", () => {
    it("offers no folder actions in a collection the user may only view", async () => {
      renderVault({
        at: "/?c=parts",
        auth: memberSession(),
        collections: [aCollection({ effective_role: "view" })],
      });

      await waitFor(() => expect(screen.queryByRole("button", { name: /New folder/i })).toBeNull());
    });
  });

  describe("creating a folder", () => {
    it("POSTs the folder under the one the user is in", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderVault({
        at: "/?c=parts",
        collections: [aCollection()],
        models: [aModelListItem({ name: "Benchy" })],
        routes: { "POST /api/v1/collections": json(aCollection({ id: 9, name: "Bolts" })) },
      });
      await screen.findByText("Benchy");

      await user.click(screen.getByRole("button", { name: /New collection/ }));
      await user.type(screen.getByPlaceholderText(/New subcollection/), "Bolts");
      await user.click(screen.getByRole("button", { name: "Create" }));

      await waitFor(() =>
        expect(requestsWithMethod("POST").at(-1)?.url).toBe("/api/v1/collections"),
      );
      // The parent travels as an id, not as a path prefix, so renaming the parent
      // cannot orphan a folder created under its old name.
      expect(JSON.parse(requestsWithMethod("POST").at(-1)?.body ?? "{}")).toMatchObject({
        name: "Bolts",
        parent_id: 1,
      });
    });

    it("creates at the root when no folder is selected", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderVault({
        models: [aModelListItem({ name: "Benchy" })],
        routes: { "POST /api/v1/collections": json(aCollection({ id: 9, name: "Bolts" })) },
      });
      await screen.findByText("Benchy");

      await user.click(screen.getByRole("button", { name: /New collection/ }));
      await user.type(screen.getByPlaceholderText("Collection name..."), "Bolts");
      await user.click(screen.getByRole("button", { name: "Create" }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("POST").at(-1)?.body ?? "{}")).toMatchObject({
          name: "Bolts",
          parent_id: null,
        }),
      );
    });

    it("refuses to create a folder with no name", async () => {
      const user = userEvent.setup();
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });
      await screen.findByText("Benchy");

      await user.click(screen.getByRole("button", { name: /New collection/ }));

      expect(screen.getByRole("button", { name: "Create" })).toBeDisabled();
    });

    it("abandons the form on cancel", async () => {
      const user = userEvent.setup();
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });
      await screen.findByText("Benchy");
      await user.click(screen.getByRole("button", { name: /New collection/ }));

      await user.click(screen.getByRole("button", { name: "Cancel" }));

      expect(screen.queryByPlaceholderText("Collection name...")).toBeNull();
    });
  });

  describe("acting on several models at once", () => {
    async function selectBoth(user: ReturnType<typeof userEvent.setup>) {
      await screen.findByText("Benchy");
      await user.click(screen.getByRole("button", { name: "Select" }));
      await user.click(screen.getByRole("button", { name: /Select all on screen/ }));
    }

    it("moves the selection in one request", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderVault({
        collections: [aCollection()],
        models: [
          aModelListItem({ id: 1, name: "Benchy" }),
          aModelListItem({ id: 2, name: "Cube" }),
        ],
        routes: { "POST /api/v1/models/batch/move": json({ succeeded_ids: [1, 2] }) },
      });
      await selectBoth(user);

      await user.click(screen.getByRole("button", { name: "Move" }));
      const dialog = await screen.findByRole("dialog");
      await user.click(within(dialog).getByRole("button", { name: /None \(root\)/ }));
      await user.click(within(dialog).getByRole("button", { name: "Move here" }));

      await waitFor(() =>
        expect(requestsWithMethod("POST").some((call) => call.url.includes("/batch/move"))).toBe(
          true,
        ),
      );
    });

    it("deletes the selection in one request", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderVault({
        models: [
          aModelListItem({ id: 1, name: "Benchy" }),
          aModelListItem({ id: 2, name: "Cube" }),
        ],
        routes: { "POST /api/v1/models/batch/delete": json({ succeeded_ids: [1, 2] }) },
      });
      await selectBoth(user);

      await user.click(screen.getByRole("button", { name: "Delete" }));
      const dialog = await screen.findByRole("dialog");
      await user.click(within(dialog).getByRole("button", { name: /Delete|Move to trash/ }));

      await waitFor(() =>
        expect(requestsWithMethod("POST").some((call) => call.url.includes("/batch/delete"))).toBe(
          true,
        ),
      );
    });

    it("leaves select mode when the user is done", async () => {
      // Leaving the mode has to take the checkboxes with it, or the grid stays
      // in a state the user thought they had left.
      const user = userEvent.setup();
      renderVault({
        models: [
          aModelListItem({ id: 1, name: "Benchy" }),
          aModelListItem({ id: 2, name: "Cube" }),
        ],
      });
      await screen.findByText("Benchy");
      await user.click(screen.getByRole("button", { name: "Select" }));

      await user.click(screen.getByRole("button", { name: "Done" }));

      expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
    });

    it("selects every model the current filters match", async () => {
      // The toolbar acts on ids, so "select all matching" has to fetch the ids
      // the filters resolve to rather than the page the user can see.
      const user = userEvent.setup();
      const { requests } = renderVault({
        models: [
          aModelListItem({ id: 1, name: "Benchy" }),
          aModelListItem({ id: 2, name: "Cube" }),
        ],
      });
      await screen.findByText("Benchy");
      await user.click(screen.getByRole("button", { name: "Select" }));

      await user.click(screen.getByRole("button", { name: /Select all matching models/ }));

      await waitFor(() =>
        expect(requests().some((call) => call.url.includes("limit=500"))).toBe(true),
      );
    });
  });

  describe("batch outcomes", () => {
    async function selectOneModel(user: ReturnType<typeof userEvent.setup>) {
      await screen.findByText("Benchy");
      await user.click(screen.getByRole("button", { name: "Select" }));
      await user.click(screen.getAllByRole("checkbox")[0]);
    }

    it("tags the selection in one request", async () => {
      const user = userEvent.setup();
      const { requests } = renderVault({
        models: [aModelListItem({ id: 1, name: "Benchy" })],
        tags: [aTag()],
        routes: {
          "POST /api/v1/models/batch/tags": json({
            succeeded_ids: [1],
            succeeded_count: 1,
            failed_count: 0,
            failed: [],
          }),
        },
      });
      await selectOneModel(user);

      await user.click(screen.getByRole("button", { name: "Tag" }));
      const dialog = await screen.findByRole("dialog");
      await user.type(within(dialog).getAllByRole("combobox")[0], "functional{Enter}");
      await user.click(within(dialog).getByRole("button", { name: /Apply/ }));

      await waitFor(() =>
        expect(requests().some((call) => call.url.includes("/batch/tags"))).toBe(true),
      );
    });

    it("reports what a partial batch skipped", async () => {
      // A batch that half-succeeded must say so; reporting only the successes
      // leaves the user believing models moved that did not.
      const user = userEvent.setup();
      renderVault({
        models: [aModelListItem({ id: 1, name: "Benchy" })],
        routes: {
          "POST /api/v1/models/batch/delete": json({
            succeeded_ids: [],
            succeeded_count: 0,
            failed_count: 1,
            failed: [{ model_id: 1, reason: "forbidden" }],
          }),
        },
      });
      await selectOneModel(user);

      await user.click(screen.getByRole("button", { name: "Delete" }));
      const dialog = await screen.findByRole("dialog");
      await user.click(within(dialog).getByRole("button", { name: /Delete|Move to trash/ }));

      expect(await screen.findByText(/1 skipped/)).toBeInTheDocument();
    });

    it("surfaces a batch that failed outright", async () => {
      const user = userEvent.setup();
      renderVault({
        models: [aModelListItem({ id: 1, name: "Benchy" })],
        routes: {
          "POST /api/v1/models/batch/delete": json({ detail: "forbidden" }, 403),
        },
      });
      await selectOneModel(user);

      await user.click(screen.getByRole("button", { name: "Delete" }));
      const dialog = await screen.findByRole("dialog");
      await user.click(within(dialog).getByRole("button", { name: /Delete|Move to trash/ }));

      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    });
  });

  describe("dragging files onto the vault", () => {
    /**
     * The slice of `DataTransfer` the drop handlers read. jsdom cannot construct
     * a real one, and the handlers only ever touch these four members — a fuller
     * stand-in would assert nothing more.
     */
    interface DroppedPayload {
      types: string[];
      files: File[];
      items: DataTransferItem[];
      dropEffect: string;
    }

    function dataTransfer(files: File[]): DroppedPayload {
      return { types: ["Files"], files, items: [], dropEffect: "none" };
    }

    it("opens the upload dialog for a dropped mesh", async () => {
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });
      const main = await screen.findByText("Benchy");

      fireEvent.drop(main, { dataTransfer: dataTransfer([new File(["x"], "cube.stl")]) });

      expect(await screen.findByRole("dialog")).toBeInTheDocument();
    });

    it("ignores a drop that carries nothing importable", async () => {
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });
      const main = await screen.findByText("Benchy");

      fireEvent.drop(main, { dataTransfer: dataTransfer([new File(["x"], "notes.txt")]) });

      expect(screen.queryByRole("dialog")).toBeNull();
    });

    it("opens the upload dialog for a dropped archive", async () => {
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });
      const main = await screen.findByText("Benchy");

      fireEvent.drop(main, { dataTransfer: dataTransfer([new File(["x"], "pack.zip")]) });

      expect(await screen.findByRole("dialog")).toBeInTheDocument();
    });

    it("opens the upload dialog for several dropped meshes", async () => {
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });
      const main = await screen.findByText("Benchy");

      fireEvent.drop(main, {
        dataTransfer: dataTransfer([new File(["a"], "a.stl"), new File(["b"], "b.stl")]),
      });

      expect(await screen.findByRole("dialog")).toBeInTheDocument();
    });

    it("opens the upload dialog for a dropped G-code file", async () => {
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });
      const main = await screen.findByText("Benchy");

      fireEvent.drop(main, { dataTransfer: dataTransfer([new File(["x"], "part.gcode")]) });

      expect(await screen.findByRole("dialog")).toBeInTheDocument();
    });

    it("ignores a model being dragged between folders", async () => {
      // An internal model drag carries its own MIME type and is handled by the
      // folder drop targets; treating it as a file upload would open the dialog
      // on top of the move the user is doing.
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });
      const main = await screen.findByText("Benchy");

      const modelDrag: DroppedPayload = {
        types: ["application/x-printstash-model"],
        files: [],
        items: [],
        dropEffect: "move",
      };

      fireEvent.drop(main, { dataTransfer: modelDrag });

      expect(screen.queryByRole("dialog")).toBeNull();
    });
  });

  describe("saved views", () => {
    it("saves the current filters under a name", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderVault({
        at: "/?tag=functional",
        models: [aModelListItem({ name: "Benchy" })],
        routes: { "POST /api/v1/saved-views": json({ id: 1, name: "PETG", filters: {} }) },
      });
      await screen.findByText("Benchy");

      await user.click(screen.getByRole("button", { name: /Saved views/ }));
      await user.click(await screen.findByRole("button", { name: /Save current view/ }));
      const dialog = await screen.findByRole("dialog");
      await user.type(within(dialog).getByRole("textbox"), "PETG");
      await user.click(within(dialog).getByRole("button", { name: "Save view" }));

      await waitFor(() =>
        expect(requestsWithMethod("POST").some((call) => call.url.includes("saved-views"))).toBe(
          true,
        ),
      );
    });
  });

  describe("clearing filters", () => {
    it("drops every active filter in one action", async () => {
      // Undoing them one at a time is the difference between "start over" and a
      // chore, and a filter left behind quietly narrows every later search.
      const user = userEvent.setup();
      const { requests } = renderVault({
        at: "/?tag=functional&favorites=true&q=bracket",
        models: [aModelListItem({ name: "Benchy" })],
      });
      await screen.findByText("Benchy");

      await user.click(await screen.findByRole("button", { name: /Clear all/ }));

      await waitFor(() => {
        const query = lastModelsQuery(requests);
        expect(query.getAll("tag")).toEqual([]);
        expect(query.get("favorites")).toBeNull();
      });
    });
  });

  describe("the documents tab", () => {
    it("offers a way to write a new document", async () => {
      renderVault({ at: "/?v=docs" });

      expect(await screen.findByRole("button", { name: /New document/ })).toBeInTheDocument();
    });

    it("offers a way to upload one", async () => {
      renderVault({ at: "/?v=docs" });

      expect(await screen.findByRole("button", { name: /Upload PDF/ })).toBeInTheDocument();
    });

    it("remembers the tab for the next visit", async () => {
      const user = userEvent.setup();
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });
      await screen.findByText("Benchy");

      await user.click(screen.getByRole("button", { name: "Documents" }));

      expect(window.localStorage.getItem("printstash.last.view")).toBe("docs");
    });
  });

  describe("pagination", () => {
    it("offers more when the page reports a cursor", async () => {
      renderVault({
        models: [aModelListItem({ name: "Benchy" })],
        routes: {
          "GET /api/v1/models/page": json({
            items: [aModelListItem({ name: "Benchy" })],
            total: 120,
            next_cursor: "next",
          }),
        },
      });

      await screen.findByText("Benchy");
      await waitFor(() =>
        expect(screen.queryByRole("button", { name: /Load more/ })).toBeInTheDocument(),
      );
    });
  });
});
