import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Localized } from "@/components/ui/localized";
import { Skeleton } from "@/components/ui/skeleton";
import { TabBar } from "@/components/ui/tabs";
import { artifactCacheApi, type ArtifactCacheRead } from "@/lib/api/artifact-cache";
import { formatBytes } from "@/lib/format";
import { uiText } from "@/lib/locale";
import { toast } from "@/lib/toast";

const SIZE_LIMITS = [
  { name: "max_bytes", labelKey: "Maximum cache size" },
  { name: "headroom_bytes", labelKey: "Minimum free space" },
] as const;

const LIMITS = [
  { name: "max_entries", labelKey: "Maximum cached files", min: 0 },
  { name: "max_fills", labelKey: "Concurrent downloads", min: 1 },
  {
    name: "fill_wait_seconds",
    labelKey: "Maximum wait for an active download (seconds)",
    min: 0,
  },
  {
    name: "verify_every_hits",
    labelKey: "Recheck digest every N reads (0 disables sampling)",
    min: 0,
  },
] as const;

type SizeName = (typeof SIZE_LIMITS)[number]["name"];
type SizeUnit = "MB" | "GB";
const MB = 1024 ** 2;
const GB = 1024 ** 3;

function sizeUnit(bytes: number): SizeUnit {
  return bytes >= GB ? "GB" : "MB";
}

function unitsForPolicy(policy: ArtifactCacheRead["policy"]) {
  return {
    max_bytes: sizeUnit(policy.max_bytes),
    headroom_bytes: sizeUnit(policy.headroom_bytes),
  };
}

function draftsForPolicy(policy: ArtifactCacheRead["policy"], units: Record<SizeName, SizeUnit>) {
  return {
    max_bytes: Number(
      (policy.max_bytes / (units.max_bytes === "GB" ? GB : MB)).toPrecision(4),
    ).toString(),
    headroom_bytes: Number(
      (policy.headroom_bytes / (units.headroom_bytes === "GB" ? GB : MB)).toPrecision(4),
    ).toString(),
  };
}

function readableCacheSize(bytes: number): string {
  if (bytes === 0) return "0 MB";
  if (bytes < MB) return "<1 MB";
  return formatBytes(bytes);
}

