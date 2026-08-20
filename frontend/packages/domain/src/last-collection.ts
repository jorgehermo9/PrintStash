export const LAST_COLLECTION_STORAGE_KEY = "printstash.last.collection";
export const LAST_VIEW_STORAGE_KEY = "printstash.last.view";

export function rememberLastCollection(path: string | null): void {
  if (typeof window === "undefined") return;
  try {
    if (path) window.localStorage.setItem(LAST_COLLECTION_STORAGE_KEY, path);
    else window.localStorage.removeItem(LAST_COLLECTION_STORAGE_KEY);
  } catch {
    // Best-effort context restoration when browser storage is unavailable.
  }
}

export function readLastCollection(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(LAST_COLLECTION_STORAGE_KEY) || null;
  } catch {
    return null;
  }
}

export function rememberLastView(view: "models" | "docs"): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(LAST_VIEW_STORAGE_KEY, view);
  } catch {
    // Best-effort context restoration when browser storage is unavailable.
  }
}

export function readLastView(): "models" | "docs" {
  if (typeof window === "undefined") return "models";
  try {
    return window.localStorage.getItem(LAST_VIEW_STORAGE_KEY) === "docs" ? "docs" : "models";
  } catch {
    return "models";
  }
}

export function lastVaultHref(): string {
  const parts: string[] = [];
  const path = readLastCollection();
  if (path) parts.push(`c=${encodeURIComponent(path)}`);
  if (readLastView() === "docs") parts.push("v=docs");
  return parts.length ? `/?${parts.join("&")}` : "/";
}
