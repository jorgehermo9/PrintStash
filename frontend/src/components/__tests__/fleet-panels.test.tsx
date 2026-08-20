import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  FleetMaintenancePanel,
  FleetQueuePanel,
  type FleetMaintenanceDeps,
  type FleetQueueDeps,
} from "@/components/fleet-panels";
import type {
  FleetSummary,
  MaintenanceLog,
  MaintenanceWindow,
  PrinterRead,
  PrintJobRead,
} from "@/types";

// Both panels take their collaborators through an optional `deps` prop, so the
// fleet API calls and the two query hooks are replaced by passing stubs in.
// Toasts are left as the real thing — sonner is happy without a mounted
// `<Toaster />` and nothing here asserts on them.
const cancelJob = vi.fn<FleetQueueDeps["cancelJob"]>();
const updateJob = vi.fn<FleetQueueDeps["updateJob"]>();
const retryJob = vi.fn<FleetQueueDeps["retryJob"]>();
const decideOperatorGate = vi.fn<FleetQueueDeps["decideOperatorGate"]>();
const useQueue = vi.fn<FleetQueueDeps["useQueue"]>();
const useSummary = vi.fn<FleetQueueDeps["useSummary"]>();

const queueDeps: FleetQueueDeps = {
  useQueue,
  useSummary,
  cancelJob,
  updateJob,
  retryJob,
  decideOperatorGate,
};

const listWindows = vi.fn<FleetMaintenanceDeps["listWindows"]>();
const listLog = vi.fn<FleetMaintenanceDeps["listLog"]>();
const createWindow = vi.fn<FleetMaintenanceDeps["createWindow"]>();
const createLog = vi.fn<FleetMaintenanceDeps["createLog"]>();
const deleteWindow = vi.fn<FleetMaintenanceDeps["deleteWindow"]>();
const deleteLog = vi.fn<FleetMaintenanceDeps["deleteLog"]>();
const updateRouting = vi.fn<FleetMaintenanceDeps["updateRouting"]>();

const maintenanceDeps: FleetMaintenanceDeps = {
  listWindows,
  listLog,
  createWindow,
  createLog,
  deleteWindow,
  deleteLog,
  updateRouting,
};

