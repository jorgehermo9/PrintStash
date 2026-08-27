/*
 * "Pending Imports" stays reachable, in both navigation shells.
 *
 * The desktop profile menu and the mobile bottom bar are separate components with
 * separate lists, so a route added to one and not the other is invisible on the
 * platform nobody tested on — and this is the screen a user goes to when an
 * import is waiting for them, so an unreachable entry means the import sits there.
 *
 * The nested-route case exists because active-state matching is a prefix check:
 * an inbox *detail* page must still light up the Pending entry, or the user
 * appears to have navigated out of the section they are in.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";

import { BottomNavBar } from "@/components/bottom-nav-bar";
import { TopBar } from "@/components/top-bar";
import { AuthContext, type AuthState } from "@/lib/auth-context";
import { I18nProvider } from "@/lib/i18n";
import { usePathname } from "@/lib/navigation";

const fetchMock = vi.fn<typeof fetch>();

const adminAuth: AuthState = {
  user: { id: 1, username: "admin", email: null, is_superuser: true },
  loading: false,
  login: async () => {},
  logout: async () => {},
  refresh: async () => {},
};

function CurrentPath() {
  return <output data-testid="current-path">{usePathname()}</output>;
}

function renderNavigation(ui: React.ReactNode, initialEntry = "/") {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <AuthContext.Provider value={adminAuth}>
        <I18nProvider>
          {ui}
          <CurrentPath />
        </I18nProvider>
      </AuthContext.Provider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockResolvedValue(
    new Response("[]", { status: 200, headers: { "content-type": "application/json" } }),
  );
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => vi.unstubAllGlobals());

describe("AppNavigation", () => {
  it("keeps Pending reachable and selected in the desktop profile menu", async () => {
    const user = userEvent.setup();
    renderNavigation(<TopBar />);

    await user.click(screen.getByRole("button", { name: /admin/ }));
    const pending = screen.getByRole("menuitem", { name: "Pending" });
    expect(pending).toHaveAttribute("href", "/inbox");

    await user.click(pending);

    expect(screen.getByTestId("current-path")).toHaveTextContent("/inbox");
    await user.click(screen.getByRole("button", { name: /admin/ }));
    expect(screen.getByRole("menuitem", { name: "Pending" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("keeps Pending reachable and selected in the mobile bottom bar", async () => {
    const user = userEvent.setup();
    renderNavigation(<BottomNavBar />);

    const pending = screen.getByRole("link", { name: "Pending" });
    expect(pending).toHaveAttribute("href", "/inbox");

    await user.click(pending);

    expect(screen.getByTestId("current-path")).toHaveTextContent("/inbox");
    expect(pending).toHaveAttribute("aria-current", "page");
  });

  it("marks Pending active for a nested inbox route", () => {
    renderNavigation(<BottomNavBar />, "/inbox/41");

    expect(screen.getByRole("link", { name: "Pending" })).toHaveAttribute("aria-current", "page");
  });
});
