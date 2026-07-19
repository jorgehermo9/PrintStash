import { Languages } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";

export function LocaleToggle() {
  const { locale, setLocale, t } = useI18n();
  const next = locale === "en" ? "es" : "en";

  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      onClick={() => setLocale(next)}
      aria-label={`${t("locale.label")}: ${t(`locale.${locale}`)}`}
      title={`${t("locale.label")}: ${t(`locale.${locale}`)}`}
    >
      <Languages className="h-4 w-4" aria-hidden />
      <span className="sr-only">{t(`locale.${next}`)}</span>
    </Button>
  );
}
