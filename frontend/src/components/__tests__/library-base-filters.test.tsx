/**
 * Desktop filter pickers retain tag selection and printer location semantics.
 * The real controlled fields drive local state, including the permission gate.
 */
import "@testing-library/jest-dom/vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { renderApp } from "@/test-support/render";
import { LibraryBaseFilters } from "@/components/library-base-filters";
import { aPrinter } from "@/test-support/factories";

function Filters({ canViewPrinters = true }: { canViewPrinters?: boolean }) {
  const [tags, setTags] = useState<string[]>([]);
  const [printer, setPrinter] = useState<number | null>(null);
  const [presence, setPresence] = useState<"any" | "none" | null>(null);
  return (
    <>
      <LibraryBaseFilters
        tags={[
          { id: 1, name: "Workshop", slug: "workshop", model_count: 2 },
          { id: 2, name: "House", slug: "house", model_count: 1 },
        ]}
        printers={[aPrinter({ id: 7, name: "Workshop printer" })]}
        selectedTags={tags}
        selectedPrinterId={printer}
        selectedPrinterPresence={presence}
        onTagsChange={setTags}
        onPrinterChange={(value) => {
          setPrinter(value);
          setPresence(null);
        }}
        onPrinterPresenceChange={(value) => {
          setPrinter(null);
          setPresence(value);
        }}
        canViewPrinters={canViewPrinters}
      />
      <output aria-label="Selected location">{printer ?? presence ?? "all"}</output>
    </>
  );
}

describe("LibraryBaseFilters", () => {
  it.each([{ label: "Tags" }, { label: "Printer" }])(
    "names the $label filter dialog",
    async ({ label }) => {
      const user = userEvent.setup();
      renderApp(<Filters />);

      await user.click(screen.getByRole("button", { name: label }));

      expect(screen.getByRole("dialog", { name: label })).toBeVisible();
    },
  );

  it("retains selected tags when searching other available tags", async () => {
    const user = userEvent.setup();
    renderApp(<Filters />);
    await user.click(screen.getByRole("button", { name: "Tags" }));
    await user.click(screen.getByRole("checkbox", { name: "Workshop" }));
    await user.type(screen.getByRole("textbox", { name: "Filter tags..." }), "House");
    await user.click(screen.getByRole("checkbox", { name: "House" }));
    await user.clear(screen.getByRole("textbox", { name: "Filter tags..." }));
    expect(screen.getByRole("checkbox", { name: "Workshop" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "House" })).toBeChecked();
  });

  it("explains an empty tag search", async () => {
    const user = userEvent.setup();
    renderApp(<Filters />);
    await user.click(screen.getByRole("button", { name: "Tags" }));
    await user.type(screen.getByRole("textbox", { name: "Filter tags..." }), "missing");
    expect(screen.getByText("No matching tags.")).toBeVisible();
  });

  it.each([
    { label: "a specific printer", button: "Workshop printer", value: "7" },
    { label: "any printer", button: "On a printer", value: "any" },
    { label: "vault only", button: "Vault only", value: "none" },
  ])("selects $label", async ({ button, value }) => {
    const user = userEvent.setup();
    renderApp(<Filters />);
    await user.click(screen.getByRole("button", { name: "Printer" }));
    await user.click(screen.getByRole("button", { name: button }));
    expect(screen.getByRole("status", { name: "Selected location" })).toHaveTextContent(value);
  });

  it("clears a printer restriction with Any location", async () => {
    const user = userEvent.setup();
    renderApp(<Filters />);
    await user.click(screen.getByRole("button", { name: "Printer" }));
    await user.click(screen.getByRole("button", { name: "Workshop printer" }));
    await user.click(screen.getByRole("button", { name: "Any location" }));
    expect(screen.getByRole("status", { name: "Selected location" })).toHaveTextContent("all");
  });

  it("does not expose printer location without printer access", () => {
    renderApp(<Filters canViewPrinters={false} />);
    expect(screen.queryByRole("button", { name: "Printer" })).not.toBeInTheDocument();
  });
});
