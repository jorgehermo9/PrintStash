import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, Clock3, Inbox, RefreshCw } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
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
  title,
  items,
  locale,
  t,
  retry,
}: {
  title: string;
  items: InboxItem[];
  locale: string;
  t: ReturnType<typeof useI18n>["t"];
  retry: typeof retryPendingImport;
}) {
  if (!items.length) return null;
  return (
    <section aria-labelledby={`${title}-heading`} className="space-y-3">
      <h2 id={`${title}-heading`} className="text-sm font-semibold text-foreground">
        {title}
      </h2>
      <div className="grid gap-3">
        {items.map((item) => (
          <Card key={item.id} className="animate-card-in">
            <CardContent className="flex flex-col gap-3 pt-6 sm:flex-row sm:items-center">
              {item.state === "completed" ? (
                <CheckCircle2 className="h-5 w-5 text-success" aria-hidden="true" />
              ) : (
                <Clock3 className="h-5 w-5 text-muted-foreground" aria-hidden="true" />
              )}
              <div className="min-w-0 flex-1">
                <h3 className="truncate font-medium">
                  {item.display_title || item.source_hostname || t("inbox.pendingImport")}
                </h3>
                <p className="truncate text-sm text-muted-foreground">
                  {item.source_hostname || t("inbox.sourcePreparing")} ·{" "}
                  {new Date(item.created_at).toLocaleDateString(locale)}
                </p>
              </div>
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
                    <RefreshCw className="h-3.5 w-3.5" /> {t("inbox.retry")}
                  </Button>
                )}
                {item.state === "completed" && item.resulting_model_id ? (
                  <Button size="xs" variant="outline" asChild>
                    <Link href={`/models/${item.resulting_model_id}`}>{t("inbox.openModel")}</Link>
                  </Button>
                ) : (
                  <Button size="xs" asChild>
                    <Link href={`/inbox/${item.id}`}>
                      {item.state === "review" ? t("inbox.review") : t("inbox.view")}
                    </Link>
                  </Button>
                )}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </section>
  );
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
        <div className="space-y-8">
          <Group
            title={t("inbox.needsReview")}
            items={groups.review}
            locale={locale}
            t={t}
            retry={deps.retryPendingImport}
          />
          <Group
            title={t("inbox.inProgress")}
            items={groups.active}
            locale={locale}
            t={t}
            retry={deps.retryPendingImport}
          />
          <Group
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
