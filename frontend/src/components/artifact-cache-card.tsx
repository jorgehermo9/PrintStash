import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Localized } from "@/components/ui/localized";
import { artifactCacheApi, type ArtifactCacheRead } from "@/lib/api/artifact-cache";
import { formatBytes } from "@/lib/format";
import { uiText } from "@/lib/locale";
import { toast } from "@/lib/toast";

const LIMITS = [
  { name: "max_bytes", labelKey: "Maximum cache bytes", min: 0 },
  { name: "headroom_bytes", labelKey: "Minimum free bytes", min: 0 },
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

export function ArtifactCacheCard({ api = artifactCacheApi }: { api?: typeof artifactCacheApi }) {
  const [value, setValue] = useState<ArtifactCacheRead | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true;
    api
      .read()
      .then((result) => {
        if (!active) return;
        if (result?.policy) {
          setValue(result);
          setFailed(false);
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
      setValue(await action());
      setFailed(false);
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
        <CardHeader>
          <CardTitle>{uiText("Remote file cache")}</CardTitle>
          <CardDescription>
            {uiText(
              "Reuse verified remote files for previews, printing, and downloads. Original files remain in Vault storage.",
            )}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {failed && (
            <div role="alert" className="space-y-2">
              <p>{uiText("Cache settings could not be loaded.")}</p>
              <Button variant="outline" disabled={busy} onClick={() => void perform(api.read)}>
                {uiText("Retry")}
              </Button>
            </div>
          )}
          {!failed && !value && (
            <p role="status" className="text-sm text-muted-foreground">
              {uiText("Loading cache settings…")}
            </p>
          )}
          {value && policy && (
            <form
              className="space-y-4"
              onSubmit={(event) => {
                event.preventDefault();
                void perform(() => api.save(policy));
              }}
            >
              <label className="flex items-center gap-3">
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
                {formatBytes(usage.bytes ?? 0)} {uiText("cached")} · {usage.entries ?? 0}{" "}
                {uiText("files")}
                {(usage.leases ?? 0) > 0 && (
                  <>
                    {" "}
                    · {usage.leases} {uiText("active reads")}
                  </>
                )}
              </p>
              <details className="rounded-md border border-border px-3 py-2">
                <summary className="cursor-pointer text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                  {uiText("Advanced cache settings")}
                </summary>
                <div className="mt-4 space-y-4">
                  <p className="text-sm text-muted-foreground">
                    {uiText("Policy source:")}{" "}
                    {value.source === "database"
                      ? uiText("Saved settings")
                      : uiText("Environment defaults")}
                    . {uiText("Limits apply immediately; changing the folder requires a restart.")}
                  </p>
                  <div className="grid gap-4 sm:grid-cols-2">
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
              </details>
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
              <details className="rounded-md border border-border px-3 py-2">
                <summary className="cursor-pointer text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                  {uiText("Cache activity and diagnostics")}
                </summary>
                <div className="mt-4 space-y-4">
                  <dl className="grid grid-cols-2 gap-3 text-sm tabular-nums sm:grid-cols-3">
                    {[
                      [uiText("Maximum bytes"), policy.max_bytes],
                      [uiText("Hit ratio"), `${usage.hit_ratio_percent ?? 0}%`],
                      [uiText("Provider bytes saved"), usage.bytes_saved ?? 0],
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
                  {health !== "ready" && health !== "disabled" && (
                    <p role="status" className="text-sm text-warning">
                      {uiText("Cache needs attention")} ({health.replaceAll("_", " ")}).{" "}
                      {uiText("Original Vault storage remains authoritative.")}
                    </p>
                  )}
                  <p className="text-xs text-muted-foreground">
                    {uiText("Representation:")} {labels.representation} ·{" "}
                    {uiText("Storage provider:")} {labels.backend}
                  </p>
                </div>
              </details>
              {pending && (
                <p role="status" className="text-sm text-muted-foreground">
                  {uiText("Reclaiming cache space.")} {usage.pending_eviction_bytes ?? 0}{" "}
                  {uiText("bytes wait for active reads to finish.")}
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
