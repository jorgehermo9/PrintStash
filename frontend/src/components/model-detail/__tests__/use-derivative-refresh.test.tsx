/**
 * An open Model picks up the thumbnail and metadata a worker derives after
 * upload, without a reload. It refetches on a derivative that settled (ready,
 * skipped or failed) or on a `resync` after a reconnect, never on a derivative
 * that merely started, which would refetch the whole Model for nothing.
 */
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useDerivativeRefresh } from "@/components/model-detail/use-derivative-refresh";
import { setEventSocketFactory, type EventSocket } from "@/lib/events";
import type { ModelRead } from "@/types";

class FakeSocket implements EventSocket {
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  send = vi.fn<(data: string) => void>();
  close = vi.fn<() => void>();
}

let socket: FakeSocket;
// SAFETY: the hook only forwards the loaded Model to its callback; no field is read.
const MODEL = { id: 3 } as ModelRead;

async function subscribed(): Promise<void> {
  await waitFor(() => expect(socket.onmessage).not.toBeNull());
  socket.onopen?.();
}

function send(frame: { type: string; model_id?: number; state?: string }): void {
  socket.onmessage?.({ data: JSON.stringify({ file_id: 1, kind: "thumbnail", ...frame }) });
}

beforeEach(() => {
  socket = new FakeSocket();
  setEventSocketFactory(async () => socket);
});

describe("useDerivativeRefresh", () => {
  it("follows the Model's channel", async () => {
    renderHook(() => useDerivativeRefresh(3, vi.fn<(model: ModelRead) => void>()));

    await subscribed();

    expect(socket.send).toHaveBeenCalledWith(JSON.stringify({ subscribe: "model:3" }));
  });

  it.each(["ready", "skipped", "failed"])("refetches when a derivative is %s", async (state) => {
    const onModel = vi.fn<(model: ModelRead) => void>();
    const load = vi.fn<(id: number) => Promise<ModelRead>>().mockResolvedValue(MODEL);
    renderHook(() => useDerivativeRefresh(3, onModel, load));
    await subscribed();

    send({ type: "derivative", model_id: 3, state });

    await waitFor(() => expect(onModel).toHaveBeenCalledWith(MODEL));
    expect(load).toHaveBeenCalledWith(3);
  });

  it("does not refetch for a derivative that only started", async () => {
    const load = vi.fn<(id: number) => Promise<ModelRead>>().mockResolvedValue(MODEL);
    renderHook(() => useDerivativeRefresh(3, vi.fn<(model: ModelRead) => void>(), load));
    await subscribed();

    send({ type: "derivative", model_id: 3, state: "running" });

    expect(load).not.toHaveBeenCalled();
  });

  it("refetches after a resync", async () => {
    const load = vi.fn<(id: number) => Promise<ModelRead>>().mockResolvedValue(MODEL);
    renderHook(() => useDerivativeRefresh(3, vi.fn<(model: ModelRead) => void>(), load));
    await subscribed();

    send({ type: "resync" });

    await waitFor(() => expect(load).toHaveBeenCalledTimes(1));
  });

  it("stops following when the view closes", async () => {
    const { unmount } = renderHook(() =>
      useDerivativeRefresh(3, vi.fn<(model: ModelRead) => void>()),
    );
    await subscribed();

    unmount();

    // It was the only listener, so the socket closes (and the server drops
    // every subscription the connection held).
    expect(socket.close).toHaveBeenCalled();
  });
});
