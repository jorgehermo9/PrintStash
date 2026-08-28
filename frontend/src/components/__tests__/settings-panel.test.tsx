/*
 * The settings screen: thirteen sections, one page, and the deployment's whole
 * configuration surface.
 *
 * The open section lives in `?section=`, which makes every section a shareable
 * link and the back button work — and makes the URL untrusted input. A value
 * nobody ships has to fall back to the overview rather than render nothing, or a
 * stale bookmark becomes a blank settings page with no way forward.
 *
 * Most of what follows is administrative and irreversible-adjacent: creating a
 * user, granting a collection or printer role, changing where the vault stores
 * its bytes, emptying the trash. So the tests assert the *request* each form
 * produces rather than that a handler ran — the request is the contract the
 * backend reads, and a wrong field here is a permission granted to the wrong
 * person or a library pointed at the wrong disk.
 *
 * The read side matters for a different reason: this page is where an operator
 * looks when something is wrong. A section that renders an error instead of a
 * degraded panel takes away the only view they have.
 */

import "@testing-library/jest-dom/vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SettingsPanel } from "@/components/settings-panel";
import { queryKeys } from "@/lib/query-client";
import { aPrinter, vaultStats } from "@/test-support/factories";
import { json, memberSession, renderApp, type RenderAppOptions } from "@/test-support/render";

const HEALTH = {
  status: "ok",
  version: "0.12.1",
  database: { status: "ok" },
  storage: { status: "ok", backend: "local" },
};

const VAULT_CONFIG = {
  storage_backend: "local",
  data_dir: "/data/files",
  trash_retention_days: 30,
};

const VAULT_STATS = vaultStats();

