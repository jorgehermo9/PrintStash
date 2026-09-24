/**
 * Settings → Background work: what every job-running process is doing.
 *
 * One framed work surface (DESIGN.md): lanes with their concurrency, job
 * definitions with their queues, derivative kinds that can be re-derived, the
 * processes running work, and recent failures. It refreshes when the events
 * socket reports a Job change, and on a slow interval as a fallback, because a
 * notice can be dropped.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, AlertTriangle, Cpu, Layers, RefreshCw, Server } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  cancelQueuedJobs,
  getWorkOverview,
  regenerateDerivatives,
  retryJob,
  setLaneConcurrency,
} from "@/lib/api";
import { subscribeEvents } from "@/lib/events";
import { getErrorMessage, userMessage } from "@/lib/errors";
import { useUiLocale } from "@/lib/i18n";
import { currentLocale, uiText } from "@/lib/locale";
import { toast } from "@/lib/toast";
import type { JobStatus, WorkDefinition, WorkLane, WorkOverview } from "@/types";

/** The panel's server boundary, so a test drives it without a fetch layer. */
export interface BackgroundWorkApi {
  overview: () => Promise<WorkOverview>;
  setLane: (lane: string, concurrency: number | null) => Promise<WorkOverview>;
  cancelQueued: (definition: string) => Promise<{ cancelled: number }>;
  regenerate: (
    kind: string,
    mode: "missing" | "all",
  ) => Promise<{ kind: string; mode: "missing" | "all" }>;
  retry: (jobId: string) => Promise<JobStatus>;
}

const WORK_API: BackgroundWorkApi = {
  overview: getWorkOverview,
  setLane: setLaneConcurrency,
  cancelQueued: cancelQueuedJobs,
  regenerate: regenerateDerivatives,
  retry: retryJob,
};

/** A fallback refresh for dropped notices; the socket is the primary signal. */
const REFRESH_MS = 10_000;

