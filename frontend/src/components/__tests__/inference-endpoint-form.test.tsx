/** The guided server form exposes essential connection facts while preserving server options. */
import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { InferenceEndpointForm } from "@/components/inference-endpoint-form";
import { renderApp } from "@/test-support/render";

describe("InferenceEndpointForm", () => {
  it("keeps server tuning optional in guided setup", async () => {
    renderApp(<InferenceEndpointForm compact onSaved={() => {}} />);
    expect(
      screen.getByRole("spinbutton", { name: "Model output size (from your server)" }),
    ).toBeVisible();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Model revision")).not.toBeInTheDocument();
    expect(screen.queryByText("Server options")).not.toBeInTheDocument();
  });
});
