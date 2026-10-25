import { lazy, Suspense, useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Checkbox } from "@/components/ui/checkbox";
import { similarityPrintJobsOptions } from "@/lib/queries/similarity";
import { Button } from "@/components/ui/button";
import { getAssetUrl } from "@/lib/api/request";
import { createComparisonCamera } from "@/lib/comparison-camera";
import { formatBytes } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import { Link } from "@/lib/link";
import type { FileRead, ModelRead } from "@/types/models";
import type { SimilarityCandidate, SimilaritySource } from "@/types/similarity";
import { MeshPreviewGate } from "@/components/mesh-preview-gate";

const STLViewer = lazy(() =>
  import("@/components/stl-viewer").then((module) => ({ default: module.STLViewer })),
);

function comparisonFile(
  model: ModelRead | undefined,
  source: SimilaritySource | undefined,
  hash: string | undefined,
) {
  return model?.files.find(
    (file) =>
      file.file_type !== "gcode" && file.sha256 === hash && (!source || file.id === source.file_id),
  );
}
function extent(file: FileRead | undefined) {
  return Math.max(
    file?.metadata?.bbox_x_mm ?? 0,
    file?.metadata?.bbox_y_mm ?? 0,
    file?.metadata?.bbox_z_mm ?? 0,
  );
}

