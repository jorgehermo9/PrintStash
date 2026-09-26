"use client";

import { AlertTriangle } from "lucide-react";

import { useI18n } from "@/lib/i18n";
import type { StorageHealthRead } from "@/types";

const LAYOUT_GUIDE =
  "https://github.com/xiao-villamor/PrintStash/blob/main/docs/deployment.md#hard-linked-imports";

/**
 * Warns that imports copy every file instead of hard-linking it.
 *
 * The backend probes at startup whether staging can hard-link into the library.
 * It cannot when they sit on different mounts, which is easy to cause without
 * touching a setting: a second volume mapped onto a subfolder of `/data` does
 * it. Imports keep working, only slower and needing twice the space, so
 * nothing else would ever tell the operator.
 */
export function ImportCopyWarning({ storageHealth }: { storageHealth?: StorageHealthRead | null }) {
  const { t } = useI18n();
  if (storageHealth?.diagnostics?.staged_hardlink !== false) return null;
  return (
    <div
      role="status"
      className="flex items-start gap-3 rounded-lg border border-warning/30 bg-warning/10 p-3"
    >
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" aria-hidden />
      <div className="min-w-0">
        <p className="text-sm font-semibold text-foreground">
          {t("settings.storageImportsCopyTitle")}
        </p>
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
          {t("settings.storageImportsCopyDescription")}
        </p>
        <a
          className="mt-2 inline-block text-xs text-primary underline underline-offset-4"
          href={LAYOUT_GUIDE}
          target="_blank"
          rel="noreferrer"
        >
          {t("settings.storageImportsCopyGuide")}
        </a>
      </div>
    </div>
  );
}
