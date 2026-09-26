/*
 * The page a fresh installation shows when this browser may not claim it.
 *
 * This replaced a one-line "registration is disabled" alert that gave an operator
 * nothing to act on — the dead end reported from an Unraid install in #248. Each
 * reason has to name a change the operator can make in their deployment. When the
 * first-run settings contradict each other, the page is the only place a
 * one-click store install explains itself, so it must name the variables to
 * change and both ways to fix them.
 */
import "@testing-library/jest-dom/vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SetupUnavailable } from "@/components/setup-unavailable";
import { renderApp } from "@/test-support/render";
import type { SetupStatus } from "@/types";

function renderPage(
  reason: NonNullable<SetupStatus["unavailable_reason"]>,
  options: { variables?: string[]; onRetry?: () => void } = {},
) {
  return renderApp(
    <SetupUnavailable
      reason={reason}
      host="vault.example.net"
      variables={options.variables ?? []}
      onRetry={options.onRetry ?? vi.fn<() => void>()}
    />,
  );
}

function exits() {
  return within(screen.getByRole("list", { name: "Ways to continue" }));
}

describe("SetupUnavailable", () => {
  it("names the host it refused", () => {
    renderPage("untrusted_host");

    expect(screen.getByRole("alert")).toHaveTextContent("vault.example.net");
  });

  it("offers the refused host as an allowed hostname", () => {
    renderPage("untrusted_host");

    expect(exits().getByText("VAULT_SETUP_ALLOWED_HOSTS=vault.example.net")).toBeVisible();
  });

  it("offers the local network address for an untrusted host", () => {
    renderPage("untrusted_host");

    expect(exits().getByText(/local network address/)).toBeVisible();
  });

  it("offers turning on registration when it is disabled", () => {
    renderPage("disabled");

    expect(exits().getByText("VAULT_SETUP_MODE=trusted_network")).toBeVisible();
  });

  it("does not suggest another address when registration is disabled", () => {
    renderPage("disabled");

    expect(exits().queryByText(/VAULT_SETUP_ALLOWED_HOSTS/)).not.toBeInTheDocument();
  });

  it.each([{ reason: "disabled" as const }, { reason: "untrusted_host" as const }])(
    "offers the environment administrator with its mode for $reason",
    ({ reason }) => {
      renderPage(reason);

      expect(
        exits().getByText(/VAULT_SETUP_MODE=environment\s+VAULT_SETUP_ADMIN_USERNAME=/),
      ).toBeVisible();
    },
  );

  it("explains that the deployment creates the administrator in environment mode", () => {
    renderPage("environment");

    expect(screen.getByRole("alert")).toHaveTextContent(/creates the administrator/);
  });

  it("offers no browser exit in environment mode", () => {
    renderPage("environment");

    expect(screen.queryByRole("list", { name: "Ways to continue" })).not.toBeInTheDocument();
  });

  it.each([
    { reason: "admin_credentials_missing" as const },
    { reason: "admin_credentials_invalid" as const },
    { reason: "admin_credentials_without_environment_mode" as const },
  ])("names the variables to fix for $reason", ({ reason }) => {
    renderPage(reason, { variables: ["VAULT_SETUP_ADMIN_PASSWORD"] });

    expect(screen.getByRole("alert")).toHaveTextContent("VAULT_SETUP_ADMIN_PASSWORD");
  });

  it.each([
    { reason: "admin_credentials_missing" as const },
    { reason: "admin_credentials_invalid" as const },
  ])("offers setting the credentials or registering in the browser for $reason", ({ reason }) => {
    renderPage(reason, { variables: ["VAULT_SETUP_ADMIN_PASSWORD"] });

    expect(
      exits()
        .getAllByRole("listitem")
        .map((item) => item.textContent),
    ).toEqual([
      expect.stringContaining("VAULT_SETUP_ADMIN_PASSWORD=<your password>"),
      expect.stringContaining("VAULT_SETUP_MODE=trusted_network"),
    ]);
  });

  it("offers switching to environment mode for credentials in the wrong mode", () => {
    renderPage("admin_credentials_without_environment_mode", {
      variables: ["VAULT_SETUP_ADMIN_USERNAME"],
    });

    expect(exits().getByText("VAULT_SETUP_MODE=environment")).toBeVisible();
  });

  it("checks again on request", async () => {
    const onRetry = vi.fn<() => void>();
    renderPage("untrusted_host", { onRetry });

    await userEvent.click(screen.getByRole("button", { name: "Check again" }));

    expect(onRetry).toHaveBeenCalledOnce();
  });
});
