/*
 * The gate every page passes through before it renders anything.
 *
 * A vault with no admin account is not a vault the user can be shown — every
 * screen behind it would 401, and the only way forward is the setup wizard. So
 * an unconfigured backend redirects there from whatever URL the user typed,
 * bookmark included, and a configured one refuses to serve the wizard a second
 * time: running it again on a live vault is how somebody creates a second
 * "first" admin.
 *
 * Nothing renders while the probe is in flight. Painting the shell first and
 * redirecting afterwards flashes a UI the user cannot use, and on a slow link
 * they get long enough to click something in it.
 *
 * A backend that cannot be reached at all is the one case where the gate lets go
 * rather than holding: locking the tree behind a probe that will never answer
 * hides the error UI that would have explained why.
 */

import "@testing-library/jest-dom/vitest";
import { screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SetupGate } from "@/components/setup-gate";
import { usePathname } from "@/lib/navigation";
import {
  adminSession,
  json,
  memberSession,
  renderApp,
  type RenderAppOptions,
} from "@/test-support/render";
import type { SetupStatus } from "@/types";

const CONFIGURED: SetupStatus = { configured: true, user_count: 1 };
const UNCONFIGURED: SetupStatus = { configured: false, user_count: 0 };

function Path() {
  return <span data-testid="path">{usePathname()}</span>;
}

function renderGate(options: RenderAppOptions & { status?: SetupStatus } = {}) {
  const { status = CONFIGURED, routes = {}, ...rest } = options;
  return renderApp(
    <SetupGate>
      <p>the vault</p>
      <Path />
    </SetupGate>,
    {
      routes: { "GET /api/v1/setup/status": json(status), ...routes },
      ...rest,
    },
  );
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SetupGate", () => {
  describe("while the probe is in flight", () => {
    it("shows nothing of the app", () => {
      // Painting the shell and redirecting afterwards gives the user long
      // enough on a slow link to click something they cannot use.
      renderGate({
        routes: { "GET /api/v1/setup/status": () => json(CONFIGURED) },
      });

      expect(screen.queryByText("the vault")).toBeNull();
    });
  });

  describe("a configured vault", () => {
    it("lets the app through", async () => {
      renderGate();

      expect(await screen.findByText("the vault")).toBeInTheDocument();
    });

    it("refuses to run the wizard a second time", async () => {
      // Re-running setup on a live vault is how somebody creates a second
      // "first" admin.
      renderGate({ at: "/setup" });

      await waitFor(() => expect(screen.queryByText("the vault")).toBeNull());
    });
  });

  describe("a vault with nobody in it", () => {
    it("holds the app back", async () => {
      // Every screen behind this would 401; the only way forward is the wizard.
      renderGate({ status: UNCONFIGURED });

      await waitFor(() => expect(screen.queryByText("the vault")).toBeNull());
    });

    it("serves the wizard itself", async () => {
      renderGate({ status: UNCONFIGURED, at: "/setup" });

      expect(await screen.findByText("the vault")).toBeInTheDocument();
    });
  });

  describe("an environment-provisioned owner who has not chosen storage", () => {
    // VAULT_SETUP_ADMIN_* creates the account before anyone chose where files
    // live; every page but the storage step would write to storage nobody chose.
    const CHOOSING: SetupStatus = { ...CONFIGURED, storage_choice_required: true };

    it("sends a superuser to the storage step", async () => {
      renderGate({ status: CHOOSING, at: "/models" });

      expect(await screen.findByTestId("path")).toHaveTextContent("/getting-started");
    });

    it("keeps anyone else where they are", async () => {
      // Only a superuser can choose; redirecting anyone else would bounce forever.
      renderGate({ status: CHOOSING, at: "/models", auth: memberSession() });

      expect(await screen.findByTestId("path")).toHaveTextContent("/models");
    });

    it("holds the app back until it knows who signed in", async () => {
      // A full page load learns the user after the probe answers; rendering first
      // would show a page the owner is about to be redirected away from.
      renderGate({
        status: CHOOSING,
        at: "/models",
        auth: adminSession({ user: null, loading: true }),
      });

      await waitFor(() => expect(screen.queryByText("the vault")).toBeNull());
    });

    it("serves the storage step itself", async () => {
      renderGate({ status: CHOOSING, at: "/getting-started" });

      expect(await screen.findByText("the vault")).toBeInTheDocument();
    });
  });

  describe("a backend nobody can reach", () => {
    it("lets the app through anyway", async () => {
      // Holding the tree behind a probe that will never answer hides the error
      // UI that would have explained why.
      renderGate({
        routes: { "GET /api/v1/setup/status": json({ detail: "unavailable" }, 503) },
      });

      expect(await screen.findByText("the vault")).toBeInTheDocument();
    });
  });
});
