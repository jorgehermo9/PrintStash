import { useEffect, useMemo, useState } from "react";
import { ExternalLink } from "lucide-react";
import { useParams } from "react-router-dom";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { Input } from "@/components/ui/input";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";
import {
  dismissPendingImport,
  getPendingImport,
  importPendingImport,
  retryPendingImport,
  updatePendingImport,
} from "@/lib/api";
import { createCompletionChainedPoller } from "@/lib/completion-chained-polling";
import { formatBytes } from "@/lib/format";
import { Link } from "@/lib/link";
import { useI18n } from "@/lib/i18n";
import { useRouter } from "@/lib/navigation";
import { useCollections } from "@/lib/queries";
import { toast } from "@/lib/toast";
import type { InboxManifestFile, InboxItem } from "@/types";
import { safeHttpUrl } from "@/components/model-detail/source-url";

export interface InboxDetailApi {
  dismissPendingImport: typeof dismissPendingImport;
  getPendingImport: typeof getPendingImport;
  importPendingImport: typeof importPendingImport;
  retryPendingImport: typeof retryPendingImport;
  updatePendingImport: typeof updatePendingImport;
}

const defaultInboxDetailApi: InboxDetailApi = {
  dismissPendingImport,
  getPendingImport,
  importPendingImport,
  retryPendingImport,
  updatePendingImport,
};

const ACTIVE_STATES = new Set<InboxItem["state"]>(["captured", "resolving", "importing"]);

function files(item: InboxItem): InboxManifestFile[] {
  return item.manifest.kind === "archive"
    ? (item.manifest.entries ?? [])
    : item.manifest.kind === "model_files"
      ? (item.manifest.files ?? [])
      : [];
}

function isTerminalState(state: InboxItem["state"]): boolean {
  return state === "completed" || state === "failed" || state === "dismissed";
}

