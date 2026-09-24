/*
 * The events socket every view shares to hear that background work changed.
 *
 * One connection per tab, opened while anything listens and closed when
 * nothing does: a socket per component would multiply server load by the
 * number of mounted views. It reconnects with a bounded backoff, and after a
 * reconnect re-follows every Model a view still shows, because the server
 * forgot the old connection's subscriptions. A frame it does not understand is
 * ignored rather than guessed at; a newer server may send kinds this build
 * predates.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { EventNotice, EventSocket } from "@/lib/events";

type Events = typeof import("@/lib/events");

class FakeSocket implements EventSocket {
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  send = vi.fn<(data: string) => void>();
  close = vi.fn<() => void>();

  open(): void {
    this.onopen?.();
  }

  frame(data: string): void {
    this.onmessage?.({ data });
  }

  drop(): void {
    this.onclose?.();
  }

  sent(): string[] {
    return this.send.mock.calls.map(([data]) => data);
  }
}

let events: Events;
let sockets: FakeSocket[];
const opened = vi.fn<() => Promise<EventSocket>>();

beforeEach(async () => {
  vi.resetModules();
  vi.useFakeTimers();
  sockets = [];
  opened.mockReset();
  opened.mockImplementation(async () => {
    const socket = new FakeSocket();
    sockets.push(socket);
    return socket;
  });
  events = await import("@/lib/events");
  events.setEventSocketFactory(opened);
});

afterEach(() => {
  vi.useRealTimers();
});

/** Let the factory's promise resolve and the socket open. */
async function connected(): Promise<FakeSocket> {
  await vi.advanceTimersByTimeAsync(0);
  const socket = sockets.at(-1)!;
  socket.open();
  return socket;
}

describe("subscribeEvents", () => {
  it("delivers each notice the server sends", async () => {
    const heard = vi.fn<(notice: EventNotice) => void>();
    events.subscribeEvents(heard);
    const socket = await connected();

    socket.frame(
      JSON.stringify({
        type: "job",
        job_id: "j1",
        kind: "ingest.upload",
        state: "running",
        progress: 40,
      }),
    );

    expect(heard).toHaveBeenCalledWith({
      type: "job",
      job_id: "j1",
      kind: "ingest.upload",
      state: "running",
      progress: 40,
    });
  });

  it("shares one connection between listeners", async () => {
    events.subscribeEvents(vi.fn<(notice: EventNotice) => void>());
    events.subscribeEvents(vi.fn<(notice: EventNotice) => void>());
    await connected();

    expect(opened).toHaveBeenCalledTimes(1);
  });

  it("closes the connection when the last listener leaves", async () => {
    const first = events.subscribeEvents(vi.fn<(notice: EventNotice) => void>());
    const second = events.subscribeEvents(vi.fn<(notice: EventNotice) => void>());
    const socket = await connected();

    first();
    expect(socket.close).not.toHaveBeenCalled();
    second();

    expect(socket.close).toHaveBeenCalledTimes(1);
  });

  it.each([
    { label: "an unknown kind", frame: JSON.stringify({ type: "hologram" }) },
    { label: "a frame that is not JSON", frame: "not json" },
  ])("ignores $label", async ({ frame }) => {
    const heard = vi.fn<(notice: EventNotice) => void>();
    events.subscribeEvents(heard);
    const socket = await connected();

    socket.frame(frame);

    expect(heard).not.toHaveBeenCalled();
  });

  it("reconnects after the connection drops", async () => {
    events.subscribeEvents(vi.fn<(notice: EventNotice) => void>());
    const first = await connected();

    first.drop();
    await vi.advanceTimersByTimeAsync(1_000);

    expect(opened).toHaveBeenCalledTimes(2);
  });

  it("backs off while the server stays unreachable", async () => {
    opened.mockRejectedValue(new Error("offline"));
    events.subscribeEvents(vi.fn<(notice: EventNotice) => void>());
    await vi.advanceTimersByTimeAsync(0);
    expect(opened).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(1_000);
    expect(opened).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1_999);
    expect(opened).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1);

    expect(opened).toHaveBeenCalledTimes(3);
  });

  it("stops reconnecting once nobody listens", async () => {
    const stop = events.subscribeEvents(vi.fn<(notice: EventNotice) => void>());
    const socket = await connected();
    socket.drop();

    stop();
    await vi.advanceTimersByTimeAsync(60_000);

    expect(opened).toHaveBeenCalledTimes(1);
  });
});

describe("followModel", () => {
  it("subscribes to the Model's channel once the socket is open", async () => {
    events.followModel(3, vi.fn<(notice: EventNotice) => void>());
    const socket = await connected();

    expect(socket.sent()).toEqual([JSON.stringify({ subscribe: "model:3" })]);
  });

  it("hears only its own Model's derivatives", async () => {
    const heard = vi.fn<(notice: EventNotice) => void>();
    events.followModel(3, heard);
    const socket = await connected();

    socket.frame(
      JSON.stringify({
        type: "derivative",
        model_id: 4,
        file_id: 1,
        kind: "thumbnail",
        state: "ready",
      }),
    );
    socket.frame(
      JSON.stringify({
        type: "derivative",
        model_id: 3,
        file_id: 2,
        kind: "thumbnail",
        state: "ready",
      }),
    );

    expect(heard).toHaveBeenCalledTimes(1);
    expect(heard).toHaveBeenCalledWith(expect.objectContaining({ model_id: 3, file_id: 2 }));
  });

  it("hears a resync", async () => {
    const heard = vi.fn<(notice: EventNotice) => void>();
    events.followModel(3, heard);
    const socket = await connected();

    socket.frame(JSON.stringify({ type: "resync" }));

    expect(heard).toHaveBeenCalledWith({ type: "resync" });
  });

  it("follows the Model again on a new connection", async () => {
    events.followModel(3, vi.fn<(notice: EventNotice) => void>());
    const first = await connected();
    first.drop();
    await vi.advanceTimersByTimeAsync(1_000);

    const second = sockets.at(-1)!;
    second.open();

    expect(second.sent()).toEqual([JSON.stringify({ subscribe: "model:3" })]);
  });

  it("unsubscribes when the last view of the Model leaves", async () => {
    const first = events.followModel(3, vi.fn<(notice: EventNotice) => void>());
    const second = events.followModel(3, vi.fn<(notice: EventNotice) => void>());
    events.subscribeEvents(vi.fn<(notice: EventNotice) => void>());
    const socket = await connected();

    first();
    expect(socket.sent()).not.toContain(JSON.stringify({ unsubscribe: "model:3" }));
    second();

    expect(socket.sent()).toContain(JSON.stringify({ unsubscribe: "model:3" }));
  });
});
