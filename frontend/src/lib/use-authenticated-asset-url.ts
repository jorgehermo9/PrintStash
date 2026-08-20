"use client";

import { useEffect, useState } from "react";

import { getCachedAssetUrl, peekCachedAssetUrl } from "@/lib/asset-cache";

export function useAuthenticatedAssetUrl(path: string | null | undefined): string | null {
  // Seed from the session cache so a thumbnail that's already been fetched shows
  // instantly on re-mount (re-scroll, pagination, folder revisit) instead of
  // flashing empty and fading in again.
  const [url, setUrl] = useState<string | null>(() => (path ? peekCachedAssetUrl(path) : null));

  useEffect(() => {
    let alive = true;
    if (!path) {
      setUrl(null);
      return;
    }
    const cached = peekCachedAssetUrl(path);
    if (cached) {
      setUrl(cached);
      return;
    }
    setUrl(null);
    getCachedAssetUrl(path)
      .then((resolved) => {
        if (alive) setUrl(resolved);
      })
      .catch(() => {
        if (alive) setUrl(null);
      });

    // Object URLs are owned by the cache (shared across components and reused
    // across mounts), so we no longer revoke on unmount — the cache evicts.
    return () => {
      alive = false;
    };
  }, [path]);

  return url;
}
