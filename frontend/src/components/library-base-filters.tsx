import { useState } from "react";
import { LibraryFilterMenu } from "@/components/library-filter-menu";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { useI18n } from "@/lib/i18n";
import { FILTER_INPUT } from "@/lib/filter-input";
import type { PrinterRead, TagRead } from "@/types";

/** Tags and printer location share ModelGrid's existing URL-owned filter state. */
export function LibraryBaseFilters({
  tags,
  printers,
  selectedTags,
  selectedPrinterId,
  selectedPrinterPresence,
  onTagsChange,
  onPrinterChange,
  onPrinterPresenceChange,
  canViewPrinters,
}: {
  tags: TagRead[];
  printers: PrinterRead[];
  selectedTags: string[];
  selectedPrinterId: number | null;
  selectedPrinterPresence: "any" | "none" | null;
  onTagsChange: (tags: string[]) => void;
  onPrinterChange: (id: number | null) => void;
  onPrinterPresenceChange: (presence: "any" | "none" | null) => void;
  canViewPrinters: boolean;
}) {
  const { t } = useI18n();
  const [query, setQuery] = useState("");
  const visibleTags = tags.filter((tag) => tag.name.toLowerCase().includes(query.toLowerCase()));
  return (
    <>
      {canViewPrinters && (
        <LibraryFilterMenu
          label={t("Printer")}
          count={Number(selectedPrinterId !== null || selectedPrinterPresence !== null)}
        >
          <div className="space-y-1">
            <Button
              variant="ghost"
              size="sm"
              aria-pressed={selectedPrinterId === null && selectedPrinterPresence === null}
              className={`w-full justify-start ${selectedPrinterId === null && selectedPrinterPresence === null ? "bg-accent text-accent-foreground" : ""}`}
              onClick={() => onPrinterChange(null)}
            >
              {t("Any location")}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className={`w-full justify-start ${selectedPrinterPresence === "any" ? "bg-accent text-accent-foreground" : ""}`}
              onClick={() => onPrinterPresenceChange("any")}
            >
              {t("On a printer")}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className={`w-full justify-start ${selectedPrinterPresence === "none" ? "bg-accent text-accent-foreground" : ""}`}
              onClick={() => onPrinterPresenceChange("none")}
            >
              {t("Vault only")}
            </Button>
            {printers.map((printer) => (
              <Button
                key={printer.id}
                variant="ghost"
                size="sm"
                className={`w-full justify-start ${selectedPrinterId === printer.id ? "bg-accent text-accent-foreground" : ""}`}
                onClick={() => onPrinterChange(printer.id)}
              >
                <span className="truncate">{printer.name}</span>
              </Button>
            ))}
          </div>
        </LibraryFilterMenu>
      )}
      {(tags.length > 0 || selectedTags.length > 0) && (
        <LibraryFilterMenu label={t("Tags")} count={selectedTags.length}>
          <Input
            aria-label={t("Filter tags...")}
            placeholder={t("Filter tags...")}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            className={FILTER_INPUT}
          />
          <div className="mt-2 space-y-1">
            {visibleTags.map((tag) => (
              <label
                key={tag.id}
                className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-xs hover:bg-muted"
              >
                <Checkbox
                  ariaLabel={tag.name}
                  checked={selectedTags.includes(tag.slug)}
                  onChange={(checked) =>
                    onTagsChange(
                      checked
                        ? [...selectedTags, tag.slug]
                        : selectedTags.filter((slug) => slug !== tag.slug),
                    )
                  }
                />
                <span className="min-w-0 flex-1 truncate">{tag.name}</span>
                <span className="text-muted-foreground">
                  {tag.model_count + (tag.multipart_model_count ?? 0)}
                </span>
              </label>
            ))}
            {visibleTags.length === 0 && (
              <p className="px-2 py-1 text-xs text-muted-foreground">{t("No matching tags.")}</p>
            )}
          </div>
        </LibraryFilterMenu>
      )}
    </>
  );
}
