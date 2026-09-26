/** Controlled facets translate without changing printer or filament names. */
import { afterEach, describe, expect, it } from "vitest";
import { filterValueText } from "../filter-labels";
import { setLocale } from "../locale";
afterEach(() => setLocale("en"));
describe("filterValueText", () => {
  it("localizes controlled revision states", () => {
    setLocale("es");
    expect(filterValueText("revision_status", "known_good")).toBe("Verificado");
  });
  it("preserves user-authored facet values", () => {
    setLocale("es");
    expect(filterValueText("printer_model", "known_good")).toBe("known_good");
    expect(filterValueText("material_type", "Unknown")).toBe("Unknown");
  });
  it("shows print duration filters in readable units", () => {
    expect(filterValueText("print_duration_max_s", "10800")).toBe("3h 0m");
    expect(filterValueText("print_duration_min_s", "0")).toBe("0s");
    expect(filterValueText("print_duration_max_s", "invalid")).toBe("invalid");
  });
});
