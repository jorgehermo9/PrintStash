import { useEffect, useRef, useState } from "react";
import { ExternalLink } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  deleteModelSourceCover,
  getModelProvenance,
  getModelSourceCover,
  getModelSourceCoverContentPath,
  patchModelProvenance,
  putModelSourceCover,
  updateModel as updateModelApi,
} from "@/lib/api";
import { invalidateCachedAsset } from "@/lib/asset-cache";
import { useOptionalI18n, type MessageKey } from "@/lib/i18n";
import { toast } from "@/lib/toast";
import { useAuthenticatedAssetUrl } from "@/lib/use-authenticated-asset-url";
import { safeHttpUrl } from "@/components/model-detail/source-url";
import type {
  ModelProvenanceRead,
  ModelSourceCoverRead,
  ProvenanceFieldRead,
  ProvenanceSourceRead,
} from "@/types";
import { provenanceOriginKey, type ProvenanceOrigin } from "@printstash/domain";

export interface SourceTabApi {
  getProvenance: typeof getModelProvenance;
  patchProvenance: typeof patchModelProvenance;
  getCover: typeof getModelSourceCover;
  putCover: typeof putModelSourceCover;
  deleteCover: typeof deleteModelSourceCover;
  getCoverContentPath: typeof getModelSourceCoverContentPath;
  updateModel: typeof updateModelApi;
}

const sourceTabApi: SourceTabApi = {
  getProvenance: getModelProvenance,
  patchProvenance: patchModelProvenance,
  getCover: getModelSourceCover,
  putCover: putModelSourceCover,
  deleteCover: deleteModelSourceCover,
  getCoverContentPath: getModelSourceCoverContentPath,
  updateModel: updateModelApi,
};

const LABELS = {
  title: ["source.field.title", "Title"],
  description: ["source.field.description", "Description"],
  instructions: ["source.field.instructions", "Instructions"],
  creator_name: ["source.field.creatorName", "Creator"],
  creator_id: ["source.field.creatorId", "Creator ID"],
  creator_url: ["source.field.creatorUrl", "Creator profile"],
  license_code: ["source.field.licenseCode", "License code"],
  license_url: ["source.field.licenseUrl", "License URL"],
  license_text: ["source.field.licenseText", "License"],
  attribution_text: ["source.field.attributionText", "Attribution"],
  published_at: ["source.published", "Published"],
  updated_at: ["source.updated", "Updated"],
} satisfies Record<ProvenanceFieldRead["field_name"], [MessageKey, string]>;

const ORIGIN_LABELS = {
  confirmed: ["source.origin.confirmed", "Source"],
  inferred: ["source.origin.inferred", "Inferred"],
  user: ["source.origin.user", "Edited"],
} satisfies Record<ProvenanceOrigin, [MessageKey, string]>;

function provenanceOriginLabel(
  origin: ProvenanceOrigin,
  i18n: ReturnType<typeof useOptionalI18n>,
): string {
  const [key, fallback] = ORIGIN_LABELS[provenanceOriginKey(origin)];
  return i18n?.t(key) ?? fallback;
}

