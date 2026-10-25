/*
 * The desktop header: the logo, the model search, and the account menu.
 *
 * Two things here are stateful in ways a render test is the only way to catch.
 *
 * The search box is owned by the URL, not by the input. A `?q=` in the address
 * bar — from a back navigation, a bookmark, or a shared link — has to land in the
 * box, and typing has to end up back in the URL after a debounce. Break either
 * direction and the results stop matching what the user sees they searched for.
 * It is also deliberately absent everywhere except the vault: a search box on the
 * settings page that silently searches models is worse than no box.
 *
 * The account menu is the desktop half of the navigation, and it carries the same
 * prefix-matched active state and admin-only filtering as the mobile bar. A route
 * added to one shell and not the other is invisible on the platform nobody tested
 * on.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TopBar } from "@/components/top-bar";
import { type AuthState } from "@/lib/auth-context";
import { json, renderApp } from "@/test-support/render";
import { searchStatus } from "@/test-support/search";
import { usePathname, useSearchParams } from "@/lib/navigation";

const fetchMock = vi.fn<typeof fetch>();

function session(overrides: Partial<AuthState> = {}): AuthState {
  return {
    user: { id: 1, username: "admin", email: null, is_superuser: true },
    loading: false,
    login: async () => {},
    logout: async () => {},
    refresh: async () => {},
    ...overrides,
  };
}

/** Renders the current location, so a navigation or a URL write is observable. */
function CurrentLocation() {
  const search = useSearchParams().toString();
  return <output data-testid="location">{`${usePathname()}${search ? `?${search}` : ""}`}</output>;
}

function renderTopBar({
  at = "/",
  auth = session(),
  matchesMedia,
}: { at?: string; auth?: AuthState; matchesMedia?: (query: string) => boolean } = {}) {
  return renderApp(
    <>
      <TopBar />
      <CurrentLocation />
    </>,
    {
      at,
      auth,
      matchesMedia,
      routes: {
        "GET /api/v1/search/status": json(searchStatus()),
        "GET /api/v1/jobs": json([]),
      },
    },
  );
}