function makePrinter(overrides: Partial<PrinterRead> = {}): PrinterRead {
  return {
    id: 1,
    name: "Voron 2.4",
    provider: "moonraker",
    moonraker_url: "http://10.0.0.1:7125",
    has_api_key: false,
    access: { role: "admin", can_view: true, can_print: true, can_control: true, can_admin: true },
    capabilities: {
      can_start: true,
      can_pause: true,
      can_resume: true,
      can_cancel: true,
      can_live_status: true,
      can_upload: true,
      can_list_files: true,
      can_send_gcode: true,
      can_measure_consumption: true,
      support_level: "stable",
      support_notes: [],
      unsupported_actions: [],
    },
    notes: null,
    group: null,
    is_default: false,
    drain_mode: false,
    drain_reason: null,
    drain_updated_at: null,
    status: "ready",
    last_seen_at: null,
    last_error: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function makeJob(overrides: Partial<PrintJobRead> = {}): PrintJobRead {
  return {
    id: 1,
    printer_id: 1,
    file_id: 10,
    model_id: 1,
    remote_filename: "bracket.gcode",
    state: "queued",
    progress: 0,
    source: "vault",
    external_display_name: null,
    external_task_id: null,
    external_subtask_id: null,
    external_project_id: null,
    external_profile_id: null,
    external_gcode_file: null,
    external_plate_index: null,
    external_current_layer: null,
    external_total_layers: null,
    external_nozzle_diameter: null,
    artifact_evidence: "vault",
    artifact_capture_error: null,
    error: null,
    routing_strategy: "least_busy",
    queue_position: 1,
    provider_job_id: null,
    blocked_reason: null,
    dispatch_claimed_at: null,
    dispatch_attempts: 0,
    retryable: false,
    requested_by: null,
    spool_id: null,
    spool_name: null,
    started_at: null,
    finished_at: null,
    created_at: "2026-07-15T00:00:00Z",
    updated_at: "2026-07-15T00:00:00Z",
    ...overrides,
  };
}

function makeSummary(overrides: Partial<FleetSummary> = {}): FleetSummary {
  return {
    total_printers: 0,
    queued_jobs: 0,
    active_jobs: 0,
    draining_printers: 0,
    maintenance_printers: 0,
    attention_jobs: 0,
    ...overrides,
  };
}

function makeWindow(overrides: Partial<MaintenanceWindow> = {}): MaintenanceWindow {
  return {
    id: 1,
    printer_id: 1,
    starts_at: "2026-08-01T09:00:00Z",
    ends_at: "2026-08-01T11:00:00Z",
    reason: "Nozzle swap",
    created_at: "2026-07-15T00:00:00Z",
    updated_at: "2026-07-15T00:00:00Z",
    ...overrides,
  };
}

function makeLog(overrides: Partial<MaintenanceLog> = {}): MaintenanceLog {
  return {
    id: 1,
    printer_id: 1,
    performed_at: "2026-07-15T00:00:00Z",
    category: "belt",
    note: "Tensioned X belt",
    counter_value: null,
    counter_unit: null,
    created_at: "2026-07-15T00:00:00Z",
    updated_at: "2026-07-15T00:00:00Z",
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  useQueue.mockReturnValue({ data: [], isLoading: false, refetch: vi.fn<() => void>() });
  useSummary.mockReturnValue({ data: makeSummary(), refetch: vi.fn<() => void>() });
  listWindows.mockResolvedValue([]);
  listLog.mockResolvedValue([]);
});

describe("FleetQueuePanel", () => {
  it("renders queued, active, and recent jobs grouped into sections", () => {
    useQueue.mockReturnValue({
      data: [
        makeJob({ id: 1, state: "queued", queue_position: 1, remote_filename: "first.gcode" }),
        makeJob({ id: 2, state: "queued", queue_position: 2, remote_filename: "second.gcode" }),
        makeJob({ id: 3, state: "printing", remote_filename: "active.gcode" }),
        makeJob({ id: 4, state: "completed", remote_filename: "done.gcode" }),
      ],
      isLoading: false,
      refetch: vi.fn<() => void>(),
    });
    render(<FleetQueuePanel printers={[makePrinter()]} deps={queueDeps} />);

    expect(screen.getByRole("heading", { name: "Queued" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Active" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Recent" })).toBeInTheDocument();
    expect(screen.getByText("first.gcode")).toBeInTheDocument();
    expect(screen.getByText("active.gcode")).toBeInTheDocument();
    expect(screen.getByText("done.gcode")).toBeInTheDocument();
  });

  it("shows the empty state when there are no jobs", () => {
    render(<FleetQueuePanel printers={[]} deps={queueDeps} />);
    expect(screen.getByText("No queued print jobs")).toBeInTheDocument();
  });

  it("moving a queued job down calls updateFleetJob with the new queue position", async () => {
    updateJob.mockResolvedValue(makeJob());
    useQueue.mockReturnValue({
      data: [
        makeJob({ id: 1, state: "queued", queue_position: 1, remote_filename: "first.gcode" }),
        makeJob({ id: 2, state: "queued", queue_position: 2, remote_filename: "second.gcode" }),
      ],
      isLoading: false,
      refetch: vi.fn<() => void>(),
    });
    render(<FleetQueuePanel printers={[makePrinter()]} deps={queueDeps} />);

    await userEvent.click(screen.getByRole("button", { name: "Move first.gcode down" }));

    await waitFor(() => expect(updateJob).toHaveBeenCalledWith(1, { queue_position: 2 }));
  });

  it("moving a queued job up calls updateFleetJob with the new queue position", async () => {
    updateJob.mockResolvedValue(makeJob());
    useQueue.mockReturnValue({
      data: [
        makeJob({ id: 1, state: "queued", queue_position: 1, remote_filename: "first.gcode" }),
        makeJob({ id: 2, state: "queued", queue_position: 2, remote_filename: "second.gcode" }),
      ],
      isLoading: false,
      refetch: vi.fn<() => void>(),
    });
    render(<FleetQueuePanel printers={[makePrinter()]} deps={queueDeps} />);

    await userEvent.click(screen.getByRole("button", { name: "Move second.gcode up" }));

    await waitFor(() => expect(updateJob).toHaveBeenCalledWith(2, { queue_position: 1 }));
  });

  it("cancelling a queued job confirms then calls cancelFleetJob", async () => {
    cancelJob.mockResolvedValue(undefined);
    useQueue.mockReturnValue({
      data: [
        makeJob({ id: 5, state: "queued", queue_position: 1, remote_filename: "cancel-me.gcode" }),
      ],
      isLoading: false,
      refetch: vi.fn<() => void>(),
    });
    render(<FleetQueuePanel printers={[makePrinter()]} deps={queueDeps} />);

    await userEvent.click(screen.getByRole("button", { name: "Cancel cancel-me.gcode" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel job" }));

    await waitFor(() => expect(cancelJob).toHaveBeenCalledWith(5));
  });

  it("retrying a failed retryable job calls retryFleetJob", async () => {
    retryJob.mockResolvedValue(makeJob({ id: 9, state: "queued" }));
    useQueue.mockReturnValue({
      data: [
        makeJob({ id: 9, state: "failed", retryable: true, remote_filename: "retry-me.gcode" }),
      ],
      isLoading: false,
      refetch: vi.fn<() => void>(),
    });
    render(<FleetQueuePanel printers={[makePrinter()]} deps={queueDeps} />);

    await userEvent.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(retryJob).toHaveBeenCalledWith(9));
  });
});

describe("FleetMaintenancePanel", () => {
  it("shows the empty state with no printers", () => {
    render(
      <FleetMaintenancePanel
        printers={[]}
        onPrintersChanged={vi.fn<() => void>()}
        deps={maintenanceDeps}
      />,
    );
    expect(screen.getByText("No printers to maintain")).toBeInTheDocument();
  });

  it("toggling soft drain calls updatePrinterRouting with drain_mode true", async () => {
    updateRouting.mockResolvedValue({});
    const onPrintersChanged = vi.fn<() => void>();
    render(
      <FleetMaintenancePanel
        printers={[makePrinter({ drain_mode: false })]}
        onPrintersChanged={onPrintersChanged}
        deps={maintenanceDeps}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Soft drain" }));

    await waitFor(() =>
      expect(updateRouting).toHaveBeenCalledWith(1, {
        drain_mode: true,
        drain_reason: "Manual soft drain",
      }),
    );
    expect(onPrintersChanged).toHaveBeenCalled();
  });

  it("resuming a drained printer calls updatePrinterRouting with drain_mode false", async () => {
    updateRouting.mockResolvedValue({});
    render(
      <FleetMaintenancePanel
        printers={[makePrinter({ drain_mode: true, drain_reason: "Nozzle swap" })]}
        onPrintersChanged={vi.fn<() => void>()}
        deps={maintenanceDeps}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Resume routing" }));

    await waitFor(() =>
      expect(updateRouting).toHaveBeenCalledWith(1, {
        drain_mode: false,
        drain_reason: null,
      }),
    );
  });

  it("scheduling a maintenance window calls createMaintenanceWindow with the entered fields", async () => {
    createWindow.mockResolvedValue(makeWindow());
    render(
      <FleetMaintenancePanel
        printers={[makePrinter()]}
        onPrintersChanged={vi.fn<() => void>()}
        deps={maintenanceDeps}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Schedule" }));
    const dialog = screen.getByRole("dialog", { name: "Schedule maintenance" });
    await userEvent.type(within(dialog).getByLabelText("Starts"), "2026-08-01T09:00");
    await userEvent.type(within(dialog).getByLabelText("Ends"), "2026-08-01T11:00");
    await userEvent.type(within(dialog).getByLabelText("Reason"), "Nozzle swap");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(createWindow).toHaveBeenCalledWith(
        1,
        expect.objectContaining({ reason: "Nozzle swap" }),
      ),
    );
  });

  it("logging maintenance calls createMaintenanceLog with the category and note", async () => {
    createLog.mockResolvedValue(makeLog());
    render(
      <FleetMaintenancePanel
        printers={[makePrinter()]}
        onPrintersChanged={vi.fn<() => void>()}
        deps={maintenanceDeps}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Log" }));
    const dialog = screen.getByRole("dialog", { name: "Log maintenance" });
    await userEvent.clear(within(dialog).getByLabelText("Category"));
    await userEvent.type(within(dialog).getByLabelText("Category"), "belt");
    await userEvent.type(within(dialog).getByLabelText("Note"), "Tensioned X belt");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(createLog).toHaveBeenCalledWith(1, {
        category: "belt",
        note: "Tensioned X belt",
      }),
    );
  });
});
