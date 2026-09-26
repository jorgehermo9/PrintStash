import { knownUiText } from "./locale";
import { formatDuration } from "./format";

/** Only controlled facet values are UI enums; material/printer names remain data. */
export function filterValueText(key: string, value: string): string {
  if (key === "print_duration_min_s" || key === "print_duration_max_s") {
    const seconds = Number(value);
    if (Number.isFinite(seconds) && seconds >= 0) {
      return seconds === 0 ? "0s" : formatDuration(seconds);
    }
  }
  return ["revision_status", "print_outcome", "storage", "printed"].includes(key)
    ? knownUiText(value)
    : value;
}
