import { afterEach, describe, expect, it, vi } from "vitest";

import { createCompletionChainedPoller } from "@/lib/completion-chained-polling";

type PollResult = "active" | "complete";

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((next) => {
    resolve = next;
  });
  return { promise, resolve };
}

async function flushPromises(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}

afterEach(() => {
  vi.useRealTimers();
});

describe("createCompletionChainedPoller", () => {
  it("keeps one request in flight and continues after each active result", async () => {
    vi.useFakeTimers();
    const first = deferred<PollResult>();
    const second = deferred<PollResult>();
    const request = vi
      .fn<(signal: AbortSignal) => Promise<PollResult>>()
      .mockImplementationOnce((signal) => {
        expect(signal.aborted).toBe(false);
        return first.promise;
      })
      .mockImplementationOnce((signal) => {
        expect(signal.aborted).toBe(false);
        return second.promise;
      });
    const results: PollResult[] = [];
    const poller = createCompletionChainedPoller({
      request,
      intervalMs: 1_500,
      shouldContinue: (result) => result === "active",
      onResult: (result) => results.push(result),
    });

    poller.refresh();
    expect(request).toHaveBeenCalledTimes(1);
    poller.refresh();
    expect(request).toHaveBeenCalledTimes(1);

    first.resolve("active");
    await flushPromises();
    expect(request).toHaveBeenCalledTimes(2);
    second.resolve("complete");
    await flushPromises();

    expect(results).toEqual(["active", "complete"]);
    await vi.advanceTimersByTimeAsync(3_000);
    expect(request).toHaveBeenCalledTimes(2);
  });

  it("suppresses stale responses after stop/restart without overlapping requests", async () => {
    vi.useFakeTimers();
    const first = deferred<PollResult>();
    const second = deferred<PollResult>();
    const request = vi
      .fn<(signal: AbortSignal) => Promise<PollResult>>()
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    const results: PollResult[] = [];
    const poller = createCompletionChainedPoller({
      request,
      intervalMs: 1_500,
      shouldContinue: (result) => result === "active",
      onResult: (result) => results.push(result),
    });

    poller.refresh();
    const [signal] = request.mock.calls[0];
    poller.stop();
    poller.refresh();
    expect(signal.aborted).toBe(true);
    expect(request).toHaveBeenCalledTimes(1);

    first.resolve("active");
    await flushPromises();
    expect(results).toEqual([]);
    expect(request).toHaveBeenCalledTimes(2);

    second.resolve("complete");
    await flushPromises();
    expect(results).toEqual(["complete"]);
  });

  it("continues the chain after a transient request error", async () => {
    vi.useFakeTimers();
    const request = vi
      .fn<(signal: AbortSignal) => Promise<PollResult>>()
      .mockRejectedValueOnce(new Error("temporary"))
      .mockResolvedValueOnce("complete");
    const onError = vi.fn<(error: Error) => void>();
    const onResult = vi.fn<(result: PollResult) => void>();
    const poller = createCompletionChainedPoller({
      request,
      intervalMs: 1_500,
      shouldContinue: (result) => result === "active",
      onResult,
      onError,
    });

    poller.refresh();
    await flushPromises();
    expect(onError).toHaveBeenCalledOnce();
    expect(request).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(1_500);
    expect(request).toHaveBeenCalledTimes(2);
    await flushPromises();
    expect(onResult).toHaveBeenCalledWith("complete");
  });

  it("aborts in-flight work on stop and stops scheduling after a terminal result", async () => {
    vi.useFakeTimers();
    const pending = deferred<PollResult>();
    const request = vi
      .fn<(signal: AbortSignal) => Promise<PollResult>>()
      .mockReturnValue(pending.promise);
    const onResult = vi.fn<(result: PollResult) => void>();
    const poller = createCompletionChainedPoller({
      request,
      intervalMs: 1_500,
      shouldContinue: (result) => result === "active",
      onResult,
    });

    poller.refresh();
    const [signal] = request.mock.calls[0];
    poller.stop();
    expect(signal.aborted).toBe(true);
    pending.resolve("active");
    await flushPromises();
    expect(onResult).not.toHaveBeenCalled();

    const terminal = Promise.resolve<PollResult>("complete");
    request.mockReturnValueOnce(terminal);
    poller.refresh();
    await flushPromises();
    await vi.advanceTimersByTimeAsync(3_000);
    expect(onResult).toHaveBeenCalledTimes(1);
    expect(request).toHaveBeenCalledTimes(2);
  });
});