export function SimilarityComparison({
  candidate,
  models,
  evidence,
}: {
  candidate: SimilarityCandidate;
  models: [ModelRead | undefined, ModelRead | undefined];
  evidence?: ReactNode;
}) {
  const { t, locale } = useI18n();
  const [scale, setScale] = useState(false);
  const [mirror, setMirror] = useState(false);
  const [overlay, setOverlay] = useState(false);
  const camera = useMemo(() => createComparisonCamera(), []);
  const observation = candidate.observations?.find(
    (item) => item.lineage_key === candidate.primary_lineage_key,
  );
  const files = [
    comparisonFile(models[0], observation?.source_a, observation?.input_hash_a),
    comparisonFile(models[1], observation?.source_b, observation?.input_hash_b),
  ];
  const referenceSizeMm = Math.max(...files.map(extent), 1);
  // Component transforms are in resource space. Whole-Artifact previews keep their
  // original coordinates; applying a resource transform would misrepresent them.
  const canAlign = observation?.kind === "whole" && candidate.freshness === "current";
  const alignment = canAlign ? candidate.summary.transform : undefined;
  const left = useMemo(() => ({ referenceSizeMm }), [referenceSizeMm]);
  const right = useMemo(
    () => ({ referenceSizeMm, alignment, compensateScale: scale, compensateMirror: mirror }),
    [referenceSizeMm, alignment, scale, mirror],
  );
  const jobsA = useQuery(similarityPrintJobsOptions(candidate.model_a_id));
  const jobsB = useQuery(similarityPrintJobsOptions(candidate.model_b_id));
  const number = (value: number | null | undefined, unit = "") =>
    value == null
      ? t("similarity.missingMeasurement")
      : `${new Intl.NumberFormat(locale, { maximumFractionDigits: 3 }).format(value)}${unit}`;
  const sources = [observation?.source_a, observation?.source_b];
  const modelRefs = [candidate.model_a, candidate.model_b];
  const latestPrint = [jobsA, jobsB].map((query) =>
    query.data?.reduce<string | undefined>(
      (latest, job) => (!latest || job.created_at > latest ? job.created_at : latest),
      undefined,
    ),
  );
  const dimensions = (file: FileRead | undefined) =>
    file?.metadata
      ? [file.metadata.bbox_x_mm, file.metadata.bbox_y_mm, file.metadata.bbox_z_mm]
          .map((v) => number(v))
          .join(" × ") + " mm"
      : t("similarity.missingMeasurement");
  const rows = [
    { label: t("similarity.dimensions"), values: files.map(dimensions) },
    {
      label: t("similarity.volume"),
      values: files.map((file, index) =>
        number(sources[index]?.volume ?? file?.metadata?.volume_mm3, " mm³"),
      ),
    },
    {
      label: t("similarity.area"),
      values: sources.map((source) => number(source?.surface_area, " mm²")),
    },
    {
      label: t("similarity.triangles"),
      values: files.map((file, index) =>
        number(sources[index]?.face_count ?? file?.metadata?.triangle_count),
      ),
    },
    {
      label: t("similarity.watertight"),
      values: sources.map((source) =>
        source?.watertight == null
          ? t("similarity.missingMeasurement")
          : source.watertight
            ? t("Yes")
            : t("No"),
      ),
    },
    {
      label: t("similarity.format"),
      values: files.map(
        (file) => file?.file_type.toUpperCase() ?? t("similarity.missingMeasurement"),
      ),
    },
    {
      label: t("Size"),
      values: files.map((file) =>
        file ? formatBytes(file.size_bytes) : t("similarity.missingMeasurement"),
      ),
    },
    {
      label: t("similarity.source"),
      values: files.map((file) =>
        file
          ? t(file.is_external ? "similarity.external" : "similarity.vault")
          : t("similarity.missingMeasurement"),
      ),
    },
    {
      label: t("Revisions"),
      values: models.map((model) =>
        model
          ? number(model.files.filter((file) => file.file_type === "gcode").length)
          : t("similarity.missingMeasurement"),
      ),
    },
    {
      label: t("similarity.knownGood"),
      values: models.map((model) =>
        model
          ? t(model.files.some((file) => file.revision_status === "known_good") ? "Yes" : "No")
          : t("similarity.missingMeasurement"),
      ),
    },
    {
      label: t("similarity.lastPrint"),
      values: latestPrint.map((date) =>
        date ? new Date(date).toLocaleString(locale) : t("similarity.missingMeasurement"),
      ),
    },
  ];
  return (
    <section aria-label={t("similarity.compareTitle")} className="@container/comparison min-w-0">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-3 border-b border-border bg-muted/30 p-3">
        <div
          role="group"
          aria-label={t("similarity.viewMode")}
          className="flex max-w-full flex-wrap gap-1 rounded-md bg-muted p-1"
        >
          <Button
            size="sm"
            variant="ghost"
            className={!overlay ? "bg-accent text-accent-foreground" : undefined}
            aria-pressed={!overlay}
            onClick={() => setOverlay(false)}
          >
            {t("similarity.sideBySide")}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className={overlay ? "bg-accent text-accent-foreground" : undefined}
            aria-pressed={overlay}
            disabled={!files[0] || !files[1]}
            onClick={() => setOverlay(true)}
          >
            {t("similarity.overlay")}
          </Button>
        </div>
        <div
          role="group"
          aria-label={t("similarity.alignmentOptions")}
          className="flex flex-wrap gap-x-4 gap-y-2 text-xs"
        >
          <label className="flex min-w-0 items-center gap-2">
            <Checkbox
              ariaLabel={t("similarity.compensateScale")}
              checked={scale}
              disabled={!alignment}
              onChange={setScale}
            />
            {t("similarity.compensateScale")}
          </label>
          <label className="flex min-w-0 items-center gap-2">
            <Checkbox
              ariaLabel={t("similarity.compensateMirror")}
              checked={mirror}
              disabled={!alignment}
              onChange={setMirror}
            />
            {t("similarity.compensateMirror")}
          </label>
        </div>
        <span className="text-xs text-muted-foreground">{t("similarity.physicalScale")}</span>
      </div>
      <div className="grid min-w-0 @5xl/comparison:grid-cols-[minmax(0,1fr)_24rem]">
        <div className="min-w-0 p-3 sm:p-4">
          <div
            role="group"
            aria-label={t("similarity.previews")}
            className={overlay ? "grid gap-3" : "grid gap-3 @xl/comparison:grid-cols-2"}
          >
            {files.map((file, index) =>
              overlay && index === 1 ? null : (
                <div
                  key={modelRefs[index].id}
                  className="min-w-0 overflow-hidden rounded-md border border-border"
                >
                  <div className="space-y-1 border-b border-border bg-muted/20 px-3 py-2">
                    {(overlay ? modelRefs : [modelRefs[index]]).map((model, modelIndex) => (
                      <div key={model.id} className="flex min-w-0 items-center gap-2">
                        <span className="shrink-0 font-mono text-xs text-muted-foreground">
                          {t(
                            (overlay ? modelIndex : index) === 0
                              ? "similarity.modelA"
                              : "similarity.modelB",
                          )}
                        </span>
                        <Link
                          className="min-w-0 truncate text-sm font-medium hover:underline"
                          href={`/models/${model.id}`}
                          title={model.name}
                        >
                          {model.name}
                        </Link>
                      </div>
                    ))}
                    <p className="break-words text-xs text-muted-foreground">
                      {dimensions(file)} ·{" "}
                      {file?.file_type.toUpperCase() ?? t("similarity.noPreview")}
                    </p>
                    <p
                      className="truncate text-xs text-muted-foreground"
                      title={file?.original_filename}
                    >
                      {file?.original_filename ?? t("similarity.noPreview")}
                    </p>
                  </div>
                  <div className="h-64 bg-muted/30 @xl/comparison:h-80 @5xl/comparison:h-112">
                    {file ? (
                      <Suspense
                        fallback={
                          <p role="status" className="p-4 text-sm text-muted-foreground">
                            {t("similarity.loading")}
                          </p>
                        }
                      >
                        <MeshPreviewGate
                          key={file.id}
                          // The overlay draws the other Model's mesh in this pane too.
                          sizeBytes={
                            index === 0 && overlay && files[1]
                              ? Math.max(file.size_bytes, files[1].size_bytes)
                              : file.size_bytes
                          }
                          posterUrl={null}
                          posterAlt={modelRefs[index].name}
                        >
                          <STLViewer
                            url={getAssetUrl(`/api/v1/files/${file.id}/stl`)}
                            modelId={modelRefs[index].id}
                            comparison={index === 0 ? left : right}
                            comparisonCamera={camera}
                            showGrid={false}
                            overlay={
                              index === 0 && overlay && files[1]
                                ? {
                                    url: getAssetUrl(`/api/v1/files/${files[1].id}/stl`),
                                    comparison: right,
                                  }
                                : undefined
                            }
                          />
                        </MeshPreviewGate>
                      </Suspense>
                    ) : (
                      <p className="flex h-full items-center justify-center p-4 text-sm text-muted-foreground">
                        {t("similarity.noPreview")}
                      </p>
                    )}
                  </div>
                </div>
              ),
            )}
          </div>
          {!canAlign && (
            <p className="mt-3 text-xs text-muted-foreground">
              {t("similarity.componentPreviewHelp")}
            </p>
          )}
        </div>
        <div className="min-w-0 border-t border-border @5xl/comparison:border-l @5xl/comparison:border-t-0">
          <div className="border-b border-border px-4 py-3">
            <h2 className="text-sm font-semibold">{t("similarity.measurements")}</h2>
          </div>
          {[jobsA, jobsB].map(
            (jobs, index) =>
              jobs.isError && (
                <div
                  key={index}
                  role="alert"
                  className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-2 text-xs text-destructive"
                >
                  <span>
                    {t(index === 0 ? "similarity.modelA" : "similarity.modelB")} ·{" "}
                    {t("similarity.loadError")}
                  </span>
                  <Button size="sm" variant="outline" onClick={() => void jobs.refetch()}>
                    {t("similarity.retry")}
                  </Button>
                </div>
              ),
          )}
          <table
            aria-label={t("similarity.measurements")}
            className="w-full table-fixed text-left text-xs"
          >
            <thead className="border-b border-border bg-muted/30">
              <tr>
                <th scope="col" className="w-1/3 px-3 py-2 font-medium text-muted-foreground">
                  {t("similarity.measurement")}
                </th>
                {modelRefs.map((model, index) => (
                  <th scope="col" className="w-1/3 px-3 py-2 font-medium" key={model.id}>
                    <span className="block font-mono text-muted-foreground">
                      {t(index === 0 ? "similarity.modelA" : "similarity.modelB")}
                    </span>
                    <span className="block truncate" title={model.name}>
                      {model.name}
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {rows.map((row) => (
                <tr key={row.label}>
                  <th
                    scope="row"
                    className="break-words px-3 py-2 font-medium text-muted-foreground"
                  >
                    {row.label}
                  </th>
                  {row.values.map((value, index) => (
                    <td className="break-words px-3 py-2 tabular-nums" key={modelRefs[index].id}>
                      {value}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {evidence && <div className="px-4 pb-3">{evidence}</div>}
        </div>
      </div>
    </section>
  );
}