function statusLabel(item: InboxItem, t: ReturnType<typeof useI18n>["t"]): string {
  if (item.completion === "partial") return t("inbox.partial");
  switch (item.state) {
    case "review":
      return t("inbox.needsReview");
    case "completed":
      return t("inbox.completed");
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

function resultLabel(result: InboxItem["results"][number], t: ReturnType<typeof useI18n>["t"]) {
  switch (result.state) {
    case "imported":
      return t("inbox.result.imported");
    case "deduplicated":
      return t("inbox.result.deduplicated");
    case "failed":
      return t("inbox.result.failed");
  }
}

export default function InboxDetailPage({ api = defaultInboxDetailApi }: { api?: InboxDetailApi }) {
  const { t } = useI18n();
  const { id } = useParams();
  const inboxId = Number(id);
  const router = useRouter();
  const collections = useCollections().data ?? [];
  const [item, setItem] = useState<InboxItem | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [tags, setTags] = useState("");
  const [busy, setBusy] = useState(false);
  // The import/retry endpoint returns the pre-worker row, which can still be
  // `review` while its background work has already been queued.
  const [pollingAfterSubmit, setPollingAfterSubmit] = useState(false);
  const [confirmDismiss, setConfirmDismiss] = useState(false);
  const poller = useMemo(
    () =>
      createCompletionChainedPoller<InboxItem>({
        request: () => api.getPendingImport(inboxId),
        intervalMs: 1_500,
        shouldContinue: (next, forceContinue) =>
          !isTerminalState(next.state) && (ACTIVE_STATES.has(next.state) || forceContinue),
        onResult: (next) => {
          setItem(next);
          if (isTerminalState(next.state)) setPollingAfterSubmit(false);
          setSelected(next.manifest.selected_ids ?? []);
          setTags(next.requested_tags.join(", "));
        },
        onError: toast.error,
      }),
    [api, inboxId],
  );
  useEffect(() => {
    if (!Number.isFinite(inboxId)) return;
    poller.refresh();
    return () => poller.stop();
  }, [inboxId, poller]);
  useEffect(() => {
    if (!item) return;
    if (!isTerminalState(item.state) && (pollingAfterSubmit || ACTIVE_STATES.has(item.state))) {
      poller.start();
    } else {
      poller.stop();
    }
  }, [item, pollingAfterSubmit, poller]);
  const choices = useMemo(() => (item ? files(item) : []), [item]);
  if (!item)
    return (
      <PageContainer>
        <PageHeader title={t("inbox.detailTitle")} />
        <p className="text-sm text-muted-foreground">{t("inbox.loading")}</p>
      </PageContainer>
    );
  const toggle = (fileId: string) =>
    setSelected((current) =>
      current.includes(fileId) ? current.filter((value) => value !== fileId) : [...current, fileId],
    );
  const saveDestination = async (collectionId: number | null) => {
    const next = await api.updatePendingImport(item.id, {
      collection_id: collectionId,
      tags: tags
        .split(",")
        .map((tag) => tag.trim())
        .filter(Boolean),
      selected_ids: selected,
    });
    setItem(next);
  };
  const importSelected = async () => {
    poller.stop();
    setBusy(true);
    try {
      await saveDestination(item.target_collection_id);
      const next = await api.importPendingImport(item.id, selected);
      setItem(next);
      const continuePolling = !isTerminalState(next.state);
      setPollingAfterSubmit(continuePolling);
      if (continuePolling) poller.start(true);
    } catch (error) {
      toast.error(error);
      if (item && !isTerminalState(item.state)) poller.start();
    } finally {
      setBusy(false);
    }
  };
  const retry = async () => {
    poller.stop();
    try {
      const next = await api.retryPendingImport(item.id);
      setItem(next);
      const continuePolling = !isTerminalState(next.state);
      setPollingAfterSubmit(continuePolling);
      if (continuePolling) poller.start(true);
    } catch (error) {
      toast.error(error);
      if (item && !isTerminalState(item.state)) poller.start();
    }
  };
  const sourceUrl = item.source_url ? safeHttpUrl(item.source_url) : null;
  return (
    <PageContainer>
      <PageHeader
        title={item.display_title || t("inbox.detailTitle")}
        description={t("inbox.detailDescription")}
        actions={
          <Button variant="outline" asChild>
            <Link href="/inbox">{t("inbox.back")}</Link>
          </Button>
        }
      />
      <Card className="mb-5">
        <CardContent className="space-y-2 pt-6">
          <p className="text-sm font-medium">{t("inbox.source")}</p>
          {sourceUrl ? (
            <a
              href={sourceUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-sm text-primary hover:underline"
            >
              {item.source_hostname || sourceUrl}
              <ExternalLink className="h-3.5 w-3.5" />
            </a>
          ) : item.source_url ? (
            <p className="text-sm text-muted-foreground">{item.source_url}</p>
          ) : (
            <p className="text-sm text-muted-foreground">{t("inbox.sourcePreparing")}</p>
          )}
          <Badge variant="secondary">{statusLabel(item, t)}</Badge>
        </CardContent>
      </Card>
      {item.state === "importing" && (
        <p role="status" aria-live="polite" className="mb-4 text-sm text-muted-foreground">
          {t("inbox.importing")}
        </p>
      )}
      {item.state === "review" && (
        <Card className="mb-5">
          <CardContent className="space-y-4 pt-6">
            <fieldset>
              <legend className="mb-3 text-sm font-medium">{t("inbox.filesToImport")}</legend>
              <div className="space-y-2">
                {choices.map((file) => (
                  <label
                    key={file.id}
                    className="flex items-center gap-3 rounded-md border border-border p-3"
                  >
                    <Checkbox
                      checked={selected.includes(file.id)}
                      onChange={() => toggle(file.id)}
                      ariaLabel={t("inbox.selectFile", { name: file.name })}
                    />
                    <span className="min-w-0 flex-1 truncate text-sm">{file.name}</span>
                    <span className="text-xs text-muted-foreground">
                      {file.file_type} · {formatBytes(file.size)}
                    </span>
                  </label>
                ))}
              </div>
            </fieldset>
            <label className="block text-sm font-medium">
              {t("inbox.destination")}
              <select
                value={item.target_collection_id ?? ""}
                onChange={(event) =>
                  void saveDestination(event.target.value ? Number(event.target.value) : null)
                }
                className="mt-1 block h-10 w-full rounded-md border border-input bg-background px-3 text-sm text-foreground ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              >
                <option value="">{t("inbox.noCollection")}</option>
                {collections.map((collection) => (
                  <option key={collection.id} value={collection.id}>
                    {collection.path}
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-sm font-medium">
              {t("inbox.tags")}
              <Input
                value={tags}
                onChange={(event) => setTags(event.target.value)}
                onBlur={() => void saveDestination(item.target_collection_id)}
                placeholder={t("inbox.commaSeparated")}
              />
            </label>
            <Button
              onClick={() => void importSelected()}
              disabled={selected.length === 0}
              loading={busy}
            >
              {t("inbox.importSelected")}
            </Button>
          </CardContent>
        </Card>
      )}
      {item.results.length > 0 && (
        <Card className="mb-5">
          <CardContent className="space-y-2 pt-6">
            <h2 className="text-sm font-medium">{t("inbox.results")}</h2>
            {item.results.map((result) => (
              <div key={result.id} className="flex items-center gap-2 text-sm">
                <Badge variant={result.state === "failed" ? "destructive" : "success"}>
                  {resultLabel(result, t)}
                </Badge>
                <span>{result.original_filename}</span>
                {result.model_id && (
                  <Link
                    className="text-primary hover:underline"
                    href={`/models/${result.model_id}`}
                  >
                    {t("inbox.openModel")}
                  </Link>
                )}
              </div>
            ))}
            {item.completion === "partial" &&
              item.results.some((result) => result.state === "failed" && result.retryable) && (
                <Button size="sm" variant="outline" onClick={() => void retry()}>
                  {t("inbox.retryFailedFiles")}
                </Button>
              )}
          </CardContent>
        </Card>
      )}
      {item.state === "failed" && item.retryable && (
        <Button variant="outline" onClick={() => void retry()}>
          {t("inbox.retry")}
        </Button>
      )}
      <Button variant="ghost" onClick={() => setConfirmDismiss(true)}>
        {t("inbox.dismiss")}
      </Button>
      <ConfirmModal
        open={confirmDismiss}
        onClose={() => setConfirmDismiss(false)}
        title={t("inbox.dismissTitle")}
        description={t("inbox.dismissDescription")}
        confirmLabel={t("inbox.dismissConfirm")}
        onConfirm={() =>
          void api
            .dismissPendingImport(item.id)
            .then(() => router.push("/inbox"))
            .catch(toast.error)
        }
      />
    </PageContainer>
  );
}
