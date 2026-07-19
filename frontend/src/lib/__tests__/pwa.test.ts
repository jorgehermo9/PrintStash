import { registerPwa } from "@/lib/pwa";
import { readFileSync } from "node:fs";
import { expect, it, vi } from "vitest";

it("registers and checks the production service worker without HTTP cache", async () => {
  const update = vi.fn().mockResolvedValue(undefined);
  const waiting = { postMessage: vi.fn() };
  const register = vi.fn().mockResolvedValue({ update, waiting });
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
  const register = vi.fn();
  Object.defineProperty(navigator, "serviceWorker", {
    configurable: true,
    value: { register },
  });

  registerPwa(false);
  window.dispatchEvent(new Event("load"));
  expect(register).not.toHaveBeenCalled();
});

it("uses versioned caches, offline navigation fallback, and revalidation", () => {
  const source = readFileSync(
    `${process.cwd()}/public/sw.js`,
    "utf8",
  );

  expect(source).toContain('const CACHE = "printstash-shell-v2"');
  expect(source).toContain('caches.match("/offline.html")');
  expect(source).toContain("event.waitUntil(network.catch");
  expect(source).toContain('event.data?.type === "SKIP_WAITING"');
});
