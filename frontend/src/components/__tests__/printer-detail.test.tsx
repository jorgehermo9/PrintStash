/*
 * One printer's console: what it is doing now, and the controls that change it.
 *
 * Everything here is gated twice — by what the *provider* can do and by what the
 * *user* may do — and the two are independent. A Bambu printer cannot be sent
 * raw G-code at all; a viewer may not pause a print on a printer that is
 * perfectly capable of pausing. Rendering a control that fails either check
 * gives the user a button that answers 409 or 403, which reads as the printer
 * being broken rather than as the action being unavailable.
 *
 * The live status arrives over a websocket. A page that cannot open one still
 * has to render the printer it already knows about, because "the socket is
 * down" and "the printer is gone" are different situations and only one of them
 * is worth alarming somebody about.
 *
 * The temperature form is the one place a typo has physical consequences, so it
 * is asserted on the request it produces rather than on the field's value.
 */

import "@testing-library/jest-dom/vitest";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PrinterDetailPage } from "@/components/printer-detail";
import { aPrinter, printerAccess, printerCapabilities } from "@/test-support/factories";
import { json, renderApp, type RenderAppOptions } from "@/test-support/render";
import type { PrinterRead } from "@/types";

/** The subset of a live snapshot these tests push, named so it is not a dictionary. */
interface LiveSnapshot {
  print_stats?: {
    state?: string;
    print_duration?: number;
    total_duration?: number;
  };
}

/**
 * jsdom has no WebSocket, and the live snapshot is the *only* source of the
 * print state the controls key off — so a socket that merely exists is not
 * enough. This one records itself so a test can push the snapshot the printer
 * would have sent.
 */
class FakeSocket {
  static latest: FakeSocket | null = null;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  readyState = 1;

  constructor() {
    FakeSocket.latest = this;
  }

  close() {}
  send() {}
}

/** Push the snapshot the printer would have sent over the live socket. */
async function pushSnapshot(data: LiveSnapshot) {
  await waitFor(() => expect(FakeSocket.latest?.onmessage).not.toBeNull());
  act(() => {
    FakeSocket.latest?.onmessage?.({ data: JSON.stringify({ type: "snapshot", data }) });
  });
}

/** A printer mid-print, as the live socket reports it. */
const PRINTING = { print_stats: { state: "printing", print_duration: 60, total_duration: 600 } };

function renderPrinter(options: RenderAppOptions & { printer?: PrinterRead } = {}) {
  const { printer = aPrinter({ id: 4, name: "Voron" }), routes = {}, ...rest } = options;
  return renderApp(<PrinterDetailPage printerId={4} initialPrinter={printer} />, {
    routes: {
      "GET /api/v1/printers/4": json(printer),
      "GET /api/v1/printers/4/jobs": json([]),
      "GET /api/v1/printers/4/files": json([]),
      "GET /api/v1/printers/4/status": json({ state: "ready" }),
      "GET /api/v1/printers/4/diagnostics": json({ provider: "moonraker", checks: [] }),
      "GET /api/v1/printers/4/config": json({ config: "" }),
      // The live socket is opened against a short-lived ticket, so the page
      // cannot reach a snapshot at all without this one.
      "POST /api/v1/printers/4/ws-ticket": json({ ticket: "t", expires_in: 60 }),
      "GET /api/v1/printers/4/materials": json({ slots: [], tools: [] }),
      ...routes,
    },
    ...rest,
  });
}

