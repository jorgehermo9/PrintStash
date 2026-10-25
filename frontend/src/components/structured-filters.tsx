import { FilterGroup } from "@/components/filter-group";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { PrintHistoryFields } from "@/components/print-history-fields";
import { historyKeys } from "@/lib/search-filters";
import type { SavedViewFilters } from "@/types";
import { FILTER_INPUT } from "@/lib/filter-input";
import { filterValueText } from "@/lib/filter-labels";
import { structuredLibraryFilterLabel } from "@/features/library/filters";
import { Separator } from "@/components/ui/separator";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import { ChevronDown, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { LibraryFilterMenu } from "@/components/library-filter-menu";
import { useState } from "react";

import { Checkbox } from "@/components/ui/checkbox";
import { Localized } from "@/components/ui/localized";
import type { FacetValueRead, ModelFacetsRead } from "@/types";

type FacetFilterKey =
  | "file_type"
  | "material_type"
  | "slicer_name"
  | "printer_model"
  | "revision_status"
  | "print_outcome"
  | "storage"
  | "printed";

type FilterKey = FacetFilterKey | "has_similar_candidates";

const GROUPS: FacetFilterKey[] = [
  "file_type",
  "material_type",
  "slicer_name",
  "printer_model",
  "revision_status",
  "printed",
  "print_outcome",
  "storage",
];

/** Sliced output is listed apart from the source formats it was made from. */
const SLICED_FILE_TYPES = new Set(["gcode"]);

export function StructuredFilters({
  facets,
  active,
  onChange,
  uploadedAfter,
  uploadedBefore,
  history = {},
  onDateChange,
  onClearAll,
  loading = false,
  error = false,
  layout = "sidebar",
  extraControls,
}: {
  layout?: "sidebar" | "inline";
  extraControls?: React.ReactNode;
  facets?: ModelFacetsRead;
  active: Partial<Record<FilterKey, string[]>>;
  onChange: (key: FilterKey, values: string[]) => void;
  uploadedAfter?: string;
  uploadedBefore?: string;
  history?: Pick<SavedViewFilters, (typeof historyKeys)[number]>;
  onDateChange?: (
    key: "uploaded_after" | "uploaded_before" | (typeof historyKeys)[number],
    value: string,
  ) => void;
  onClearAll?: () => void;
  loading?: boolean;
  error?: boolean;
}) {
  useUiLocale();
  const [open, setOpen] = useState<Partial<Record<FilterKey, boolean>>>({});
  const [extraOpen, setExtraOpen] = useState<{ history?: boolean; uploaded?: boolean }>({});
  const historyActive = historyKeys.some((key) => history[key] != null && history[key] !== "");
  const uploadedActive = !!uploadedAfter || !!uploadedBefore;

  function toggleGroup(key: FilterKey) {
    setOpen((current) => ({ ...current, [key]: !(current[key] ?? !!active[key]?.length) }));
  }

  function toggleValue(key: FilterKey, value: string) {
    const selected = active[key] ?? [];
    onChange(
      key,
      selected.includes(value) ? selected.filter((item) => item !== value) : [...selected, value],
    );
  }

  const count =
    Object.values(active).reduce((total, values) => total + (values?.length ?? 0), 0) +
    (uploadedAfter ? 1 : 0) +
    (uploadedBefore ? 1 : 0) +
    historyKeys.filter((key) => history[key] != null && history[key] !== "").length;
  function clearAll() {
    if (onClearAll) {
      onClearAll();
      return;
    }
    GROUPS.forEach((key) => onChange(key, []));
    onChange("has_similar_candidates", []);
    onDateChange?.("uploaded_after", "");
    onDateChange?.("uploaded_before", "");
    historyKeys.forEach((key) => onDateChange?.(key, ""));
  }
  const inline = layout === "inline";
  const groups: FacetFilterKey[] = inline
    ? ["file_type", "printed", ...GROUPS.filter((key) => key !== "file_type" && key !== "printed")]
    : GROUPS;
  const uploadedOpen = extraOpen.uploaded ?? uploadedActive;
  const historyOpen = extraOpen.history ?? historyActive;
  const uploadFields = (
    <div className={inline ? "grid grid-cols-2 gap-2" : "grid gap-2 py-1"}>
      <label className="min-w-0 space-y-1 text-xs text-muted-foreground">
        {uiText("After")}
        <Input
          type="date"
          value={uploadedAfter ?? ""}
          onChange={(event) => onDateChange?.("uploaded_after", event.target.value)}
          className={FILTER_INPUT}
        />
      </label>
      <label className="min-w-0 space-y-1 text-xs text-muted-foreground">
        {uiText("Before")}
        <Input
          type="date"
          value={uploadedBefore ?? ""}
          onChange={(event) => onDateChange?.("uploaded_before", event.target.value)}
          className={FILTER_INPUT}
        />
      </label>
    </div>
  );
  return (
    <Localized>
      <section>
        <div
          className={
            inline ? "flex items-center gap-2" : "mb-2 flex items-center justify-between pl-2 pr-1"
          }
        >
          <div className="flex items-center gap-2">
            <h3
              className={
                inline
                  ? "sr-only"
                  : "text-xs font-bold uppercase tracking-wider text-muted-foreground"
              }
            >
              {uiText("Model filters")}
            </h3>
            {loading && <Spinner size="sm" label={uiText("Loading filter values…")} />}
          </div>
          {!inline && count > 0 && (
            <button
              type="button"
              onClick={clearAll}
              className="flex items-center gap-1 text-3xs text-muted-foreground hover:text-foreground"
            >
              <X className="h-3 w-3" />
              {uiText(" Clear ")}
              {count}
            </button>
          )}
        </div>
        {error && (
          <p className="mx-2 mb-2 rounded-md border border-destructive/30 px-2 py-2 text-xs text-destructive">
            {uiText("Filter values could not be loaded.")}
          </p>
        )}
        <div className={inline ? "flex flex-wrap items-center gap-2" : "space-y-0.5"}>
          {!inline && (
            <label className="flex items-center gap-2 px-2 py-2 text-sm">
              <Checkbox
                ariaLabel={structuredLibraryFilterLabel("has_similar_candidates")}
                checked={active.has_similar_candidates?.includes("yes") ?? false}
                onChange={(checked) => onChange("has_similar_candidates", checked ? ["yes"] : [])}
              />
              {structuredLibraryFilterLabel("has_similar_candidates")}
            </label>
          )}
          {groups.map((key) => {
            const values: FacetValueRead[] = facets?.[key] ?? [];
            const selected = active[key] ?? [];
            const isOpen = open[key] ?? selected.length > 0;
            const contentId = `model-filter-${key}`;
            if (values.length === 0 && selected.length === 0) return null;
            const row = (item: FacetValueRead) => (
              <label
                key={item.value}
                className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-xs transition-colors duration-press hover:bg-muted"
              >
                <Checkbox
                  className="h-4 w-4"
                  ariaLabel={filterValueText(key, item.value)}
                  checked={selected.includes(item.value)}
                  onChange={() => toggleValue(key, item.value)}
                />
                <span className="min-w-0 flex-1 truncate">{filterValueText(key, item.value)}</span>
                <span className="min-w-[18px] rounded bg-muted px-1 py-0.5 text-center text-2xs font-medium text-muted-foreground">
                  {item.count}
                </span>
              </label>
            );
            const sources =
              key === "file_type"
                ? values.filter((item) => !SLICED_FILE_TYPES.has(item.value))
                : values;
            const sliced =
              key === "file_type" ? values.filter((item) => SLICED_FILE_TYPES.has(item.value)) : [];
            if (inline) {
              const missing = selected.filter(
                (value) => !values.some((item) => item.value === value),
              );
              if (key === "file_type" || key === "printed") {
                const options = [
                  ...sources,
                  ...sliced,
                  ...missing.map((value) => ({ value, count: null })),
                ];
                return (
                  <div
                    key={key}
                    role="group"
                    aria-label={structuredLibraryFilterLabel(key)}
                    className="flex min-w-0 flex-wrap items-center gap-1.5"
                  >
                    <span className="mr-1 text-xs font-medium text-muted-foreground">
                      {structuredLibraryFilterLabel(key)}
                    </span>
                    {options.map((item) =>
                      key === "printed" ? (
                        <Button
                          key={item.value}
                          variant={selected.includes(item.value) ? "ghost" : "outline"}
                          size="xs"
                          aria-pressed={selected.includes(item.value)}
                          onClick={() =>
                            onChange(key, selected.includes(item.value) ? [] : [item.value])
                          }
                          className={
                            selected.includes(item.value)
                              ? "h-8 bg-accent text-accent-foreground"
                              : "h-8"
                          }
                        >
                          {filterValueText(key, item.value)}
                          {item.count !== null && (
                            <span className="text-muted-foreground">{item.count}</span>
                          )}
                        </Button>
                      ) : (
                        <label
                          key={item.value}
                          className={`flex h-8 cursor-pointer items-center gap-1.5 rounded-md px-2 text-xs ${selected.includes(item.value) ? "bg-accent text-accent-foreground" : "hover:bg-muted"}`}
                        >
                          <Checkbox
                            className="h-4 w-4"
                            ariaLabel={filterValueText(key, item.value)}
                            checked={selected.includes(item.value)}
                            onChange={() => toggleValue(key, item.value)}
                          />
                          {filterValueText(key, item.value)}
                          {item.count !== null && (
                            <span className="text-muted-foreground">{item.count}</span>
                          )}
                        </label>
                      ),
                    )}
                  </div>
                );
              }
              return (
                <LibraryFilterMenu
                  key={key}
                  label={structuredLibraryFilterLabel(key)}
                  count={selected.length}
                >
                  {sources.map(row)}
                  {sources.length > 0 && sliced.length > 0 && <Separator className="my-1" />}
                  {sliced.map(row)}
                  {missing.map((value) => (
                    <label
                      key={value}
                      className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-xs hover:bg-muted"
                    >
                      <Checkbox
                        ariaLabel={filterValueText(key, value)}
                        checked
                        onChange={() => toggleValue(key, value)}
                      />
                      {filterValueText(key, value)}
                    </label>
                  ))}
                </LibraryFilterMenu>
              );
            }
            return (
              <FilterGroup
                key={key}
                id={contentId}
                label={structuredLibraryFilterLabel(key)}
                count={selected.length}
                open={isOpen}
                onToggle={() => toggleGroup(key)}
              >
                {sources.map(row)}
                {sources.length > 0 && sliced.length > 0 && <Separator className="my-1" />}
                {sliced.map(row)}
              </FilterGroup>
            );
          })}
          {extraControls}
          {inline ? (
            <>
              <label className="flex items-center gap-2 px-1 text-xs">
                <Checkbox
                  ariaLabel={structuredLibraryFilterLabel("has_similar_candidates")}
                  checked={active.has_similar_candidates?.includes("yes") ?? false}
                  onChange={(checked) => onChange("has_similar_candidates", checked ? ["yes"] : [])}
                />
                {structuredLibraryFilterLabel("has_similar_candidates")}
              </label>
              <Button
                variant={uploadedOpen ? "ghost" : "outline"}
                size="xs"
                className={uploadedOpen ? "h-8 bg-accent text-accent-foreground" : "h-8"}
                aria-expanded={uploadedOpen}
                aria-controls="library-uploaded-fields"
                onClick={() => setExtraOpen((current) => ({ ...current, uploaded: !uploadedOpen }))}
              >
                {uiText("Uploaded")}
                <ChevronDown className="h-3.5 w-3.5" aria-hidden />
              </Button>
              <Button
                variant={historyOpen ? "ghost" : "outline"}
                size="xs"
                className={historyOpen ? "h-8 bg-accent text-accent-foreground" : "h-8"}
                aria-expanded={historyOpen}
                aria-controls="library-history-fields"
                onClick={() => setExtraOpen((current) => ({ ...current, history: !historyOpen }))}
              >
                {uiText("Print history")}
                <ChevronDown className="h-3.5 w-3.5" aria-hidden />
              </Button>
            </>
          ) : (
            <>
              <FilterGroup
                id="model-filter-history"
                label={uiText("Print history")}
                count={
                  historyKeys.filter((key) => history[key] != null && history[key] !== "").length
                }
                open={extraOpen.history ?? historyActive}
                onToggle={() =>
                  setExtraOpen((current) => ({
                    ...current,
                    history: !(current.history ?? historyActive),
                  }))
                }
              >
                <PrintHistoryFields
                  compact
                  value={history}
                  onChange={(key, value) => onDateChange?.(key, value)}
                />
              </FilterGroup>
              <FilterGroup
                id="model-filter-uploaded"
                label={uiText("Uploaded")}
                count={(uploadedAfter ? 1 : 0) + (uploadedBefore ? 1 : 0)}
                open={extraOpen.uploaded ?? uploadedActive}
                onToggle={() =>
                  setExtraOpen((current) => ({
                    ...current,
                    uploaded: !(current.uploaded ?? uploadedActive),
                  }))
                }
              >
                {uploadFields}
              </FilterGroup>
            </>
          )}
        </div>
        {inline && (uploadedOpen || historyOpen) && (
          <div className="mt-3 flex flex-wrap items-start gap-x-6 gap-y-3 border-t border-border pt-3">
            {uploadedOpen && (
              <fieldset id="library-uploaded-fields" className="min-w-0 max-w-md flex-1 basis-72">
                <legend className="mb-1 text-xs font-medium">{uiText("Uploaded")}</legend>
                {uploadFields}
              </fieldset>
            )}
            {historyOpen && (
              <fieldset id="library-history-fields" className="min-w-0 flex-[2] basis-96">
                <legend className="mb-1 text-xs font-medium">{uiText("Print history")}</legend>
                <PrintHistoryFields
                  layout="inline"
                  value={history}
                  onChange={(key, value) => onDateChange?.(key, value)}
                />
              </fieldset>
            )}
          </div>
        )}
      </section>
    </Localized>
  );
}
