"use client";

import { useCallback, useSyncExternalStore } from "react";

const noopSub = () => () => {};

function getSnapshot(query: string) {
  if (typeof window === "undefined") return false;
  return window.matchMedia(query).matches;
}

function subscribe(onChange: () => void, query: string) {
  if (typeof window === "undefined") return noopSub();
  const mql = window.matchMedia(query);
  mql.addEventListener("change", onChange);
  return () => mql.removeEventListener("change", onChange);
}

export function useMediaQuery(query: string): boolean {
  const subscribeToMql = useCallback(
    (onChange: () => void) => subscribe(onChange, query),
    [query],
  );
  const getSnap = useCallback(() => getSnapshot(query), [query]);
  return useSyncExternalStore(subscribeToMql, getSnap);
}