beforeEach(() => {
  window.localStorage.clear();
  FakeSocket.latest = null;
  vi.stubGlobal("WebSocket", FakeSocket);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("PrinterDetailPage", () => {
  describe("what it shows", () => {
    it("names the printer", async () => {
      renderPrinter();

      expect(await screen.findByText("Voron")).toBeInTheDocument();
    });

    it("reports there is nothing printing", async () => {
      renderPrinter();

      expect(await screen.findByText("No active print")).toBeInTheDocument();
    });

    it("opens on the status tab", async () => {
      renderPrinter();

      expect(await screen.findByRole("tab", { name: "Status" })).toHaveAttribute(
        "aria-selected",
        "true",
      );
    });

    it("stays rendered when the live socket never opens", async () => {
      // "The socket is down" and "the printer is gone" are different situations,
      // and only one of them is worth alarming somebody about.
      renderPrinter();

      expect(await screen.findByText("Voron")).toBeInTheDocument();
    });
  });

  describe("the tabs", () => {
    it("shows the printer's files", async () => {
      const user = userEvent.setup();
      renderPrinter();
      await screen.findByText("Voron");

      await user.click(screen.getByRole("tab", { name: "Files" }));

      expect(await screen.findByText("Printer files")).toBeInTheDocument();
    });

    it("shows the print history", async () => {
      const user = userEvent.setup();
      renderPrinter();
      await screen.findByText("Voron");

      await user.click(screen.getByRole("tab", { name: "Jobs" }));

      expect(await screen.findByText("Print history")).toBeInTheDocument();
    });

    it("offers the settings tab to an admin", async () => {
      renderPrinter();

      expect(await screen.findByRole("tab", { name: "Settings" })).toBeInTheDocument();
    });

    it("keeps the settings tab from someone who may only view", async () => {
      // The tab is where the credentials live, so hiding it is the boundary
      // rather than a convenience.
      renderPrinter({
        printer: aPrinter({ id: 4, name: "Voron", access: printerAccess({ can_admin: false }) }),
      });

      await screen.findByText("Voron");
      expect(screen.queryByRole("tab", { name: "Settings" })).toBeNull();
    });
  });

  describe("what the user may do", () => {
    it("offers pause to someone who may control the printer", async () => {
      renderPrinter();
      await screen.findByText("Voron");

      await pushSnapshot(PRINTING);

      expect(await screen.findByRole("button", { name: /Pause/ })).toBeEnabled();
    });

    it("withholds pause from someone who may only view", async () => {
      // The control stays visible and says why. A missing button reads as a
      // broken page; a disabled one reads as "not yours".
      renderPrinter({
        printer: aPrinter({
          id: 4,
          name: "Voron",
          access: printerAccess({ can_print: false, can_control: false, can_admin: false }),
        }),
      });
      await screen.findByText("Voron");

      await pushSnapshot(PRINTING);

      // Pause, resume and cancel all relabel, so every control says the same thing.
      const blocked = await screen.findAllByRole("button", { name: /No access/ });
      expect(blocked.every((button) => button.hasAttribute("disabled"))).toBe(true);
    });

    it("withholds a control the provider cannot perform", async () => {
      // A button that answers 409 reads as the printer being broken rather than
      // as the action being unsupported.
      renderPrinter({
        printer: aPrinter({
          id: 4,
          name: "Voron",
          capabilities: printerCapabilities({ can_pause: false }),
        }),
      });
      await screen.findByText("Voron");

      await pushSnapshot(PRINTING);

      expect(await screen.findByRole("button", { name: /Pause/ })).toBeDisabled();
    });

    it("withholds pause from a printer that is not printing", async () => {
      renderPrinter();
      await screen.findByText("Voron");

      await pushSnapshot({ print_stats: { state: "paused" } });

      expect(await screen.findByRole("button", { name: /Pause/ })).toBeDisabled();
    });

    it("offers resume to a paused printer", async () => {
      renderPrinter();
      await screen.findByText("Voron");

      await pushSnapshot({ print_stats: { state: "paused" } });

      expect(await screen.findByRole("button", { name: /Resume/ })).toBeEnabled();
    });
  });

  describe("controlling a print", () => {
    it("asks the printer to pause", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderPrinter({
        routes: { "POST /api/v1/printers/4/pause": json({ ok: true }) },
      });
      await screen.findByText("Voron");
      await pushSnapshot(PRINTING);

      await user.click(await screen.findByRole("button", { name: /Pause/ }));

      await waitFor(() =>
        expect(requestsWithMethod("POST").some((call) => call.url.includes("/pause"))).toBe(true),
      );
    });

    it("asks the printer to cancel", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderPrinter({
        routes: { "POST /api/v1/printers/4/cancel": json({ ok: true }) },
      });
      await screen.findByText("Voron");
      await pushSnapshot(PRINTING);

      await user.click(await screen.findByRole("button", { name: /Cancel/ }));

      await waitFor(() =>
        expect(requestsWithMethod("POST").some((call) => call.url.includes("/cancel"))).toBe(true),
      );
    });

    it("shows how far along the print is", async () => {
      renderPrinter();
      await screen.findByText("Voron");

      await pushSnapshot(PRINTING);

      expect(await screen.findByText("Current print")).toBeInTheDocument();
    });
  });

  describe("setting a temperature", () => {
    it("sends the hotend target the user typed", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderPrinter({
        routes: { "POST /api/v1/printers/4/temperature": json({ ok: true }) },
      });
      await screen.findByText("Voron");

      const inputs = screen.getAllByPlaceholderText("°C");
      await user.type(inputs[0], "215");
      await user.click(screen.getAllByRole("button", { name: /Set/ })[0]);

      await waitFor(() =>
        expect(
          JSON.parse(
            requestsWithMethod("POST").find((call) => call.url.includes("temperature"))?.body ??
              "{}",
          ),
        ).toMatchObject({ target: 215 }),
      );
    });
  });
});
