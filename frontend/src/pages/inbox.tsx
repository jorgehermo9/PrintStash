import { useEffect, useMemo, useState } from "react";
import {
  CalendarDays,
  CheckCircle2,
  CircleAlert,
  Clock3,
  FileCheck2,
  Files,
  Inbox,
  RefreshCw,
  type LucideIcon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";
import { listPendingImports, retryPendingImport } from "@/lib/api";
import { createCompletionChainedPoller } from "@/lib/completion-chained-polling";
import { Link } from "@/lib/link";
import { useI18n } from "@/lib/i18n";
import { toast } from "@/lib/toast";
import type { InboxItem } from "@/types";

const ACTIVE = new Set(["captured", "resolving", "importing"]);

export interface InboxPageDeps {
  listPendingImports: typeof listPendingImports;
  retryPendingImport: typeof retryPendingImport;
}

const inboxPageDeps: InboxPageDeps = { listPendingImports, retryPendingImport };

function Group({
  id,
  title,
  items,
  locale,
  t,
  retry,
}: {
  id: string;
  title: string;
  items: InboxItem[];
  locale: string;
  t: ReturnType<typeof useI18n>["t"];
  retry: typeof retryPendingImport;
}) {
  if (!items.length) return null;
  const headingId = `inbox-${id}-heading`;
  return (
    <section aria-labelledby={headingId} className="space-y-2">
      <div className="flex items-baseline gap-2">
        <h2 id={headingId} className="text-sm font-semibold text-foreground">
          {title}
        </h2>
        <span aria-hidden="true" className="font-mono text-xs tabular-nums text-muted-foreground">
          {items.length}
        </span>
      </div>
      <ul
        aria-labelledby={headingId}
        className="divide-y divide-border overflow-hidden rounded-lg border bg-card shadow-sm"
      >
        {items.map((item) => {
          const title = capturedTitle(item) || item.source_hostname || t("inbox.pendingImport");
          const provider = providerLabel(item) || t("inbox.sourcePreparing");
          const fileCount = manifestFiles(item).length;
          const StateIcon = stateIcon(item);
          return (
            <li
              key={item.id}
              className="animate-card-in grid min-w-0 gap-3 p-4 sm:grid-cols-[auto_minmax(0,1fr)_auto] sm:items-center sm:gap-4 lg:px-5"
            >
              <div className="flex min-w-0 items-start gap-3 sm:contents">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-muted">
                  <StateIcon className={stateIconClass(item)} aria-hidden="true" />
                </span>
                <div className="min-w-0 flex-1">
                  <h3 className="line-clamp-2 break-words text-sm font-semibold leading-5 text-foreground">
                    {title}
                  </h3>
                  <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                    <span className="font-medium text-foreground/80">{provider}</span>
                    <span className="inline-flex items-center gap-1">
                      <CalendarDays className="h-3.5 w-3.5" aria-hidden="true" />
                      {new Date(item.completed_at || item.created_at).toLocaleDateString(locale)}
                    </span>
                    {fileCount > 0 && (
                      <span className="inline-flex items-center gap-1">
                        <Files className="h-3.5 w-3.5" aria-hidden="true" />
                        {t("inbox.fileSummary", { count: String(fileCount) })}
                      </span>
                    )}
                    {item.results.length > 0 && (
                      <span>
                        {t("inbox.resultSummary", { count: String(item.results.length) })}
                      </span>
                    )}
                  </div>
                </div>
              </div>
              <div className="flex shrink-0 items-center justify-between gap-3 border-t border-border pt-3 sm:justify-end sm:border-0 sm:pt-0">
                <Badge
                  variant={
                    item.state === "completed"
                      ? "success"
                      : item.state === "failed"
                        ? "destructive"
                        : "secondary"
                  }
                >
                  {statusLabel(item, t)}
                </Badge>
                <div className="flex gap-2">
                  {item.state === "failed" && item.retryable && (
                    <Button
                      size="xs"
                      variant="outline"
                      onClick={() =>
                        void retry(item.id)
                          .then(() => toast.success(t("inbox.retryQueued")))
                          .catch(toast.error)
                      }
                    >
                      <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
                      {t("inbox.retry")}
                    </Button>
                  )}
                  {item.state === "completed" && item.resulting_model_id ? (
                    <Button size="xs" variant="outline" asChild>
                      <Link href={`/models/${item.resulting_model_id}`}>
                        {t("inbox.openModel")}
                      </Link>
                    </Button>
                  ) : (
                    <Button size="xs" asChild>
                      <Link href={`/inbox/${item.id}`}>
                        {item.state === "review" ? t("inbox.review") : t("inbox.view")}
                      </Link>
                    </Button>
                  )}
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function capturedTitle(item: InboxItem): string | null {
  if (item.manifest.schema_version === 2) {
    const title = item.manifest.source.fields.title?.value.trim();
    if (title) return title;
  }
  return item.display_title?.trim() || null;
}

function manifestFiles(item: InboxItem) {
  if (item.manifest.kind === "archive") return item.manifest.entries ?? [];
  if (item.manifest.kind === "model_files") return item.manifest.files ?? [];
  return [];
}

function providerLabel(item: InboxItem): string | null {
  const provider =
    item.manifest.schema_version === 2
      ? item.manifest.source.provider
      : item.source_hostname?.replace(/^www\./, "").split(".")[0];
  if (!provider) return null;
  switch (provider.toLowerCase()) {
    case "cults3d":
      return "Cults3D";
    case "makerworld":
      return "MakerWorld";
    case "myminifactory":
      return "MyMiniFactory";
    case "printables":
      return "Printables";
    case "thingiverse":
      return "Thingiverse";
    default:
      return provider.charAt(0).toUpperCase() + provider.slice(1);
  }
}

function stateIcon(item: InboxItem): LucideIcon {
  if (item.state === "completed") return CheckCircle2;
  if (item.state === "failed") return CircleAlert;
  if (item.state === "review") return FileCheck2;
  return Clock3;
}

function stateIconClass(item: InboxItem): string {
  if (item.state === "completed") return "h-4.5 w-4.5 text-success";
  if (item.state === "failed") return "h-4.5 w-4.5 text-destructive";
  if (item.state === "review") return "h-4.5 w-4.5 text-primary";
  return "h-4.5 w-4.5 text-muted-foreground";
}

function statusLabel(item: InboxItem, t: ReturnType<typeof useI18n>["t"]): string {
  if (item.completion === "partial") return t("inbox.partial");
  if (item.state === "review") return t("inbox.needsReview");
  if (item.state === "completed") return t("inbox.completed");
  switch (item.state) {
    case "captured":
      return t("inbox.state.captured");
    case "resolving":
      return t("inbox.state.resolving");
    case "importing":
      return t("inbox.state.importing");
    case "failed":
      return t("inbox.state.failed");
    default:
      return item.state;
  }
}

export default function InboxPage({ deps = inboxPageDeps }: { deps?: InboxPageDeps }) {
  const { locale, t } = useI18n();
  const [items, setItems] = useState<InboxItem[]>([]);
  const [loading, setLoading] = useState(true);
  const poller = useMemo(
    () =>
      createCompletionChainedPoller<InboxItem[]>({
        request: () => deps.listPendingImports(true),
        intervalMs: 1_500,
        shouldContinue: (next) => next.some((item) => ACTIVE.has(item.state)),
        onResult: (next) => {
          setItems(next);
          setLoading(false);
        },
        onError: (error) => {
          toast.error(error);
          setLoading(false);
        },
      }),
    [deps],
  );
  useEffect(() => {
    poller.refresh();
    return () => poller.stop();
  }, [poller]);
  useEffect(() => {
    if (loading) return;
    if (items.some((item) => ACTIVE.has(item.state))) poller.start();
    else poller.stop();
  }, [items, loading, poller]);
  const groups = useMemo(
    () => ({
      review: items.filter((item) => item.state === "review" || item.state === "failed"),
      active: items.filter((item) => ACTIVE.has(item.state)),
      done: items.filter((item) => item.state === "completed"),
    }),
    [items],
  );
  return (
    <PageContainer>
      <PageHeader title={t("inbox.title")} description={t("inbox.description")} />
      {loading ? (
        <p className="text-sm text-muted-foreground">{t("inbox.loading")}</p>
      ) : items.filter((item) => item.state !== "dismissed").length === 0 ? (
        <EmptyState
          icon={Inbox}
          title={t("inbox.emptyTitle")}
          description={t("inbox.emptyDescription")}
        />
      ) : (
        <div className="space-y-6">
          <Group
            id="review"
            title={t("inbox.needsReview")}
            items={groups.review}
            locale={locale}
            t={t}
            retry={deps.retryPendingImport}
          />
          <Group
            id="progress"
            title={t("inbox.inProgress")}
            items={groups.active}
            locale={locale}
            t={t}
            retry={deps.retryPendingImport}
          />
          <Group
            id="completed"
            title={t("inbox.completed")}
            items={groups.done}
            locale={locale}
            t={t}
            retry={deps.retryPendingImport}
          />
        </div>
      )}
    </PageContainer>
  );
}
