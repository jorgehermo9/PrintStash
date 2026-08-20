import { afterEach, describe, expect, it, vi } from "vitest";

import {
  CARD_METRIC_STORAGE_KEY,
  CURRENCY_OPTIONS,
  DEFAULT_CARD_METRICS,
  DEFAULT_METADATA_PREFERENCES,
  LAST_VIEW_STORAGE_KEY,
  METADATA_PREFERENCE_STORAGE_KEY,
  formatBytes,
  formatCost,
  formatCurrency,
  formatDuration,
  formatGrams,
  formatMillimeters,
  formatPercent,
  formatTemperature,
  lastVaultHref,
  readCardMetrics,
  readLastCollection,
  readLastView,
  readMetadataPreferences,
  rememberLastCollection,
  rememberLastView,
  timeAgo,
  timeAgoShort,
  writeCardMetrics,
  writeMetadataPreferences,
  type CardMetrics,
} from "../index";

describe("display formatters", () => {
  afterEach(() => vi.useRealTimers());

  it("preserves byte, duration, scalar, and missing-value rules", () => {
    expect(formatBytes(null)).toBe("—");
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(1024 ** 5)).toContain("TB");
    expect(formatDuration(3661)).toBe("1h 1m");
    expect(formatDuration(125)).toBe("2m 5s");
    expect(formatDuration(0)).toBe("—");
    expect(formatMillimeters(0.2)).toBe("0.2mm");
    expect(formatMillimeters(0)).toBe("—");
    expect(formatPercent(0)).toBe("0%");
    expect(formatPercent(88.88888888888889)).toBe("88.9%");
    expect(formatGrams(1231.0000000000002)).toBe("1,231g");
    expect(formatGrams(0)).toBe("—");
    expect(formatTemperature(0)).toBe("0°C");
    expect(formatCost(24.5)).toBe("24.50");
    expect(formatCost(0)).toBe("—");
  });

  it("preserves relative-time cutoffs and absolute fallback", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-14T12:00:00Z"));

    expect(timeAgo("2026-06-14T11:45:00Z")).toBe("15m ago");
    expect(timeAgo("2026-06-14T09:00:00Z")).toBe("3h ago");
    expect(timeAgo("2026-06-11T12:00:00Z")).toBe("3d ago");
    expect(timeAgo("2026-05-01T12:00:00Z")).toMatch(/May/);
    expect(timeAgoShort("2026-06-14T06:00:00Z")).toBe("Today");
    expect(timeAgoShort("2026-06-13T06:00:00Z")).toBe("Yesterday");
  });
});

describe("currency", () => {
  it("formats real values and distinguishes zero from missing data", () => {
    expect(formatCurrency(null, "USD")).toBe("—");
    expect(formatCurrency(0, "USD")).toContain("0.00");
    expect(formatCurrency(12.5, "EUR")).toContain("12.50");
    expect(formatCurrency(5, "")).toContain("5.00");
    expect(formatCurrency(5, "NOTACODE")).toBe("5.00 NOTACODE");
  });

  it("exports the existing well-formed picker options", () => {
    expect(CURRENCY_OPTIONS).toHaveLength(15);
    for (const option of CURRENCY_OPTIONS) {
      expect(option.code).toMatch(/^[A-Z]{3}$/);
      expect(option.label).not.toBe("");
    }
  });
});

describe("card metric preferences", () => {
  it("defaults and round-trips a valid three-metric selection", () => {
    expect(readCardMetrics()).toEqual(DEFAULT_CARD_METRICS);
    const metrics: CardMetrics = ["material", "slicer", "file_count"];
    writeCardMetrics(metrics);
    expect(readCardMetrics()).toEqual(metrics);
  });

  it("rejects malformed, short, and unknown stored selections", () => {
    for (const raw of [
      "{not json",
      JSON.stringify(["material", "slicer"]),
      JSON.stringify(["material", "slicer", "not_a_metric"]),
    ]) {
      window.localStorage.setItem(CARD_METRIC_STORAGE_KEY, raw);
      expect(readCardMetrics()).toEqual(DEFAULT_CARD_METRICS);
    }
  });
});

describe("metadata preferences", () => {
  it("defaults every field to visible and round-trips false values", () => {
    expect(Object.values(readMetadataPreferences()).every(Boolean)).toBe(true);
    const preferences = { ...DEFAULT_METADATA_PREFERENCES, material: false };
    writeMetadataPreferences(preferences);
    expect(readMetadataPreferences().material).toBe(false);
  });

  it("merges partial data and treats only literal false as hidden", () => {
    window.localStorage.setItem(
      METADATA_PREFERENCE_STORAGE_KEY,
      JSON.stringify({ walls: false, supports: "yes" }),
    );
    const preferences = readMetadataPreferences();
    expect(preferences.walls).toBe(false);
    expect(preferences.supports).toBe(true);
    expect(preferences.material).toBe(true);
  });

  it("falls back to defaults for malformed JSON", () => {
    window.localStorage.setItem(METADATA_PREFERENCE_STORAGE_KEY, "broken");
    expect(readMetadataPreferences()).toEqual(DEFAULT_METADATA_PREFERENCES);
  });
});

describe("last vault context", () => {
  it("remembers, encodes, and clears collection paths", () => {
    expect(readLastCollection()).toBeNull();
    expect(lastVaultHref()).toBe("/");

    rememberLastCollection("spoolers/old prints");
    expect(readLastCollection()).toBe("spoolers/old prints");
    expect(lastVaultHref()).toBe("/?c=spoolers%2Fold%20prints");

    rememberLastCollection(null);
    expect(lastVaultHref()).toBe("/");
  });

  it("remembers the documents tab and combines it with the collection", () => {
    expect(readLastView()).toBe("models");
    rememberLastCollection("spoolers");
    rememberLastView("docs");

    expect(readLastView()).toBe("docs");
    expect(lastVaultHref()).toBe("/?c=spoolers&v=docs");

    window.localStorage.setItem(LAST_VIEW_STORAGE_KEY, "unexpected");
    expect(readLastView()).toBe("models");
  });
});