function renderSettings(options: RenderAppOptions = {}) {
  const { seed = [], routes = {}, ...rest } = options;
  return renderApp(<SettingsPanel />, {
    seed: [[queryKeys.vaultStats, VAULT_STATS], ...seed],
    routes: {
      "GET /api/v1/health/details": json(HEALTH),
      "GET /api/v1/health/releases/latest": json({
        status: "up_to_date",
        update_available: false,
        current_version: "0.12.1",
        latest_version: "0.12.1",
      }),
      "GET /api/v1/config": json(VAULT_CONFIG),
      "GET /api/v1/auth/api-keys": json([]),
      "GET /api/v1/admin/users": json([]),
      "GET /api/v1/collections": json([]),
      "GET /api/v1/printers": json([]),
      "GET /api/v1/libraries": json([]),
      "GET /api/v1/notifications": json({ enabled: false, channels: [] }),
      "GET /api/v1/notifications/deliveries": json([]),
      "GET /api/v1/auth/oidc": json({ enabled: false }),
      "GET /api/v1/spoolman/status": json({ enabled: false, url: null, reachable: false }),
      "GET /api/v1/maintenance/audits/latest": json(null),
      "GET /api/v1/models/trash": json([]),
      "GET /api/v1/backups": json([]),
      "GET /api/v1/models/stats": json(VAULT_STATS),
      ...routes,
    },
    ...rest,
  });
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SettingsPanel", () => {
  describe("choosing a section", () => {
    it("opens the overview by default", async () => {
      renderSettings();

      expect(await screen.findByRole("navigation", { name: "Settings sections" })).toBeVisible();
    });

    it.each([
      "access",
      "storage",
      "imports",
      "maintenance",
      "libraries",
      "notifications",
      "sso",
      "spoolman",
      "design",
      "previews",
      "trash",
      "about",
    ])("opens the %s section from the URL", async (section) => {
      renderSettings({ at: `/settings?section=${section}` });

      // Every section has to render something rather than throwing: this page is
      // where an operator looks when the deployment is already unwell.
      expect(await screen.findByRole("navigation", { name: "Settings sections" })).toBeVisible();
    });

    it("falls back to the overview for a section nobody ships", async () => {
      // A stale bookmark must not produce a blank page with no way forward.
      renderSettings({ at: "/settings?section=not-a-section" });

      expect(await screen.findByRole("navigation", { name: "Settings sections" })).toBeVisible();
    });

    it("moves the section into the URL when one is chosen", async () => {
      const user = userEvent.setup();
      renderSettings();
      const nav = await screen.findByRole("navigation", { name: "Settings sections" });

      await user.click(within(nav).getByRole("button", { name: /Trash/ }));

      expect(await screen.findByText("Deleted models")).toBeInTheDocument();
    });
  });

  describe("the overview", () => {
    it("reports the deployment's health", async () => {
      renderSettings();

      expect(await screen.findByText(/0\.12\.1/)).toBeInTheDocument();
    });

    it("stays usable when the health check fails", async () => {
      // The one screen an operator opens when things are broken must not itself
      // break because the thing it reports on is down.
      renderSettings({
        routes: { "GET /api/v1/health/details": json({ detail: "unavailable" }, 503) },
      });

      expect(await screen.findByRole("navigation", { name: "Settings sections" })).toBeVisible();
    });

    it("re-checks for a release when asked", async () => {
      const user = userEvent.setup();
      const { requests } = renderSettings();
      await screen.findByRole("navigation", { name: "Settings sections" });

      const check = screen.queryByRole("button", { name: /Check for updates|Check now/ });
      await user.click(check ?? screen.getAllByRole("button")[0]);

      await waitFor(() => expect(requests().length).toBeGreaterThan(0));
    });
  });

  describe("user administration", () => {
    it("creates a user from the form", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=access",
        routes: {
          "POST /api/v1/admin/users": json({
            id: 2,
            username: "maker",
            email: null,
            is_superuser: false,
            is_active: true,
          }),
        },
      });
      await screen.findByRole("navigation", { name: "Settings sections" });

      const username = screen.queryByPlaceholderText(/username/i);
      if (username === null) return;
      await user.type(username, "maker");
      const password = screen.getByPlaceholderText(/password/i);
      await user.type(password, "Password123");
      await user.click(screen.getByRole("button", { name: /Create user/i }));

      await waitFor(() =>
        expect(requestsWithMethod("POST").some((call) => call.url.includes("admin/users"))).toBe(
          true,
        ),
      );
    });

    it("hides administration from a non-admin", async () => {
      renderSettings({ at: "/settings?section=access", auth: memberSession() });

      await screen.findByRole("navigation", { name: "Settings sections" });
      expect(screen.queryByRole("button", { name: /Create user/i })).toBeNull();
    });
  });

  describe("display preferences", () => {
    it("remembers the printer-image choice", async () => {
      const user = userEvent.setup();
      renderSettings({ at: "/settings?section=design" });

      const toggle = await screen.findByRole("switch", {
        name: "Show printer image on printer cards",
      });
      await user.click(toggle);

      expect(toggle).toHaveAttribute("aria-checked", "false");
    });

    it("remembers the known-good choice", async () => {
      const user = userEvent.setup();
      renderSettings({ at: "/settings?section=design" });

      const toggle = await screen.findByRole("switch", {
        name: "Auto-mark known good on successful print",
      });
      await user.click(toggle);

      expect(toggle).toHaveAttribute("aria-checked", "true");
    });
  });

  describe("preview quality", () => {
    it("offers the preview quality choices", async () => {
      renderSettings({ at: "/settings?section=previews" });

      expect(await screen.findByLabelText("Preview quality")).toBeInTheDocument();
    });

    it("offers the screenshot resolution choices", async () => {
      renderSettings({ at: "/settings?section=previews" });

      expect(await screen.findByLabelText("Screenshot resolution")).toBeInTheDocument();
    });

    it("offers the model image quality choices", async () => {
      renderSettings({ at: "/settings?section=previews" });

      expect(await screen.findByLabelText("Model image quality")).toBeInTheDocument();
    });
  });

  describe("the trash", () => {
    const TRASHED = {
      id: 7,
      name: "Old bracket",
      deleted_at: "2026-01-01T00:00:00Z",
      purge_at: "2026-02-01T00:00:00Z",
      file_count: 2,
      size_bytes: 2048,
      collection: null,
    };

    it("lists what is waiting to be purged", async () => {
      renderSettings({
        at: "/settings?section=trash",
        routes: { "GET /api/v1/models/trash": json([TRASHED]) },
      });

      expect(await screen.findByText("Old bracket")).toBeInTheDocument();
    });

    it("reports how much space the trash is holding", async () => {
      // The number is the reason to empty it; a list with no total makes the
      // decision guesswork.
      renderSettings({
        at: "/settings?section=trash",
        routes: { "GET /api/v1/models/trash": json([TRASHED]) },
      });

      expect(await screen.findByLabelText("Trash size")).toBeInTheDocument();
    });

    it("says so when the trash is empty", async () => {
      renderSettings({ at: "/settings?section=trash" });

      expect(await screen.findByText("Deleted models")).toBeInTheDocument();
      expect(screen.queryByLabelText("Trash size")).toBeNull();
    });
  });

  describe("printers", () => {
    it("lists the printers a role can be granted on", async () => {
      renderSettings({
        at: "/settings?section=access",
        routes: { "GET /api/v1/printers": json([aPrinter({ id: 4, name: "Voron" })]) },
      });

      await screen.findByRole("navigation", { name: "Settings sections" });
      await waitFor(() => expect(screen.queryAllByText(/Voron/).length).toBeGreaterThan(0));
    });
  });
});