export function ArtifactCacheCard({ api = artifactCacheApi }: { api?: typeof artifactCacheApi }) {
  const [value, setValue] = useState<ArtifactCacheRead | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const [detailView, setDetailView] = useState<"overview" | "limits" | "activity">("overview");
  const [sizeUnits, setSizeUnits] = useState<Record<SizeName, SizeUnit>>({
    max_bytes: "GB",
    headroom_bytes: "GB",
  });
  const [sizeDrafts, setSizeDrafts] = useState<Record<SizeName, string>>({
    max_bytes: "",
    headroom_bytes: "",
  });
  function acceptRead(result: ArtifactCacheRead) {
    const units = unitsForPolicy(result.policy);
    setValue(result);
    setSizeUnits(units);
    setSizeDrafts(draftsForPolicy(result.policy, units));
    setFailed(false);
  }
  useEffect(() => {
    let active = true;
    api
      .read()
      .then((result) => {
        if (!active) return;
        if (result?.policy) {
          acceptRead(result);
        } else {
          setFailed(true);
        }
      })
      .catch(() => {
        if (active) setFailed(true);
      });
    return () => {
      active = false;
    };
  }, [api]);
  const usage = value?.usage ?? {};
  const policy = value?.policy;
  const labels = value?.labels ?? { representation: "artifact", backend: "unknown" };
  const health = value?.health ?? "unavailable";
  const pending = Boolean(usage.maintenance_running || usage.pending_eviction_bytes);
  useEffect(() => {
    if (!pending) return;
    let active = true;
    const timer = window.setInterval(() => {
      void api
        .read()
        .then((result) => {
          if (active) {
            setValue((current) =>
              current
                ? {
                    ...current,
                    usage: result.usage,
                    health: result.health,
                    available: result.available,
                  }
                : result,
            );
            setFailed(false);
          }
        })
        .catch(() => {
          if (active) setFailed(true);
        });
    }, 1000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [api, pending]);

  async function perform(action: () => Promise<ArtifactCacheRead>) {
    setBusy(true);
    try {
      acceptRead(await action());
      toast.success(uiText("Artifact cache settings updated."));
    } catch (error) {
      toast.error(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Localized>
      <Card>
        <CardHeader className="border-b border-border">
          <CardTitle>{uiText("Remote file cache")}</CardTitle>
          <CardDescription>
            {uiText(
              "Reuse verified remote files for previews, printing, and downloads. Original files remain in Vault storage.",
            )}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5 pt-5">
          {failed && (
            <div role="alert" className="space-y-2">
              <p>{uiText("Cache settings could not be loaded.")}</p>
              <Button variant="outline" disabled={busy} onClick={() => void perform(api.read)}>
                {uiText("Retry")}
              </Button>
            </div>
          )}
          {!failed && !value && (
            <div role="status" aria-label={uiText("Loading cache settings…")} className="space-y-5">
              <Skeleton className="h-10 w-64 max-w-full" />
              <Skeleton className="h-4 w-48 max-w-full" />
              <Skeleton className="h-10 w-72 max-w-full" />
              <div className="grid gap-4 sm:grid-cols-3">
                {[0, 1, 2].map((item) => (
                  <Skeleton key={item} className="h-16 w-full" />
                ))}
              </div>
              <span className="sr-only">{uiText("Loading cache settings…")}</span>
            </div>
          )}
          {value && policy && (
            <form
              className="space-y-4"
              onSubmit={(event) => {
                event.preventDefault();
                void perform(() => api.save(policy));
              }}
            >
              <label className="flex min-h-11 items-center gap-3 font-medium">
                <Checkbox
                  checked={policy.enabled}
                  disabled={busy}
                  ariaLabel={uiText("Enable remote Artifact cache")}
                  onChange={(checked) =>
                    setValue({ ...value, policy: { ...policy, enabled: checked === true } })
                  }
                />
                <span>{uiText("Enable remote Artifact cache")}</span>
              </label>
              <p className="text-sm tabular-nums text-muted-foreground">
                {readableCacheSize(usage.bytes ?? 0)} {uiText("cached")} · {usage.entries ?? 0}{" "}
                {uiText("files")}
                {(usage.leases ?? 0) > 0 && (
                  <>
                    {" "}
                    · {usage.leases} {uiText("active reads")}
                  </>
                )}
              </p>
              <div className="border-t border-border pt-4">
                <TabBar
                  tabs={[
                    { key: "overview", label: uiText("Overview") },
                    { key: "limits", label: uiText("Cache limits") },
                    { key: "activity", label: uiText("Activity") },
                  ]}
                  active={detailView}
                  onChange={setDetailView}
                  className="inline-flex rounded-md bg-muted/40 p-1"
                  tabClassName="rounded-sm px-3 py-2 text-sm text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  activeTabClassName="bg-accent text-accent-foreground"
                  showIndicator={false}
                />
              </div>
              {detailView === "overview" && (
                <div
                  role="tabpanel"
                  aria-label={uiText("Overview")}
                  className="grid gap-3 text-sm sm:grid-cols-3"
                >
                  <p>
                    <span className="block text-muted-foreground">{uiText("Policy source:")}</span>
                    {value.source === "database"
                      ? uiText("Saved settings")
                      : uiText("Environment defaults")}
                  </p>
                  <p>
                    <span className="block text-muted-foreground">{uiText("Hit ratio")}</span>
                    {usage.hit_ratio_percent ?? 0}%
                  </p>
                  <p>
                    <span className="block text-muted-foreground">
                      {uiText("Last verification:")}
                    </span>
                    {usage.last_verification
                      ? new Date(usage.last_verification * 1000).toLocaleString()
                      : uiText("No cached files verified yet")}
                  </p>
                </div>
              )}
              {detailView === "limits" && (
                <div role="tabpanel" aria-label={uiText("Cache limits")} className="space-y-4">
                  <p className="text-sm text-muted-foreground">
                    {uiText("Policy source:")}{" "}
                    {value.source === "database"
                      ? uiText("Saved settings")
                      : uiText("Environment defaults")}
                    . {uiText("Limits apply immediately; changing the folder requires a restart.")}
                  </p>
                  <div className="grid gap-4 sm:grid-cols-2 2xl:grid-cols-3">
                    {SIZE_LIMITS.map(({ name, labelKey }) => (
                      <label className="space-y-1" key={name}>
                        <span className="block text-sm">{uiText(labelKey)}</span>
                        <div className="relative">
                          <Input
                            required
                            type="number"
                            min={0}
                            max={Math.floor(
                              Number.MAX_SAFE_INTEGER / (sizeUnits[name] === "GB" ? GB : MB),
                            )}
                            step="any"
                            value={sizeDrafts[name]}
                            disabled={busy}
                            aria-label={`${uiText(labelKey)} (${sizeUnits[name]})`}
                            className="pr-12"
                            onChange={(event) => {
                              const draft = event.target.value;
                              setSizeDrafts((current) => ({ ...current, [name]: draft }));
                              const number = event.target.valueAsNumber;
                              if (Number.isFinite(number) && number >= 0) {
                                setValue({
                                  ...value,
                                  policy: {
                                    ...policy,
                                    [name]: Math.round(
                                      number * (sizeUnits[name] === "GB" ? GB : MB),
                                    ),
                                  },
                                });
                              }
                            }}
                          />
                          <span
                            aria-hidden="true"
                            className="pointer-events-none absolute inset-y-0 right-3 flex items-center text-xs font-medium text-muted-foreground"
                          >
                            {sizeUnits[name]}
                          </span>
                        </div>
                      </label>
                    ))}
                    {LIMITS.map(({ name, labelKey, min }) => (
                      <label className="space-y-1" key={name}>
                        <span className="block text-sm">{uiText(labelKey)}</span>
                        <Input
                          required
                          type="number"
                          min={min}
                          step={1}
                          value={policy[name]}
                          disabled={busy}
                          onChange={(event) =>
                            setValue({
                              ...value,
                              policy: { ...policy, [name]: event.target.valueAsNumber },
                            })
                          }
                        />
                      </label>
                    ))}
                  </div>
                  <label className="block space-y-1">
                    <span className="text-sm">{uiText("Cache folder (restart required)")}</span>
                    <Input
                      required
                      value={policy.root}
                      disabled={busy}
                      onChange={(event) =>
                        setValue({ ...value, policy: { ...policy, root: event.target.value } })
                      }
                    />
                  </label>
                </div>
              )}
              {value.restart_required && (
                <p role="status" className="text-sm text-warning">
                  {uiText("Restart PrintStash to use the new cache folder.")}
                </p>
              )}
              {!value.available && (
                <p className="text-sm text-muted-foreground">
                  {uiText("The cache is unavailable. Files are read from their original storage.")}
                </p>
              )}
              {health !== "ready" && health !== "disabled" && (
                <p role="status" className="text-sm text-warning">
                  {uiText("Cache needs attention")} ({health.replaceAll("_", " ")}).{" "}
                  {uiText("Original Vault storage remains authoritative.")}
                </p>
              )}
              {detailView === "activity" && (
                <div role="tabpanel" aria-label={uiText("Activity")} className="space-y-4">
                  <dl className="grid grid-cols-2 gap-3 text-sm tabular-nums sm:grid-cols-3">
                    {[
                      [uiText("Maximum cache size"), readableCacheSize(policy.max_bytes)],
                      [uiText("Hit ratio"), `${usage.hit_ratio_percent ?? 0}%`],
                      [uiText("Provider data saved"), readableCacheSize(usage.bytes_saved ?? 0)],
                      [uiText("Cache hits"), usage.hits ?? 0],
                      [uiText("Cache misses"), usage.misses ?? 0],
                      [uiText("Completed downloads"), usage.completed_fills ?? 0],
                      [uiText("Publication failures"), usage.publication_failures ?? 0],
                      [uiText("Corruptions"), usage.corruptions ?? 0],
                      [uiText("Cache errors"), usage.errors ?? 0],
                      [uiText("Evictions"), usage.evictions ?? 0],
                      [uiText("Bypasses"), usage.bypasses ?? 0],
                    ].map(([label, count]) => (
                      <div key={label}>
                        <dt className="text-muted-foreground">{label}</dt>
                        <dd>{count}</dd>
                      </div>
                    ))}
                  </dl>
                  <p className="text-xs text-muted-foreground">
                    {uiText("Last verification:")}{" "}
                    {usage.last_verification
                      ? new Date(usage.last_verification * 1000).toLocaleString()
                      : uiText("No cached files verified yet")}
                    .
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {uiText("Representation:")} {labels.representation} ·{" "}
                    {uiText("Storage provider:")} {labels.backend}
                  </p>
                </div>
              )}
              {pending && (
                <p role="status" className="text-sm text-muted-foreground">
                  {uiText("Reclaiming cache space.")}{" "}
                  {readableCacheSize(usage.pending_eviction_bytes ?? 0)}{" "}
                  {uiText("wait for active reads to finish.")}
                </p>
              )}
              <div className="flex flex-wrap gap-2">
                <Button type="submit" disabled={busy}>
                  {uiText("Save cache settings")}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  disabled={busy}
                  onClick={() => void perform(api.reset)}
                >
                  {uiText("Reset to environment defaults")}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  disabled={busy}
                  onClick={() => void perform(api.clear)}
                >
                  {uiText("Clear cached files")}
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                {uiText(
                  "Files in use stay available until their active reads finish. Clearing does not remove your Artifacts.",
                )}
              </p>
            </form>
          )}
        </CardContent>
      </Card>
    </Localized>
  );
}
