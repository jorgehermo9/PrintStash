import { uiText } from "@/lib/locale";
import type { MessageKey } from "@/lib/locale";
import {
  EVIDENCE_CLASSES,
  type EvidenceClass,
  type ReviewState,
  type SimilarityCandidate,
  type SimilarityFilters,
  type SimilarityRun,
} from "@/types/similarity";

const LABELS = {
  identical_geometry: "similarity.identical",
  rescaled: "similarity.rescaled",
  mirrored: "similarity.mirrored",
  rescaled_mirrored: "similarity.rescaledMirrored",
  remeshed: "similarity.remeshed",
  repaired: "similarity.repaired",
  // eslint-disable-next-line anti-slop/no-shape-in-symbol-names -- Published geometric evidence class, not a structural placeholder.
  similar_shape: "similarity.similarShape",
  component_of: "similarity.componentOf",
  plate_of: "similarity.plateOf",
} satisfies Record<EvidenceClass, MessageKey>;
export function evidenceLabel(value: EvidenceClass) {
  return uiText(LABELS[value]);
}
export function evidenceDescription(candidate: SimilarityCandidate): string {
  const proof = candidate.summary;
  if (candidate.freshness === "stale") return uiText("similarity.staleHelp");
  if (proof.mirror_ambiguous) return uiText("similarity.mirrorAmbiguous");
  if (candidate.evidence_class === "plate_of")
    return uiText("similarity.copies", { count: proof.copies ?? 1 });
  if (candidate.evidence_class === "component_of") return uiText("similarity.componentHelp");
  if (candidate.evidence_class === "rescaled" || candidate.evidence_class === "rescaled_mirrored") {
    const scale = proof.scale_factor ?? 1;
    return Math.abs(scale - 25.4) < 0.01 || Math.abs(scale - 1 / 25.4) < 0.0001
      ? uiText("similarity.inchConversion")
      : uiText("similarity.scaleFactor", { factor: scale.toPrecision(4) });
  }
  if (candidate.exact_equivalence && candidate.evidence_class === "identical_geometry")
    return uiText("similarity.exactHelp");
  return uiText("similarity.sampledHelp");
}
export function isSimilarityRunActive(run: SimilarityRun) {
  return run.state === "queued" || run.state === "running" || run.state === "cancelling";
}

const REVIEW_STATES: ReviewState[] = ["open", "confirmed", "later", "rejected"];

/** Read only supported review filters; malformed URLs never reach the API. */
export function readSimilarityFilters(params: URLSearchParams): SimilarityFilters {
  const confidence = Number(params.get("confidence"));
  const collection = Number(params.get("collection"));
  const freshness = params.get("freshness");
  const format = params.get("format");
  const source = params.get("source");
  return {
    review_state: REVIEW_STATES.find((state) => state === params.get("review")) ?? "open",
    freshness: freshness === "any" ? undefined : freshness === "stale" ? "stale" : "current",
    evidence_class: EVIDENCE_CLASSES.find((value) => value === params.get("match")),
    minimum_confidence:
      Number.isInteger(confidence) && confidence >= 50 && confidence <= 100
        ? confidence / 100
        : undefined,
    collection_id: Number.isSafeInteger(collection) && collection > 0 ? collection : undefined,
    file_type:
      format !== null && ["stl", "obj", "3mf", "step"].includes(format) ? format : undefined,
    source: source === "vault" || source === "external" ? source : undefined,
    known_good: params.get("known_good") === "1" ? true : undefined,
  };
}

export function similarityFiltersHref(filters: SimilarityFilters): string {
  const params = new URLSearchParams();
  if (filters.review_state && filters.review_state !== "open")
    params.set("review", filters.review_state);
  if (filters.evidence_class) params.set("match", filters.evidence_class);
  if (filters.freshness !== "current") params.set("freshness", filters.freshness ?? "any");
  if (filters.minimum_confidence !== undefined)
    params.set("confidence", String(Math.round(filters.minimum_confidence * 100)));
  if (filters.collection_id !== undefined) params.set("collection", String(filters.collection_id));
  if (filters.file_type) params.set("format", filters.file_type);
  if (filters.source) params.set("source", filters.source);
  if (filters.known_good) params.set("known_good", "1");
  const query = params.toString();
  return query ? `/library/similar?${query}` : "/library/similar";
}
