import { useEffect, useState } from "react";

export type PreviewQuality = "performance" | "balanced" | "detail";
export type ScreenshotScale = 1 | 2 | 3;

export interface PreviewPreferences {
  previewQuality: PreviewQuality;
  screenshotScale: ScreenshotScale;
}

export const PREVIEW_PREFERENCES_STORAGE_KEY = "printstash.preview.preferences:v1";
export const PREVIEW_PREFERENCES_EVENT = "printstash:preview-preferences-changed";

export const DEFAULT_PREVIEW_PREFERENCES: PreviewPreferences = {
  previewQuality: "balanced",
  screenshotScale: 2,
};

const PREVIEW_PIXEL_RATIOS: Record<PreviewQuality, number> = {
  performance: 1,
  balanced: 1.5,
  detail: 2,
};

function isPreviewQuality(value: unknown): value is PreviewQuality {
  return value === "performance" || value === "balanced" || value === "detail";
}

function isScreenshotScale(value: unknown): value is ScreenshotScale {
  return value === 1 || value === 2 || value === 3;
}

export function readPreviewPreferences(): PreviewPreferences {
  if (typeof window === "undefined") return DEFAULT_PREVIEW_PREFERENCES;
  try {
    const stored = JSON.parse(
      window.localStorage.getItem(PREVIEW_PREFERENCES_STORAGE_KEY) ?? "{}",
    ) as Partial<PreviewPreferences>;
    return {
      previewQuality: isPreviewQuality(stored.previewQuality)
        ? stored.previewQuality
        : DEFAULT_PREVIEW_PREFERENCES.previewQuality,
      screenshotScale: isScreenshotScale(stored.screenshotScale)
        ? stored.screenshotScale
        : DEFAULT_PREVIEW_PREFERENCES.screenshotScale,
    };
  } catch {
    return DEFAULT_PREVIEW_PREFERENCES;
  }
}

export function writePreviewPreferences(preferences: PreviewPreferences): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(PREVIEW_PREFERENCES_STORAGE_KEY, JSON.stringify(preferences));
  window.dispatchEvent(
    new CustomEvent<PreviewPreferences>(PREVIEW_PREFERENCES_EVENT, {
      detail: preferences,
    }),
  );
}

export function previewPixelRatio(quality: PreviewQuality): number {
  return PREVIEW_PIXEL_RATIOS[quality];
}

export function usePreviewPreferences(): PreviewPreferences {
  const [preferences, setPreferences] = useState(readPreviewPreferences);

  useEffect(() => {
    const refresh = () => setPreferences(readPreviewPreferences());
    const receive = (event: Event) => {
      setPreferences((event as CustomEvent<PreviewPreferences>).detail ?? readPreviewPreferences());
    };
    window.addEventListener("storage", refresh);
    window.addEventListener(PREVIEW_PREFERENCES_EVENT, receive);
    return () => {
      window.removeEventListener("storage", refresh);
      window.removeEventListener(PREVIEW_PREFERENCES_EVENT, receive);
    };
  }, []);

  return preferences;
}
