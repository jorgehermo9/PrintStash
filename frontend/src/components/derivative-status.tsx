"use client";

/**
 * What is still being derived from one Artifact, and what failed.
 *
 * A fresh upload's preview and metadata are produced in the background, so an
 * Artifact row says so rather than looking broken; a failure names its reason
 * and, for an editor, offers a retry. Failures that share a cause share one
 * explanation, phrased as what the reader can do rather than as a code.
 * Everything finished renders nothing.
 * Missing derivatives are unknown, never zero, which is why a pending kind is
 * shown as pending rather than hidden.
 */
import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { listDerivatives, retryDerivative } from "@/lib/api";
import {
  derivativeFailureCause,
  derivativeFailureMessage,
  type DerivativeFailureCause,
} from "@/lib/derivative-failures";
import { followModel } from "@/lib/events";
import { useUiLocale } from "@/lib/i18n";
import { uiText } from "@/lib/locale";
import { toast } from "@/lib/toast";
import type { DerivativeRead, DerivativeState } from "@/types";

export interface DerivativeStatusApi {
  list: (fileId: number) => Promise<DerivativeRead[]>;
  retry: (fileId: number, kind: string) => Promise<DerivativeRead[]>;
}

const DERIVATIVE_API: DerivativeStatusApi = { list: listDerivatives, retry: retryDerivative };

const IN_PROGRESS: ReadonlySet<DerivativeState> = new Set(["pending", "queued", "running"]);

function kindLabel(kind: string): string {
  switch (kind) {
    case "thumbnail":
      return uiText("Preview");
    case "metadata":
      return uiText("Metadata");
    case "viewer_stl":
      return uiText("3D preview");
    case "toolpath":
      return uiText("Toolpath");
    default:
      return kind;
  }
}

export function DerivativeStatus({
  modelId,
  fileId,
  canRetry,
  api = DERIVATIVE_API,
}: {
  modelId: number;
  fileId: number;
  canRetry: boolean;
  api?: DerivativeStatusApi;
}) {
  useUiLocale();
  const [derivatives, setDerivatives] = useState<DerivativeRead[]>([]);
  const [retrying, setRetrying] = useState<string | null>(null);

  const refresh = useCallback(() => {
    api
      .list(fileId)
      .then(setDerivatives)
      .catch(() => {
        // Status is advisory; the Artifact itself is still usable.
      });
  }, [api, fileId]);

  useEffect(() => {
    refresh();
    return followModel(modelId, (notice) => {
      if (
        notice.type === "resync" ||
        notice.type === "derivative_policy" ||
        (notice.type === "derivative" && notice.file_id === fileId)
      )
        refresh();
    });
  }, [modelId, fileId, refresh]);

  async function retry(kind: string) {
    setRetrying(kind);
    try {
      setDerivatives(await api.retry(fileId, kind));
    } catch (error) {
      toast.error(error);
    } finally {
      setRetrying(null);
    }
  }

  const preparing = derivatives.filter((item) => IN_PROGRESS.has(item.state));
  const failed = derivatives.filter((item) => item.state === "failed");
  // Outputs that failed for the same cause share one explanation.
  const failures = new Map<DerivativeFailureCause, DerivativeRead[]>();
  for (const item of failed) {
    const cause = derivativeFailureCause(item.failure_reason);
    failures.set(cause, [...(failures.get(cause) ?? []), item]);
  }
  const disabled = derivatives.some((item) => item.state === "disabled");
  if (preparing.length === 0 && failed.length === 0 && !disabled) return null;

  return (
    <ul className="mt-1.5 space-y-1">
      {disabled && (
        <li role="status" className="text-2xs text-on-surface-variant">
          {uiText("Processing disabled")}
        </li>
      )}
      {preparing.length > 0 && (
        <li role="status" className="flex items-center gap-1.5 text-2xs text-on-surface-variant">
          <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
          {uiText("Preparing {kinds}…", {
            kinds: preparing.map((item) => kindLabel(item.kind)).join(", "),
          })}
        </li>
      )}
      {[...failures].map(([cause, items]) => (
        <li key={cause} className="flex items-start gap-1.5 text-2xs">
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0 text-warning" aria-hidden />
          <div className="min-w-0 space-y-0.5">
            <p className="font-medium text-on-surface">
              {uiText("{kinds} failed", {
                kinds: items.map((item) => kindLabel(item.kind)).join(", "),
              })}
            </p>
            <p className="text-on-surface-variant">{derivativeFailureMessage(cause)}</p>
            {canRetry && items.some((item) => item.retryable) && (
              <div className="flex flex-wrap gap-1">
                {items
                  .filter((item) => item.retryable)
                  .map((item) => (
                    <Button
                      key={item.kind}
                      size="sm"
                      variant="ghost"
                      loading={retrying === item.kind}
                      onClick={() => void retry(item.kind)}
                    >
                      {uiText("Retry {kind}", { kind: kindLabel(item.kind) })}
                    </Button>
                  ))}
              </div>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}
