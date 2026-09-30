"use client";

import { useEffect, useRef } from "react";

import { getModel } from "@/lib/api";
import { followModel } from "@/lib/events";
import type { DerivativeState, ModelRead } from "@/types";

/** States after which a Model's shown values (thumbnail, metadata) change. */
const SETTLED: ReadonlySet<DerivativeState> = new Set(["ready", "skipped", "failed"]);

/**
 * Keep an open Model current while its derivatives are produced in the
 * background: a freshly uploaded Artifact shows a placeholder, and the
 * thumbnail and metadata appear once a worker derives them, without a reload.
 *
 * Refetches through the authorized Model read on a settled derivative or a
 * `resync`; a notice never carries the data itself.
 */
export function useDerivativeRefresh(
  modelId: number,
  onModel: (model: ModelRead) => void,
  load: (id: number) => Promise<ModelRead> = getModel,
): void {
  // The latest callbacks, so a re-render does not resubscribe the channel.
  const latest = useRef({ onModel, load });
  useEffect(() => {
    latest.current = { onModel, load };
  });

  useEffect(() => {
    let alive = true;
    const stop = followModel(modelId, (notice) => {
      if (notice.type === "derivative" && !SETTLED.has(notice.state)) return;
      latest.current
        .load(modelId)
        .then((model) => {
          if (alive) latest.current.onModel(model);
        })
        .catch(() => {
          // The next notice (or the user's next action) refetches.
        });
    });
    return () => {
      alive = false;
      stop();
    };
  }, [modelId]);
}
