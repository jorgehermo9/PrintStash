/** Review copy distinguishes physical equivalence, approximate evidence and stale inputs. */
import { afterEach, describe, expect, it } from "vitest";
import { setLocale } from "@/lib/locale";
import {
  evidenceDescription,
  evidenceLabel,
  readSimilarityFilters,
  similarityFiltersHref,
} from "@/lib/similarity";
import { aSimilarityCandidate } from "@/test-support/similarity";

afterEach(() => setLocale("en"));

describe("Similarity review copy", () => {
  it.each([
    { locale: "en", identical: "Identical geometry", approximate: "Similar shape" },
    { locale: "es", identical: "Geometría idéntica", approximate: "Forma similar" },
  ] as const)("localizes evidence classes in $locale", ({ locale, identical, approximate }) => {
    setLocale(locale);
    expect(evidenceLabel("identical_geometry")).toBe(identical);
    expect(evidenceLabel("similar_shape")).toBe(approximate);
  });
  it("prioritizes stale-source guidance over exact evidence", () => {
    expect(evidenceDescription(aSimilarityCandidate({ freshness: "stale" }))).toMatch(
      /changed|unavailable/,
    );
  });
  it("explains ambiguous mirror history", () => {
    expect(
      evidenceDescription(aSimilarityCandidate({ summary: { mirror_ambiguous: true } })),
    ).toMatch(/symmetric|reflection/i);
  });
  it.each([25.4, 1 / 25.4])("identifies inch conversion at %s scale", (scale) => {
    expect(
      evidenceDescription(
        aSimilarityCandidate({ evidence_class: "rescaled", summary: { scale_factor: scale } }),
      ),
    ).toMatch(/inch/i);
  });
  it("shows the physical scale factor for other variants", () => {
    expect(
      evidenceDescription(
        aSimilarityCandidate({ evidence_class: "rescaled", summary: { scale_factor: 2 } }),
      ),
    ).toContain("2.000");
  });
  it("does not describe sampled evidence as exact", () => {
    expect(
      evidenceDescription(
        aSimilarityCandidate({ evidence_class: "remeshed", exact_equivalence: false }),
      ),
    ).toMatch(/sample/i);
  });
});

describe("Similarity review URLs", () => {
  it("round trips every supported review restriction", () => {
    const filters = {
      review_state: "later",
      freshness: "stale",
      evidence_class: "rescaled",
      minimum_confidence: 0.94,
      collection_id: 12,
      file_type: "3mf",
      source: "vault",
      known_good: true,
    } as const;

    const href = similarityFiltersHref(filters);

    expect(readSimilarityFilters(new URL(href, "https://example.test").searchParams)).toEqual(
      filters,
    );
  });

  it("restores unrestricted evidence freshness", () => {
    expect(readSimilarityFilters(new URLSearchParams("freshness=any"))).toMatchObject({
      review_state: "open",
      freshness: undefined,
    });
  });

  it("rejects unsupported filter URL values", () => {
    expect(
      readSimilarityFilters(
        new URLSearchParams(
          "review=unknown&match=invalid&freshness=unknown&confidence=101&collection=-1&format=exe&source=unknown&known_good=yes",
        ),
      ),
    ).toEqual({
      review_state: "open",
      freshness: "current",
      evidence_class: undefined,
      minimum_confidence: undefined,
      collection_id: undefined,
      file_type: undefined,
      source: undefined,
      known_good: undefined,
    });
  });

  it("rejects fractional percentages that the confidence control cannot reproduce", () => {
    const filters = readSimilarityFilters(new URLSearchParams("confidence=75.5&review=later"));

    expect(filters.minimum_confidence).toBeUndefined();
    expect(similarityFiltersHref(filters)).toBe("/library/similar?review=later");
  });

  it("keeps the default review URL concise", () => {
    expect(similarityFiltersHref({ review_state: "open", freshness: "current" })).toBe(
      "/library/similar",
    );
  });
});