function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat(currentLocale(), {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

type Pending =
  | { kind: "cancel"; definition: WorkDefinition }
  | { kind: "regenerate"; derivative: string };

function SectionHeader({
  icon: Icon,
  title,
  description,
}: {
  icon: typeof Activity;
  title: string;
  description: string;
}) {
  return (
    <div className="flex items-start gap-3 border-b px-4 py-4 sm:px-5">
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-muted">
        <Icon className="h-4 w-4 text-muted-foreground" aria-hidden />
      </div>
      <div className="min-w-0">
        <h3 className="text-sm font-semibold">{title}</h3>
        <p className="text-xs text-muted-foreground">{description}</p>
      </div>
    </div>
  );
}

function LaneRow({
  lane,
  busy,
  onSave,
}: {
  lane: WorkLane;
  busy: boolean;
  onSave: (concurrency: number | null) => void;
}) {
  // The row is keyed by its saved concurrency, so a saved change remounts it
  // with a fresh draft instead of syncing the draft from an effect.
  const [draft, setDraft] = useState(String(lane.concurrency));
  const parsed = Number(draft);
  const valid = Number.isInteger(parsed) && parsed >= 1 && parsed <= 64;
  return (
    <li className="flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:px-5">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-sm">{lane.name}</span>
          {lane.overridden && <Badge variant="secondary">{uiText("Overridden")}</Badge>}
          {lane.partitioned && <Badge variant="outline">{uiText("Per partition")}</Badge>}
        </div>
        <p className="text-xs text-muted-foreground">
          {uiText("{running} running · {queued} queued · default {default}", {
            running: lane.running,
            queued: lane.queued,
            default: lane.default_concurrency,
          })}
        </p>
      </div>
      <div className="flex items-center gap-2">
        <Input
          type="number"
          min={1}
          max={64}
          className="w-20"
          value={draft}
          aria-label={uiText("Concurrency for {lane}", { lane: lane.name })}
          onChange={(event) => setDraft(event.target.value)}
        />
        <Button
          size="sm"
          variant="outline"
          disabled={!valid || parsed === lane.concurrency || busy}
          onClick={() => onSave(parsed)}
        >
          {uiText("Save")}
        </Button>
        {lane.overridden && (
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => onSave(null)}>
            {uiText("Reset")}
          </Button>
        )}
      </div>
    </li>
  );
}

export function BackgroundWorkPanel({ api = WORK_API }: { api?: BackgroundWorkApi }) {
  useUiLocale();
  const [overview, setOverview] = useState<WorkOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);

  const refresh = useCallback(() => {
    api
      .overview()
      .then((next) => {
        setOverview(next);
        setError(null);
      })
      .catch((cause: unknown) => setError(userMessage(cause)));
  }, [api]);

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, REFRESH_MS);
    const stop = subscribeEvents((notice) => {
      if (notice.type === "job" || notice.type === "resync") refresh();
    });
    return () => {
      window.clearInterval(timer);
      stop();
    };
  }, [refresh]);

  const derivativeKinds = useMemo(
    () => [...new Set((overview?.definitions ?? []).flatMap((d) => d.derivative_kinds))].sort(),
    [overview],
  );

  async function act(key: string, work: () => Promise<void>) {
    setBusy(key);
    try {
      await work();
    } catch (cause) {
      toast.error(cause);
    } finally {
      setBusy(null);
    }
  }

  function saveLane(lane: WorkLane, concurrency: number | null) {
    void act(`lane:${lane.name}`, async () => {
      setOverview(await api.setLane(lane.name, concurrency));
      toast.success(uiText("Lane {lane} updated", { lane: lane.name }));
    });
  }

  function confirmPending() {
    const action = pending;
    if (!action) return;
    void act("confirm", async () => {
      if (action.kind === "cancel") {
        const { cancelled } = await api.cancelQueued(action.definition.name);
        toast.success(uiText("{count} queued Jobs cancelled", { count: cancelled }));
      } else {
        await api.regenerate(action.derivative, "all");
        toast.success(uiText("Re-deriving every {kind}", { kind: action.derivative }));
      }
      setPending(null);
      refresh();
    });
  }

  function deriveMissing(kind: string) {
    void act(`missing:${kind}`, async () => {
      await api.regenerate(kind, "missing");
      toast.success(uiText("Deriving missing {kind}", { kind }));
      refresh();
    });
  }

  function retry(job: JobStatus) {
    void act(`retry:${job.job_id}`, async () => {
      await api.retry(job.job_id);
      toast.success(uiText("Job queued again"));
      refresh();
    });
  }

  return (
    <Card className="overflow-hidden animate-panel-in">
      <div className="flex items-center justify-between gap-3 border-b bg-muted/30 p-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold">{uiText("Background work")}</h2>
          <p className="text-xs text-muted-foreground">
            {uiText("Imports, previews, backups and scans run here, off the request path.")}
          </p>
        </div>
        <Button size="sm" variant="outline" onClick={refresh} aria-label={uiText("Refresh")}>
          <RefreshCw className="h-4 w-4" aria-hidden />
        </Button>
      </div>

      {error && (
        <div role="alert" className="flex items-center gap-2 border-b px-4 py-3 text-sm sm:px-5">
          <AlertTriangle className="h-4 w-4 text-warning" aria-hidden />
          {error}
        </div>
      )}

      {!overview && !error ? (
        <div className="space-y-3 p-4 sm:p-5" aria-busy="true">
          <Skeleton className="h-6 w-1/3" />
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </div>
      ) : overview ? (
        <>
          <SectionHeader
            icon={Layers}
            title={uiText("Lanes")}
            description={uiText(
              "How many Jobs of each kind run at once. Changes apply to every process immediately.",
            )}
          />
          <ul className="divide-y divide-border border-b">
            {overview.lanes.map((lane) => (
              <LaneRow
                key={`${lane.name}:${lane.concurrency}`}
                lane={lane}
                busy={busy === `lane:${lane.name}`}
                onSave={(concurrency) => saveLane(lane, concurrency)}
              />
            ))}
          </ul>

          <SectionHeader
            icon={Activity}
            title={uiText("Jobs")}
            description={uiText("Each kind of background work, with its queue and schedule.")}
          />
          <ul className="divide-y divide-border border-b">
            {overview.definitions.map((definition) => (
              <li
                key={definition.name}
                className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:px-5"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium">{definition.label}</span>
                    <span className="font-mono text-2xs text-muted-foreground">
                      {definition.lane}
                    </span>
                    {definition.failed > 0 && (
                      <Badge variant="warning">
                        {uiText("{count} failed", { count: definition.failed })}
                      </Badge>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {uiText("{running} running · {queued} queued · {completed} completed", {
                      running: definition.running,
                      queued: definition.queued,
                      completed: definition.completed,
                    })}
                    {definition.next_due_at
                      ? ` · ${uiText("next {when}", { when: formatDateTime(definition.next_due_at) })}`
                      : ""}
                  </p>
                </div>
                {definition.queued > 0 && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setPending({ kind: "cancel", definition })}
                  >
                    {uiText("Cancel queued")}
                  </Button>
                )}
              </li>
            ))}
          </ul>

          {derivativeKinds.length > 0 && (
            <>
              <SectionHeader
                icon={Cpu}
                title={uiText("Derivatives")}
                description={uiText(
                  "Previews and metadata derived from each Artifact. Current outputs stay visible until their replacements are ready.",
                )}
              />
              <ul className="divide-y divide-border border-b">
                {derivativeKinds.map((kind) => (
                  <li
                    key={kind}
                    className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:px-5"
                  >
                    <span className="min-w-0 flex-1 font-mono text-sm">{kind}</span>
                    <div className="flex gap-2">
                      <Button
                        size="sm"
                        variant="outline"
                        loading={busy === `missing:${kind}`}
                        onClick={() => deriveMissing(kind)}
                      >
                        {uiText("Derive missing")}
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => setPending({ kind: "regenerate", derivative: kind })}
                      >
                        {uiText("Regenerate all")}
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
            </>
          )}

          <SectionHeader
            icon={Server}
            title={uiText("Processes")}
            description={uiText("Every process that runs background work for this vault.")}
          />
          <ul className="divide-y divide-border border-b">
            {overview.executors.map((executor) => (
              <li
                key={executor.executor_id}
                className="flex flex-wrap items-center gap-2 px-4 py-3 sm:px-5"
              >
                <span className="text-sm font-medium">{executor.hostname}</span>
                <Badge variant="outline">{executor.role}</Badge>
                <span className="font-mono text-2xs text-muted-foreground">
                  {uiText("v{value1}", { value1: executor.app_version })}
                </span>
                {executor.stale ? (
                  <Badge variant="warning">{uiText("Not responding")}</Badge>
                ) : (
                  <Badge variant="success">{uiText("Healthy")}</Badge>
                )}
              </li>
            ))}
          </ul>

          <SectionHeader
            icon={AlertTriangle}
            title={uiText("Recent failures")}
            description={uiText(
              "{count} derivatives failed across the library. Retry a Job to run it again.",
              { count: overview.failed_derivatives },
            )}
          />
          {overview.failed_jobs.length === 0 ? (
            <p className="px-4 py-4 text-sm text-muted-foreground sm:px-5">
              {uiText("No recent failures.")}
            </p>
          ) : (
            <ul className="divide-y divide-border">
              {overview.failed_jobs.map((job) => (
                <li
                  key={job.job_id}
                  className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:px-5"
                >
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium">{job.label ?? job.kind}</p>
                    <p className="text-xs text-muted-foreground">
                      {getErrorMessage(job.error ?? "unknown")}
                    </p>
                  </div>
                  {job.retryable && (
                    <Button
                      size="sm"
                      variant="outline"
                      loading={busy === `retry:${job.job_id}`}
                      onClick={() => retry(job)}
                    >
                      {uiText("Retry")}
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </>
      ) : null}

      <ConfirmModal
        open={pending !== null}
        onClose={() => setPending(null)}
        onConfirm={confirmPending}
        busy={busy === "confirm"}
        title={
          pending?.kind === "cancel"
            ? uiText("Cancel queued {label} Jobs?", { label: pending.definition.label })
            : uiText("Regenerate every {kind}?", {
                kind: pending?.kind === "regenerate" ? pending.derivative : "",
              })
        }
        description={
          pending?.kind === "cancel"
            ? uiText("Jobs that have not started are withdrawn. Running Jobs finish normally.")
            : uiText(
                "Every Artifact is derived again in the background. Current outputs stay visible until their replacements are ready.",
              )
        }
        confirmLabel={pending?.kind === "cancel" ? uiText("Cancel queued") : uiText("Regenerate")}
      />
    </Card>
  );
}
