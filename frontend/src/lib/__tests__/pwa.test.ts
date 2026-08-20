import { registerPwa } from "@/lib/pwa";
import { readFileSync } from "node:fs";
import { expect, it, vi } from "vitest";

/** The only message `registerPwa` posts to a waiting worker. */
type SkipWaitingMessage = { type: "SKIP_WAITING" };

/** The slice of `ServiceWorkerRegistration` that `registerPwa` actually touches. */
type StubRegistration = {
  update: () => Promise<void>;
  waiting: { postMessage: (message: SkipWaitingMessage) => void };
};

type RegisterStub = (scriptURL: string, options?: RegistrationOptions) => Promise<StubRegistration>;

it("registers and checks the production service worker without HTTP cache", async () => {
  const update = vi.fn<StubRegistration["update"]>().mockResolvedValue(undefined);
  const waiting = { postMessage: vi.fn<StubRegistration["waiting"]["postMessage"]>() };
  const register = vi.fn<RegisterStub>().mockResolvedValue({ update, waiting });
  Object.defineProperty(navigator, "serviceWorker", {
    configurable: true,
    value: { register },
  });

  registerPwa(true);
  window.dispatchEvent(new Event("load"));
  await vi.waitFor(() => expect(update).toHaveBeenCalled());

  expect(register).toHaveBeenCalledWith("/sw.js", {
    scope: "/",
    updateViaCache: "none",
  });
  expect(waiting.postMessage).toHaveBeenCalledWith({ type: "SKIP_WAITING" });
});

it("does not register when disabled", () => {
  const register = vi.fn<RegisterStub>();
  Object.defineProperty(navigator, "serviceWorker", {
    configurable: true,
    value: { register },
  });

  registerPwa(false);
  window.dispatchEvent(new Event("load"));
  expect(register).not.toHaveBeenCalled();
});

it("uses versioned caches, offline navigation fallback, and revalidation", () => {
  const source = readFileSync(`${process.cwd()}/public/sw.js`, "utf8");

  expect(source).toContain('const CACHE = "printstash-shell-v2"');
  expect(source).toContain('caches.match("/offline.html")');
  expect(source).toContain("event.waitUntil(network.catch");
  expect(source).toContain('event.data?.type === "SKIP_WAITING"');
});