function openProfileMenu(user: ReturnType<typeof userEvent.setup>) {
  return user.click(screen.getByRole("button", { name: "My workspace" }));
}

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("printstash.locale", "en");
  fetchMock.mockReset();
  fetchMock.mockResolvedValue(
    new Response("[]", { status: 200, headers: { "content-type": "application/json" } }),
  );
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("TopBar", () => {
  it("links Wiki to the documentation site", () => {
    renderTopBar();

    expect(screen.getByRole("link", { name: "Wiki" })).toHaveAttribute(
      "href",
      "https://www.printstash.org/docs/",
    );
  });

  describe("search", () => {
    it("shows the search box on the vault", () => {
      renderTopBar();

      expect(screen.getByRole("searchbox", { name: "Search library" })).toBeInTheDocument();
    });

    it("hides the search box away from the vault", () => {
      renderTopBar({ at: "/settings" });

      expect(screen.queryByRole("searchbox", { name: "Search library" })).toBeNull();
    });

    it("starts from the term already in the URL", () => {
      renderTopBar({ at: "/?q=benchy" });

      expect(screen.getByRole("searchbox", { name: "Search library" })).toHaveValue("benchy");
    });

    it("writes what the user typed back to the URL", async () => {
      const user = userEvent.setup();
      renderTopBar();

      await user.type(screen.getByRole("searchbox", { name: "Search library" }), "benchy");

      await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/?q=benchy"));
    });

    it("offers a clear affordance once there is a term", () => {
      renderTopBar({ at: "/?q=benchy" });

      expect(screen.getByRole("button", { name: "Clear search" })).toBeInTheDocument();
    });

    it("drops the term from the URL when the search is cleared", async () => {
      const user = userEvent.setup();
      renderTopBar({ at: "/?q=benchy" });

      await user.click(screen.getByRole("button", { name: "Clear search" }));

      await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/"));
      expect(screen.getByRole("searchbox", { name: "Search library" })).toHaveValue("");
    });

    it("focuses the box when the user presses slash", async () => {
      const user = userEvent.setup();
      renderTopBar();

      await user.keyboard("/");

      expect(screen.getByRole("searchbox", { name: "Search library" })).toHaveFocus();
    });

    it("leaves slash alone while the user is typing in the box", async () => {
      // Otherwise the shortcut swallows the character in the one field it is
      // meant to serve.
      const user = userEvent.setup();
      renderTopBar();
      const box = screen.getByRole("searchbox", { name: "Search library" });
      await user.click(box);

      await user.keyboard("a/b");

      expect(box).toHaveValue("a/b");
    });
  });

  describe("account menu", () => {
    it.each([true, false])("makes Builds reachable for either role (%s)", async (isAdmin) => {
      const user = userEvent.setup();
      renderTopBar({
        at: "/builds/1",
        auth: session({ user: { id: 1, username: "admin", email: null, is_superuser: isAdmin } }),
      });
      await openProfileMenu(user);
      const link = screen.getByRole("menuitem", { name: "Builds" });
      expect(link).toHaveAttribute("href", "/builds");
      expect(link).toHaveAttribute("aria-current", "page");
      await user.click(link);
      expect(screen.getByTestId("location")).toHaveTextContent("/builds");
      await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument());
    });

    it("offers a log-in link to a signed-out visitor", () => {
      renderTopBar({ auth: session({ user: null }) });

      expect(screen.getByRole("link", { name: "Log in" })).toHaveAttribute("href", "/login");
    });

    it("names the signed-in user inside My workspace", async () => {
      const user = userEvent.setup();
      renderTopBar();
      await openProfileMenu(user);
      expect(screen.getByRole("menu")).toHaveTextContent("admin");
    });

    it("does not duplicate the Library printer quick access", async () => {
      const user = userEvent.setup();
      renderTopBar();
      await openProfileMenu(user);
      expect(screen.queryByRole("menuitem", { name: "Printers" })).not.toBeInTheDocument();
    });

    it.each(["/settings", "/models/1", "/library/similar"])(
      "keeps Printers reachable away from the sidebar at %s",
      async (at) => {
        const user = userEvent.setup();
        renderTopBar({ at });
        await openProfileMenu(user);
        expect(screen.getByRole("menuitem", { name: "Printers" })).toHaveAttribute(
          "href",
          "/printers",
        );
      },
    );

    it("keeps Printers reachable when the desktop sidebar is hidden", async () => {
      const user = userEvent.setup();
      renderTopBar({ matchesMedia: () => false });
      await openProfileMenu(user);
      expect(screen.getByRole("menuitem", { name: "Printers" })).toHaveAttribute(
        "href",
        "/printers",
      );
    });

    it("retains the existing workspace destination names", async () => {
      const user = userEvent.setup();
      renderTopBar();
      await openProfileMenu(user);
      expect(screen.getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
        "Vault",
        "Pending",
        "Builds",
        "Statistics",
        "Profiles",
        "Settings",
        "Log out",
      ]);
    });

    it("returns focus to My workspace after Escape", async () => {
      const user = userEvent.setup();
      renderTopBar();
      await openProfileMenu(user);
      await user.keyboard("{Escape}");
      await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument());
      expect(screen.getByRole("button", { name: "My workspace" })).toHaveFocus();
    });

    it.each([
      { label: "administrator", isAdmin: true },
      { label: "regular user", isAdmin: false },
    ])("omits Wiki from the profile dropdown for a $label", async ({ isAdmin }) => {
      const user = userEvent.setup();
      renderTopBar({
        auth: session({ user: { id: 1, username: "admin", email: null, is_superuser: isAdmin } }),
      });

      await openProfileMenu(user);

      expect(screen.getByRole("menu")).toBeInTheDocument();
      expect(screen.queryByRole("menuitem", { name: "Wiki" })).toBeNull();
    });

    it("links Pending at the inbox route", async () => {
      const user = userEvent.setup();
      renderTopBar();

      await openProfileMenu(user);

      expect(screen.getByRole("menuitem", { name: "Pending" })).toHaveAttribute("href", "/inbox");
    });

    it("navigates when a destination is chosen", async () => {
      const user = userEvent.setup();
      renderTopBar();
      await openProfileMenu(user);

      await user.click(screen.getByRole("menuitem", { name: "Pending" }));

      expect(screen.getByTestId("location")).toHaveTextContent("/inbox");
    });

    it("marks the destination for the current route", async () => {
      const user = userEvent.setup();
      renderTopBar({ at: "/inbox" });

      await openProfileMenu(user);

      expect(screen.getByRole("menuitem", { name: "Pending" })).toHaveAttribute(
        "aria-current",
        "page",
      );
    });

    it("marks Pending for a nested inbox route", async () => {
      const user = userEvent.setup();
      renderTopBar({ at: "/inbox/41" });

      await openProfileMenu(user);

      expect(screen.getByRole("menuitem", { name: "Pending" })).toHaveAttribute(
        "aria-current",
        "page",
      );
    });

    it("drops the admin-only destinations for an ordinary user", async () => {
      const user = userEvent.setup();
      renderTopBar({
        at: "/settings",
        auth: session({ user: { id: 2, username: "maker", email: null, is_superuser: false } }),
      });

      await user.click(screen.getByRole("button", { name: "My workspace" }));

      expect(screen.queryByRole("menuitem", { name: "Printers" })).toBeNull();
      expect(screen.queryByRole("menuitem", { name: "Statistics" })).toBeNull();
    });

    it("signs the user out", async () => {
      const logout = vi.fn<() => Promise<void>>().mockResolvedValue(undefined);
      const user = userEvent.setup();
      renderTopBar({ auth: session({ logout }) });
      await openProfileMenu(user);

      await user.click(screen.getByRole("menuitem", { name: "Log out" }));

      expect(logout).toHaveBeenCalledTimes(1);
    });

    it("sends the signed-out user to the login page", async () => {
      const user = userEvent.setup();
      renderTopBar();
      await openProfileMenu(user);

      await user.click(screen.getByRole("menuitem", { name: "Log out" }));

      await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/login"));
    });
  });

  it("keeps Similar models out of the desktop header", () => {
    renderTopBar();
    expect(screen.queryByRole("link", { name: "Similar models" })).toBeNull();
  });

  describe("home link", () => {
    it("returns to the vault root by default", () => {
      renderTopBar({ at: "/settings" });

      expect(screen.getByRole("link", { name: /PrintStash/ })).toHaveAttribute("href", "/");
    });

    it("returns to the collection the user was last in", () => {
      // The logo is the way back to browsing, so dropping the remembered folder
      // means every trip through settings costs the user their place.
      localStorage.setItem("printstash.last.collection", "Calibration/Boats");

      renderTopBar({ at: "/settings" });

      expect(screen.getByRole("link", { name: /PrintStash/ })).toHaveAttribute(
        "href",
        "/?c=Calibration%2FBoats",
      );
    });
  });

  describe("task tray", () => {
    it("opens the task list", async () => {
      const user = userEvent.setup();
      renderTopBar();

      await user.click(screen.getByRole("button", { name: "Notifications" }));

      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });
  });
});
