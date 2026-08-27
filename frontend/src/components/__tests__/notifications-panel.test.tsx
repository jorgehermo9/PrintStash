/*
 * Notification channels, which carry webhook URLs and are admin-only.
 *
 * The permission row is first because it is a leak, not a layout bug: a channel's
 * configuration includes its target URL, and Discord and Slack webhook URLs *are*
 * the credential. A non-admin who can see the management surface can read them.
 *
 * The auto-disabled state is the other one worth its own test. A channel that
 * repeatedly failed gets switched off by the backend, and rendering it like any
 * other channel means the operator believes notifications are going out while
 * nothing has been sent for days.
 */

import "@testing-library/jest-dom/vitest";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { NotificationsPanel, type NotificationsPanelDeps } from "@/components/notifications-panel";
import type { NotificationChannel } from "@/types";

// The panel takes its endpoints and toast surface as an injected `deps` bag, so the
// test hands it fakes rather than replacing the modules underneath it.
function stubDeps(): NotificationsPanelDeps {
  return {
    getNotificationsSettings: vi.fn<NotificationsPanelDeps["getNotificationsSettings"]>(),
    setNotificationsEnabled: vi.fn<NotificationsPanelDeps["setNotificationsEnabled"]>(),
    createNotificationChannel: vi.fn<NotificationsPanelDeps["createNotificationChannel"]>(),
    updateNotificationChannel: vi.fn<NotificationsPanelDeps["updateNotificationChannel"]>(),
    deleteNotificationChannel: vi.fn<NotificationsPanelDeps["deleteNotificationChannel"]>(),
    testNotificationChannel: vi.fn<NotificationsPanelDeps["testNotificationChannel"]>(),
    listNotificationDeliveries: vi.fn<NotificationsPanelDeps["listNotificationDeliveries"]>(),
    listPrinters: vi.fn<NotificationsPanelDeps["listPrinters"]>(),
    toast: {
      error: vi.fn<NotificationsPanelDeps["toast"]["error"]>(),
      success: vi.fn<NotificationsPanelDeps["toast"]["success"]>(),
      warning: vi.fn<NotificationsPanelDeps["toast"]["warning"]>(),
    },
  };
}

let deps = stubDeps();

function channel(over: Partial<NotificationChannel> = {}): NotificationChannel {
  return {
    id: 1,
    name: "Discord alerts",
    target: "discord",
    enabled: true,
    config: { url: "********" },
    config_flags: { has_url: true },
    events: ["print_completed", "print_failed"],
    printer_ids: null,
    last_status: "sent",
    last_error: null,
    last_delivered_at: null,
    consecutive_failures: 0,
    ...over,
  };
}

function mockSettings(enabled: boolean, channels: NotificationChannel[]) {
  vi.mocked(deps.getNotificationsSettings).mockResolvedValue({ enabled, channels });
  vi.mocked(deps.listPrinters).mockResolvedValue([]);
  vi.mocked(deps.listNotificationDeliveries).mockResolvedValue([]);
}

beforeEach(() => {
  deps = stubDeps();
});

describe("NotificationsPanel", () => {
  it("hides channel management from non-admins", async () => {
    mockSettings(false, []);
    render(<NotificationsPanel canEdit={false} deps={deps} />);
    expect(await screen.findByText(/only an administrator can manage/i)).toBeInTheDocument();
    expect(screen.queryByText(/add channel/i)).not.toBeInTheDocument();
  });

  it("lists channels with their subscribed events", async () => {
    mockSettings(true, [channel()]);
    render(<NotificationsPanel canEdit deps={deps} />);
    expect(await screen.findByText("Discord alerts")).toBeInTheDocument();
    expect(screen.getByText(/Print completed, Print failed/)).toBeInTheDocument();
    expect(screen.getByText(/all printers/i)).toBeInTheDocument();
  });

  it("sends a test and reports success", async () => {
    mockSettings(true, [channel()]);
    vi.mocked(deps.testNotificationChannel).mockResolvedValue({ ok: true, error: null });
    render(<NotificationsPanel canEdit deps={deps} />);
    await screen.findByText("Discord alerts");

    const testBtn = screen.getByTitle(/send a test notification/i);
    await userEvent.click(testBtn);

    await waitFor(() => expect(deps.testNotificationChannel).toHaveBeenCalledWith(1));
    expect(deps.toast.success).toHaveBeenCalled();
  });

  it("flags an auto-disabled channel distinctly", async () => {
    mockSettings(true, [
      channel({
        enabled: false,
        last_status: "failed",
        consecutive_failures: 10,
        last_error: "auto-disabled after 10 consecutive failures: HTTP 500",
      }),
    ]);
    render(<NotificationsPanel canEdit deps={deps} />);
    await screen.findByText("Discord alerts");
    expect(screen.getByText(/auto-disabled/i)).toBeInTheDocument();
  });

  it("reveals the create form on Add channel", async () => {
    mockSettings(true, []);
    render(<NotificationsPanel canEdit deps={deps} />);
    const addBtn = await screen.findByText(/add channel/i);
    await userEvent.click(addBtn);
    expect(screen.getByText(/^Events$/)).toBeInTheDocument();
    expect(screen.getByText(/^Type$/)).toBeInTheDocument();
  });
});
