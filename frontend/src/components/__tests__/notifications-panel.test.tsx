import "@testing-library/jest-dom/vitest";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { NotificationsPanel } from "@/components/notifications-panel";
import type { NotificationChannel } from "@/types";

// Mock the API surface the panel consumes.
vi.mock("@/lib/api", () => ({
  getNotificationsSettings: vi.fn(),
  setNotificationsEnabled: vi.fn(),
  createNotificationChannel: vi.fn(),
  updateNotificationChannel: vi.fn(),
  deleteNotificationChannel: vi.fn(),
  testNotificationChannel: vi.fn(),
  listNotificationDeliveries: vi.fn(),
  listPrinters: vi.fn(),
}));
vi.mock("@/lib/toast", () => ({
  toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

import * as api from "@/lib/api";
import { toast } from "@/lib/toast";

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
  (api.getNotificationsSettings as ReturnType<typeof vi.fn>).mockResolvedValue({
    enabled,
    channels,
  });
  (api.listPrinters as ReturnType<typeof vi.fn>).mockResolvedValue([]);
  (api.listNotificationDeliveries as ReturnType<typeof vi.fn>).mockResolvedValue([]);
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("NotificationsPanel", () => {
  it("hides channel management from non-admins", async () => {
    mockSettings(false, []);
    render(<NotificationsPanel canEdit={false} />);
    expect(await screen.findByText(/only an administrator can manage/i)).toBeInTheDocument();
    expect(screen.queryByText(/add channel/i)).not.toBeInTheDocument();
  });

  it("lists channels with their subscribed events", async () => {
    mockSettings(true, [channel()]);
    render(<NotificationsPanel canEdit />);
    expect(await screen.findByText("Discord alerts")).toBeInTheDocument();
    expect(screen.getByText(/Print completed, Print failed/)).toBeInTheDocument();
    expect(screen.getByText(/all printers/i)).toBeInTheDocument();
  });

  it("sends a test and reports success", async () => {
    mockSettings(true, [channel()]);
    (api.testNotificationChannel as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      error: null,
    });
    render(<NotificationsPanel canEdit />);
    await screen.findByText("Discord alerts");

    const testBtn = screen.getByTitle(/send a test notification/i);
    await userEvent.click(testBtn);

    await waitFor(() => expect(api.testNotificationChannel).toHaveBeenCalledWith(1));
    expect(toast.success).toHaveBeenCalled();
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
    render(<NotificationsPanel canEdit />);
    await screen.findByText("Discord alerts");
    expect(screen.getByText(/auto-disabled/i)).toBeInTheDocument();
  });

  it("reveals the create form on Add channel", async () => {
    mockSettings(true, []);
    render(<NotificationsPanel canEdit />);
    const addBtn = await screen.findByText(/add channel/i);
    await userEvent.click(addBtn);
    expect(screen.getByText(/^Events$/)).toBeInTheDocument();
    expect(screen.getByText(/^Type$/)).toBeInTheDocument();
  });
});
