export function registerPwa(enabled = import.meta.env.PROD): void {
  // Feature detection, in order: opted in, running in a DOM, service workers supported.
  if (!enabled || !("window" in globalThis) || !("serviceWorker" in navigator)) return;
  window.addEventListener(
    "load",
    () => {
      void navigator.serviceWorker
        .register("/sw.js", { scope: "/", updateViaCache: "none" })
        .then(async (registration) => {
          if (registration.waiting) {
            registration.waiting.postMessage({ type: "SKIP_WAITING" });
          }
          await registration.update();
        })
        .catch(() => {
          // PWA support is optional; registration failure must never block app boot.
        });
    },
    { once: true },
  );
}
