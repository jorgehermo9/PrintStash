import type { CollectionRead } from "@/types";

/** Follow collection ids so labels never expose the URL-safe path segments. */
export function collectionDisplayPath(
  collections: CollectionRead[],
  path: string | null | undefined,
): string | null {
  if (!path) return null;
  const byPath = collections.find((collection) => collection.path === path);
  if (!byPath) return null;
  const byId = new Map(collections.map((collection) => [collection.id, collection]));
  const names: string[] = [];
  const seen = new Set<number>();
  let current: CollectionRead | undefined = byPath;
  while (current && !seen.has(current.id)) {
    seen.add(current.id);
    names.unshift(current.name);
    current = current.parent_id === null ? undefined : byId.get(current.parent_id);
  }
  return names.join("/");
}
