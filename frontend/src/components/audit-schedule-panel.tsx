import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import {
  listAuditPolicies,
  listVaultAudits,
  saveAuditPolicy,
  skipAuditSlot,
} from "@/lib/api/maintenance";
import { formatBytes } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import type { AuditPolicy, VaultAuditRun } from "@/types/maintenance";

function shownDate(value: string | null): string {
  return value ? new Date(value).toLocaleString() : "—";
}

function PolicyForm({ initial, onSaved }: { initial: AuditPolicy; onSaved: () => void }) {
  const { t } = useI18n();
  const [policy, setPolicy] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hasChanges = JSON.stringify(policy) !== JSON.stringify(initial);
  const localTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const title = policy.mode === "quick" ? t("auditSchedule.quick") : t("auditSchedule.full");
  const weekdays = [
    t("auditSchedule.monday"),
    t("auditSchedule.tuesday"),
    t("auditSchedule.wednesday"),
    t("auditSchedule.thursday"),
    t("auditSchedule.friday"),
    t("auditSchedule.saturday"),
    t("auditSchedule.sunday"),
  ];
  const deferredMessages = {
    maintenance: t("auditSchedule.deferMaintenance"),
    storage_unavailable: t("auditSchedule.deferStorage"),
    outside_window: t("auditSchedule.deferWindow"),
    audit_active: t("auditSchedule.deferActive"),
    jitter: t("auditSchedule.deferJitter"),
    launch_retry: t("auditSchedule.deferRetry"),
    skipped_once: t("auditSchedule.deferSkipped"),
    shutdown: t("auditSchedule.deferShutdown"),
  };
  async function save() {
    setBusy(true);
    setError(null);
    try {
      setPolicy(await saveAuditPolicy(policy));
      onSaved();
    } catch {
      setError(t("auditSchedule.saveFailed"));
    } finally {
      setBusy(false);
    }
  }
  async function skip() {
    setBusy(true);
    try {
      setPolicy(await skipAuditSlot(policy.mode));
      onSaved();
    } catch {
      setError(t("auditSchedule.skipFailed"));
    } finally {
      setBusy(false);
    }
  }
  return (
    <form
      id={`audit-policy-${policy.mode}`}
      aria-label={`${title} ${t("auditSchedule.schedule")}`}
      className="min-w-0 rounded-md border border-border p-4 sm:p-5"
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <div className="flex flex-col items-start gap-3 sm:flex-row sm:justify-between">
        <div className="min-w-0">
          <h4 className="text-base font-semibold">{title}</h4>
        </div>
        <label className="flex items-center gap-2 text-sm font-medium">
          <Checkbox
            checked={policy.enabled}
            onChange={(enabled) => setPolicy({ ...policy, enabled })}
          />
          {t("auditSchedule.runAutomatically")}
        </label>
      </div>
      {policy.enabled ? (
        <div className="mt-5 space-y-4">
          <div>
            <h5 className="text-sm font-semibold">{t("auditSchedule.when")}</h5>
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <label className="space-y-1 text-sm">
                {t("auditSchedule.cadence")}
                <select
                  className="w-full rounded-md border border-input bg-background p-2"
                  value={policy.cadence}
                  onChange={(event) =>
                    setPolicy({
                      ...policy,
                      cadence: event.target.value === "monthly" ? "monthly" : "weekly",
                    })
                  }
                >
                  <option value="weekly">{t("auditSchedule.weekly")}</option>
                  <option value="monthly">{t("auditSchedule.monthly")}</option>
                </select>
              </label>
              {policy.cadence === "weekly" ? (
                <label className="space-y-1 text-sm">
                  {t("auditSchedule.weekday")}
                  <select
                    className="w-full rounded-md border border-input bg-background p-2"
                    value={policy.weekday}
                    onChange={(event) =>
                      setPolicy({ ...policy, weekday: Number(event.target.value) })
                    }
                  >
                    {weekdays.map((day, index) => (
                      <option key={day} value={index}>
                        {day}
                      </option>
                    ))}
                  </select>
                </label>
              ) : (
                <label className="space-y-1 text-sm">
                  {t("auditSchedule.monthDay")}
                  <Input
                    type="number"
                    min={1}
                    max={31}
                    value={policy.month_day}
                    onChange={(event) =>
                      setPolicy({ ...policy, month_day: Number(event.target.value) })
                    }
                  />
                </label>
              )}
              <label className="space-y-1 text-sm">
                {t("auditSchedule.time")}
                <Input
                  type="time"
                  value={policy.start_time}
                  onChange={(event) => setPolicy({ ...policy, start_time: event.target.value })}
                  required
                />
              </label>
              <label className="space-y-1 text-sm">
                {t("auditSchedule.timezone")}
                <Input
                  value={policy.timezone}
                  onChange={(event) => setPolicy({ ...policy, timezone: event.target.value })}
                  required
                />
              </label>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1">
              <p className="text-xs text-muted-foreground">{t("auditSchedule.timezoneHelp")}</p>
              {localTimezone && localTimezone !== policy.timezone && (
                <Button
                  type="button"
                  variant="link"
                  size="xs"
                  onClick={() => setPolicy({ ...policy, timezone: localTimezone })}
                >
                  {t("auditSchedule.useLocalTimezone")} ({localTimezone})
                </Button>
              )}
            </div>
          </div>
          {policy.mode === "full" && (
            <label className="flex items-start gap-2 text-sm">
              <Checkbox
                checked={policy.full_cost_acknowledged}
                onChange={(full_cost_acknowledged) =>
                  setPolicy({ ...policy, full_cost_acknowledged })
                }
              />
              {t("auditSchedule.fullCost")}
            </label>
          )}
          <details className="border-t border-border pt-3">
            <summary className="cursor-pointer text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              {t("auditSchedule.advanced")}
            </summary>
            <div className="mt-4 space-y-3">
              <label className="flex items-center gap-2 text-sm">
                <Checkbox
                  checked={policy.paused}
                  onChange={(paused) => setPolicy({ ...policy, paused })}
                />
                {t("auditSchedule.paused")}
              </label>
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="space-y-1 text-sm">
                  {t("auditSchedule.jitter")}
                  <Input
                    type="number"
                    min={0}
                    max={3600}
                    value={policy.jitter_seconds ?? 0}
                    onChange={(event) =>
                      setPolicy({ ...policy, jitter_seconds: Number(event.target.value) })
                    }
                  />
                </label>
                <label className="space-y-1 text-sm">
                  {t("auditSchedule.lateness")}
                  <Input
                    type="number"
                    min={1}
                    max={44640}
                    value={policy.max_lateness_minutes ?? 120}
                    onChange={(event) =>
                      setPolicy({ ...policy, max_lateness_minutes: Number(event.target.value) })
                    }
                  />
                </label>
                <label className="space-y-1 text-sm">
                  {t("auditSchedule.notifications")}
                  <select
                    className="w-full rounded-md border border-input bg-background p-2"
                    value={policy.notification_threshold ?? "warning"}
                    onChange={(event) => {
                      const value = event.target.value;
                      if (
                        value === "off" ||
                        value === "info" ||
                        value === "warning" ||
                        value === "critical"
                      )
                        setPolicy({ ...policy, notification_threshold: value });
                    }}
                  >
                    <option value="off">{t("auditSchedule.notifyOff")}</option>
                    <option value="critical">{t("auditSchedule.notifyCritical")}</option>
                    <option value="warning">{t("auditSchedule.notifyWarning")}</option>
                    <option value="info">{t("auditSchedule.notifyInfo")}</option>
                  </select>
                </label>
                <label className="space-y-1 text-sm">
                  {t("auditSchedule.cooldown")}
                  <Input
                    type="number"
                    min={0}
                    max={1440}
                    value={policy.notification_cooldown_minutes ?? 60}
                    onChange={(event) =>
                      setPolicy({
                        ...policy,
                        notification_cooldown_minutes: Number(event.target.value),
                      })
                    }
                  />
                </label>
                <label className="space-y-1 text-sm">
                  {t("auditSchedule.windowLength")}
                  <Input
                    type="number"
                    min={1}
                    max={1440}
                    value={policy.window_minutes}
                    onChange={(event) =>
                      setPolicy({ ...policy, window_minutes: Number(event.target.value) })
                    }
                    required
                  />
                </label>
                <label className="space-y-1 text-sm">
                  {t("auditSchedule.bandwidth")}
                  <Input
                    type="number"
                    min={1024}
                    max={1073741824}
                    value={policy.bytes_per_second}
                    onChange={(event) =>
                      setPolicy({ ...policy, bytes_per_second: Number(event.target.value) })
                    }
                    required
                  />
                </label>
              </div>
              <p className="text-xs text-muted-foreground">{t("auditSchedule.channelHelp")}</p>
              <label className="flex items-center gap-2 text-sm">
                <Checkbox
                  checked={policy.auto_repair}
                  onChange={(auto_repair) => setPolicy({ ...policy, auto_repair })}
                />
                {t("auditSchedule.autoRepair")}
              </label>
              {policy.auto_repair && (
                <div className="flex flex-wrap gap-4 text-sm">
                  {(["reparse_metadata", "regenerate_thumbnail"] as const).map((action) => (
                    <label key={action} className="flex items-center gap-2">
                      <Checkbox
                        checked={policy.repair_actions.includes(action)}
                        onChange={(checked) =>
                          setPolicy({
                            ...policy,
                            repair_actions: checked
                              ? [...policy.repair_actions, action]
                              : policy.repair_actions.filter((item) => item !== action),
                          })
                        }
                      />
                      {action === "reparse_metadata"
                        ? t("auditSchedule.metadata")
                        : t("auditSchedule.thumbnails")}
                    </label>
                  ))}
                </div>
              )}
              {policy.estimated_remote_bytes != null && policy.estimated_remote_bytes > 0 && (
                <p className="text-xs text-muted-foreground">
                  {t("auditSchedule.estimatedReads")}: {formatBytes(policy.estimated_remote_bytes)}
                </p>
              )}
            </div>
          </details>
          {(policy.next_due_at || policy.last_success_at) && (
            <p className="text-xs text-muted-foreground">
              {policy.next_due_at &&
                `${t("auditSchedule.nextDue")}: ${shownDate(policy.next_due_at)}`}
              {policy.next_due_at && policy.last_success_at && " · "}
              {policy.last_success_at &&
                `${t("auditSchedule.lastSuccess")}: ${shownDate(policy.last_success_at)}`}
            </p>
          )}
        </div>
      ) : null}
      {policy.overdue && (
        <p role="status" className="text-sm text-warning">
          {t("auditSchedule.overdue")}
        </p>
      )}
      {policy.deferred_reason && (
        <p role="status" className="text-sm text-warning">
          {t("auditSchedule.deferred")}:{" "}
          {Object.entries(deferredMessages).find(
            ([reason]) => reason === policy.deferred_reason,
          )?.[1] ?? t("auditSchedule.deferred")}
        </p>
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {(hasChanges || (policy.enabled && policy.next_due_at)) && (
        <div className="mt-4 flex flex-wrap gap-2 border-t border-border pt-4">
          {hasChanges && (
            <Button type="submit" className="w-full sm:w-auto" disabled={busy}>
              {t("auditSchedule.saveChanges")}
            </Button>
          )}
          {policy.enabled && policy.next_due_at && (
            <Button type="button" variant="outline" disabled={busy} onClick={() => void skip()}>
              {t("auditSchedule.skip")}
            </Button>
          )}
        </div>
      )}
    </form>
  );
}

export function AuditSchedulePanel() {
  const { t } = useI18n();
  const [policies, setPolicies] = useState<AuditPolicy[] | null>(null);
  const [history, setHistory] = useState<VaultAuditRun[]>([]);
  const [error, setError] = useState(false);
  const [historyError, setHistoryError] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let cancelled = false;
    Promise.allSettled([listAuditPolicies(), listVaultAudits()]).then(
      ([policyResult, historyResult]) => {
        if (cancelled) return;
        if (policyResult.status === "fulfilled") {
          setPolicies(policyResult.value);
          setError(false);
        } else {
          setError(true);
        }
        if (historyResult.status === "fulfilled") {
          setHistory(historyResult.value);
          setHistoryError(false);
        } else {
          setHistoryError(true);
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [revision]);
  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>{t("auditSchedule.title")}</CardTitle>
          <CardDescription>{t("auditSchedule.description")}</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 lg:grid-cols-2">
          {error ? (
            <div role="alert" className="space-y-3">
              <p>{t("auditSchedule.loadFailed")}</p>
              <Button variant="outline" onClick={() => setRevision(revision + 1)}>
                {t("auditSchedule.retry")}
              </Button>
            </div>
          ) : !policies ? (
            <p role="status">{t("auditSchedule.loading")}</p>
          ) : (
            policies.map((policy) => (
              <PolicyForm
                key={`${policy.mode}-${policy.revision}`}
                initial={policy}
                onSaved={() => setRevision((value) => value + 1)}
              />
            ))
          )}
        </CardContent>
      </Card>
      {(historyError || history.length > 0) && (
        <Card>
          <CardHeader>
            <CardTitle>{t("auditSchedule.recent")}</CardTitle>
          </CardHeader>
          <CardContent>
            {historyError ? (
              <p role="alert" className="text-sm">
                {t("auditSchedule.historyLoadFailed")}
              </p>
            ) : (
              <ul className="divide-y divide-border">
                {history.map((run) => {
                  const issueCount = run.critical_count + run.warning_count + run.info_count;
                  const status = {
                    completed: t("auditSchedule.statusCompleted"),
                    running: t("auditSchedule.statusRunning"),
                    pending: t("auditSchedule.statusPending"),
                    failed: t("auditSchedule.statusFailed"),
                    cancelled: t("auditSchedule.statusCancelled"),
                  }[run.state];
                  return (
                    <li
                      key={run.id}
                      className="flex flex-wrap items-center justify-between gap-2 py-3 text-sm"
                    >
                      <div>
                        <p className="font-medium">
                          {t(run.mode === "quick" ? "auditSchedule.quick" : "auditSchedule.full")}
                          {" · "}
                          {status}
                        </p>
                        <p className="text-xs text-muted-foreground">
                          {shownDate(run.finished_at ?? run.created_at)} ·{" "}
                          {run.trigger === "scheduled"
                            ? t("auditSchedule.scheduled")
                            : t("auditSchedule.manual")}
                        </p>
                      </div>
                      <span className="text-xs text-muted-foreground">
                        {issueCount
                          ? t("auditSchedule.issuesFound", { count: issueCount })
                          : t("auditSchedule.noIssues")}
                      </span>
                    </li>
                  );
                })}
              </ul>
            )}
          </CardContent>
        </Card>
      )}
    </>
  );
}