function SourceField({
  modelId,
  source,
  field,
  canEdit,
  patchProvenance,
  updateModel,
  onSaved,
  last = false,
}: {
  modelId: number;
  source: ProvenanceSourceRead;
  field: ProvenanceFieldRead;
  canEdit: boolean;
  patchProvenance: typeof patchModelProvenance;
  updateModel: typeof updateModelApi;
  onSaved: (next: ModelProvenanceRead) => void;
  last?: boolean;
}) {
  const i18n = useOptionalI18n();
  const t = (key: MessageKey, fallback: string, values?: Record<string, string>) =>
    i18n?.t(key, values) ?? fallback;
  const [labelKey, labelFallback] = LABELS[field.field_name];
  const label = t(labelKey, labelFallback);
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(field.effective_value);
  const [restoreOpen, setRestoreOpen] = useState(false);
  const save = () =>
    void patchProvenance(modelId, source.id, {
      overrides: { [field.field_name]: value },
      clear_overrides: [],
    })
      .then((next) => {
        onSaved(next);
        setEditing(false);
      })
      .catch(toast.error);
  const restore = () =>
    void patchProvenance(modelId, source.id, {
      overrides: {},
      clear_overrides: [field.field_name],
    })
      .then((next) => {
        onSaved(next);
        setRestoreOpen(false);
        setEditing(false);
      })
      .catch(toast.error);
  const isLink = field.field_name === "creator_url" || field.field_name === "license_url";
  const safeLink = isLink ? safeHttpUrl(field.effective_value) : null;
  const applyToModel = () => {
    const payload =
      field.field_name === "title"
        ? { name: field.effective_value }
        : { description: field.effective_value };
    void updateModel(modelId, payload)
      .then(() => toast.success(t("source.modelUpdated", "Model updated")))
      .catch(toast.error);
  };
  return (
    <div
      className={`grid gap-2 px-3 py-3 sm:grid-cols-[minmax(8rem,0.32fr)_minmax(0,1fr)_auto] sm:items-start ${last ? "" : "border-b border-surface-container-high"}`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-mono text-2xs uppercase tracking-wider text-on-surface-variant">
          {label}
        </h3>
        <Badge variant="secondary">{provenanceOriginLabel(field.effective_origin, i18n)}</Badge>
      </div>
      {editing ? (
        <div className="space-y-2">
          <Input
            value={value}
            onChange={(event) => setValue(event.target.value)}
            aria-label={t("source.override", `${label} override`, { label })}
          />
          <div className="flex flex-wrap gap-2">
            <Button size="xs" onClick={save}>
              {t("source.save", "Save")}
            </Button>
            <Button
              size="xs"
              variant="outline"
              onClick={() => {
                setValue(field.effective_value);
                setEditing(false);
              }}
            >
              {t("source.cancel", "Cancel")}
            </Button>
            {field.user_override_set && (
              <Button size="xs" variant="ghost" onClick={() => setRestoreOpen(true)}>
                {t("source.restore", "Restore captured value")}
              </Button>
            )}
          </div>
        </div>
      ) : (
        <div className="flex min-w-0 items-start justify-between gap-3">
          <div className="min-w-0 whitespace-pre-wrap text-sm text-muted-foreground">
            {safeLink ? (
              <a
                href={safeLink}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-primary hover:underline"
              >
                {safeLink}
                <ExternalLink className="h-3.5 w-3.5" />
              </a>
            ) : (
              field.effective_value ||
              (field.field_name.startsWith("license")
                ? t("source.notSupplied", "Not supplied by source")
                : t("source.notSuppliedGeneric", "Not supplied"))
            )}
          </div>
          {canEdit && (
            <div className="flex shrink-0 flex-wrap justify-end gap-1">
              {(field.field_name === "title" || field.field_name === "description") && (
                <Button size="xs" variant="outline" onClick={applyToModel}>
                  {field.field_name === "title"
                    ? (i18n?.t("source.useTitle") ?? "Use source title")
                    : (i18n?.t("source.useDescription") ?? "Use source description")}
                </Button>
              )}
              <Button size="xs" variant="ghost" onClick={() => setEditing(true)}>
                {t("source.edit", "Edit")}
              </Button>
            </div>
          )}
        </div>
      )}
      {field.field_name === "license_text" && (
        <p className="col-span-full mt-1 text-xs text-muted-foreground sm:col-start-2">
          {i18n?.t("source.licenseDisclaimer") ??
            "PrintStash preserves published license text and does not grant, interpret, or expand rights."}
        </p>
      )}
      <ConfirmModal
        open={restoreOpen}
        onClose={() => setRestoreOpen(false)}
        title={t("source.restoreTitle", "Restore captured value?")}
        description={t(
          "source.restoreDescription",
          "This discards your correction and restores the value captured from the source.",
        )}
        confirmLabel={t("source.restoreConfirm", "Restore")}
        onConfirm={restore}
      />
    </div>
  );
}

function SourceTags({ tags, last = false }: { tags: string[]; last?: boolean }) {
  const i18n = useOptionalI18n();
  if (tags.length === 0) return null;
  return (
    <div
      className={`flex flex-col gap-2 px-3 py-3 sm:grid sm:grid-cols-[minmax(8rem,0.32fr)_minmax(0,1fr)] sm:items-start ${last ? "" : "border-b border-surface-container-high"}`}
      aria-label={i18n?.t("source.tags") ?? "Source tags"}
    >
      <span className="font-mono text-2xs uppercase tracking-wider text-on-surface-variant">
        {i18n?.t("source.tags") ?? "Source tags"}
      </span>
      <div className="flex flex-wrap gap-1.5">
        {tags.map((tag) => (
          <Badge key={tag} variant="secondary">
            {tag}
          </Badge>
        ))}
      </div>
    </div>
  );
}

const ACCEPTED_COVER_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);
const MAX_COVER_BYTES = 15 * 1024 * 1024;

function SourceCover({
  modelId,
  source,
  canEdit,
  api,
}: {
  modelId: number;
  source: ProvenanceSourceRead;
  canEdit: boolean;
  api: SourceTabApi;
}) {
  const i18n = useOptionalI18n();
  const t = (
    key:
      | "source.cover"
      | "source.coverUpload"
      | "source.coverReplace"
      | "source.coverDelete"
      | "source.coverReplaceTitle"
      | "source.coverReplaceDescription"
      | "source.coverDeleteTitle"
      | "source.coverDeleteDescription"
      | "source.coverInvalid"
      | "source.coverTooLarge"
      | "source.coverAvailable"
      | "source.coverUnavailable",
  ) =>
    i18n?.t(key) ??
    {
      "source.cover": "Private representative cover",
      "source.coverUpload": "Upload cover",
      "source.coverReplace": "Replace cover",
      "source.coverDelete": "Delete cover",
      "source.coverReplaceTitle": "Replace private cover?",
      "source.coverReplaceDescription":
        "The existing private representative cover will be replaced.",
      "source.coverDeleteTitle": "Delete private cover?",
      "source.coverDeleteDescription":
        "This removes the private representative cover from this source.",
      "source.coverInvalid": "Choose a JPEG, PNG, or WebP image.",
      "source.coverTooLarge": "Cover images must be 15 MiB or smaller.",
      "source.coverAvailable": "A private representative cover is available.",
      "source.coverUnavailable": "Private representative cover preview is unavailable.",
    }[key];
  const [cover, setCover] = useState<ModelSourceCoverRead | null>(null);
  const [busy, setBusy] = useState<"upload" | "delete" | null>(null);
  const [replaceOpen, setReplaceOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const pendingFile = useRef<File | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const contentPath = api.getCoverContentPath(modelId, source.id);
  const imageUrl = useAuthenticatedAssetUrl(cover ? contentPath : null);

  useEffect(() => {
    let active = true;
    void api
      .getCover(modelId, source.id)
      .then((next) => {
        if (active) setCover(next);
      })
      // Covers are optional and private. A missing or unauthorized cover must
      // not disclose anything in the Source tab.
      .catch(() => {
        if (active) setCover(null);
      });
    return () => {
      active = false;
    };
  }, [api, modelId, source.id]);

  const upload = (file: File) => {
    setBusy("upload");
    void api
      .putCover(modelId, source.id, file)
      .then((next) => {
        invalidateCachedAsset(contentPath);
        setCover(next);
        pendingFile.current = null;
        setReplaceOpen(false);
      })
      .catch(toast.error)
      .finally(() => setBusy(null));
  };
  const selectFile = (file: File | undefined) => {
    if (!file) return;
    if (!ACCEPTED_COVER_TYPES.has(file.type)) {
      toast.error(t("source.coverInvalid"));
      return;
    }
    if (file.size > MAX_COVER_BYTES) {
      toast.error(t("source.coverTooLarge"));
      return;
    }
    if (cover) {
      pendingFile.current = file;
      setReplaceOpen(true);
    } else {
      upload(file);
    }
  };
  const remove = () => {
    setBusy("delete");
    void api
      .deleteCover(modelId, source.id)
      .then(() => {
        invalidateCachedAsset(contentPath);
        setCover(null);
        setDeleteOpen(false);
      })
      .catch(toast.error)
      .finally(() => setBusy(null));
  };

  return (
    <section className="border-y border-surface-container-high py-3" aria-label={t("source.cover")}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-mono text-2xs uppercase tracking-wider text-on-surface-variant">
          {t("source.cover")}
        </h3>
        {canEdit && (
          <div className="flex flex-wrap gap-2">
            <input
              ref={inputRef}
              type="file"
              accept="image/jpeg,image/png,image/webp"
              className="sr-only"
              aria-label={cover ? t("source.coverReplace") : t("source.coverUpload")}
              onChange={(event) => {
                selectFile(event.currentTarget.files?.[0]);
                event.currentTarget.value = "";
              }}
            />
            <Button
              size="xs"
              variant="outline"
              loading={busy === "upload"}
              onClick={() => inputRef.current?.click()}
            >
              {cover ? t("source.coverReplace") : t("source.coverUpload")}
            </Button>
            {cover && (
              <Button
                size="xs"
                variant="destructive"
                loading={busy === "delete"}
                onClick={() => setDeleteOpen(true)}
              >
                {t("source.coverDelete")}
              </Button>
            )}
          </div>
        )}
      </div>
      <p className="mt-1 text-xs text-muted-foreground" aria-live="polite" role="status">
        {busy === "upload"
          ? t("source.coverUpload")
          : busy === "delete"
            ? t("source.coverDelete")
            : cover
              ? t("source.coverAvailable")
              : ""}
      </p>
      {cover && (
        <div className="mt-2 overflow-hidden rounded-md border border-border bg-muted">
          {imageUrl ? (
            <img
              src={imageUrl}
              alt={`${t("source.cover")} - ${source.provider}`}
              className="max-h-64 w-full object-contain"
            />
          ) : (
            <p className="p-3 text-sm text-muted-foreground">{t("source.coverUnavailable")}</p>
          )}
        </div>
      )}
      <ConfirmModal
        open={replaceOpen}
        onClose={() => {
          pendingFile.current = null;
          setReplaceOpen(false);
        }}
        title={t("source.coverReplaceTitle")}
        description={t("source.coverReplaceDescription")}
        confirmLabel={t("source.coverReplace")}
        busy={busy === "upload"}
        onConfirm={() => {
          if (pendingFile.current) upload(pendingFile.current);
        }}
      />
      <ConfirmModal
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        title={t("source.coverDeleteTitle")}
        description={t("source.coverDeleteDescription")}
        confirmLabel={t("source.coverDelete")}
        busy={busy === "delete"}
        onConfirm={remove}
      />
    </section>
  );
}

export function SourceTab({
  modelId,
  canEdit,
  api = sourceTabApi,
}: {
  modelId: number;
  canEdit: boolean;
  api?: SourceTabApi;
}) {
  const i18n = useOptionalI18n();
  const t = (key: MessageKey, fallback: string, values?: Record<string, string>) =>
    i18n?.t(key, values) ?? fallback;
  const [data, setData] = useState<ModelProvenanceRead | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    void api
      .getProvenance(modelId)
      .then(setData)
      .catch(() => setFailed(true));
  }, [api, modelId]);
  if (!data && !failed)
    return (
      <div className="space-y-3">
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  if (failed || !data?.sources.length)
    return (
      <EmptyState
        title={t("source.emptyTitle", "No captured source")}
        description={t(
          "source.emptyDescription",
          "This Model has no structured source snapshot yet.",
        )}
      />
    );
  return (
    <div className="space-y-4">
      {data.sources.map((source) => {
        const canonicalUrl = safeHttpUrl(source.canonical_url);
        const fieldCount = source.fields.length + (source.tags?.length ? 1 : 0);
        return (
          <Card key={source.id}>
            <CardContent className="space-y-5 pt-5">
              <section aria-labelledby={`source-heading-${source.id}`}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <p className="font-mono text-2xs uppercase tracking-wider text-on-surface-variant">
                      {t("source.title", "Source")}
                    </p>
                    <h2
                      id={`source-heading-${source.id}`}
                      className="mt-1 text-base font-semibold capitalize"
                    >
                      {source.provider}
                    </h2>
                  </div>
                  <Badge variant="secondary">{t("source.capturedStatus", "Captured")}</Badge>
                </div>
                {canonicalUrl ? (
                  <a
                    href={canonicalUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="mt-2 inline-flex max-w-full items-center gap-1 break-all text-sm text-primary hover:underline"
                  >
                    {canonicalUrl}
                    <ExternalLink className="h-3.5 w-3.5" />
                  </a>
                ) : (
                  <p className="text-sm text-muted-foreground">{source.canonical_url}</p>
                )}
                <div className="mt-4 grid gap-2 border-t border-surface-container-high pt-3 text-xs sm:grid-cols-2">
                  <div>
                    <span className="font-mono text-2xs uppercase tracking-wider text-on-surface-variant">
                      {t("source.sourceId", "Source ID")}
                    </span>
                    <p className="mt-1 text-muted-foreground">
                      {source.source_item_id || t("source.notSuppliedGeneric", "Not supplied")}
                    </p>
                  </div>
                  <div>
                    <span className="font-mono text-2xs uppercase tracking-wider text-on-surface-variant">
                      {t("source.revision", "Revision")}
                    </span>
                    <p className="mt-1 text-muted-foreground">
                      {source.source_revision || t("source.notSuppliedGeneric", "Not supplied")}
                    </p>
                  </div>
                  <div>
                    <span className="font-mono text-2xs uppercase tracking-wider text-on-surface-variant">
                      {t("source.captured", "Captured")}
                    </span>
                    <p className="mt-1 text-muted-foreground">
                      {new Date(source.first_captured_at).toLocaleDateString(i18n?.locale)}
                    </p>
                  </div>
                  <div>
                    <span className="font-mono text-2xs uppercase tracking-wider text-on-surface-variant">
                      {t("source.checked", "Last checked")}
                    </span>
                    <p className="mt-1 text-muted-foreground">
                      {new Date(source.last_checked_at).toLocaleDateString(i18n?.locale)}
                    </p>
                  </div>
                </div>
              </section>
              <SourceCover modelId={modelId} source={source} canEdit={canEdit} api={api} />
              {fieldCount > 0 && (
                <section aria-labelledby={`metadata-heading-${source.id}`}>
                  <h3
                    id={`metadata-heading-${source.id}`}
                    className="mb-2 font-mono text-2xs uppercase tracking-wider text-on-surface-variant"
                  >
                    {t("source.metadata", "Captured metadata")}
                  </h3>
                  <div className="overflow-hidden rounded border border-outline-variant bg-surface">
                    <SourceTags tags={source.tags ?? []} last={source.fields.length === 0} />
                    {source.fields.map((field, index) => (
                      <SourceField
                        key={field.field_name}
                        modelId={modelId}
                        source={source}
                        field={field}
                        canEdit={canEdit}
                        patchProvenance={api.patchProvenance}
                        updateModel={api.updateModel}
                        onSaved={setData}
                        last={index === source.fields.length - 1}
                      />
                    ))}
                  </div>
                </section>
              )}
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
