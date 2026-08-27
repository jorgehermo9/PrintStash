/*
 * Editing SSO settings without sending the stored client secret back.
 *
 * The card loads the current configuration, which includes a *masked* secret. If
 * saving replays that mask as though it were the value, the real secret is
 * overwritten with a row of asterisks and every SSO login stops working — on the
 * one screen where the operator cannot log in to fix it.
 *
 * So the save asserts what leaves the client: the fields the operator changed,
 * and the secret only when they typed a new one.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  OidcSettingsCard,
  type OidcConfig,
  type OidcConfigUpdate,
} from "@/components/oidc-settings-card";

const config: OidcConfig = {
  oidc_enabled: false,
  oidc_issuer_url: "https://auth.example.test/application/o/printstash",
  oidc_client_id: "printstash",
  has_oidc_client_secret: true,
  oidc_scopes: "openid profile email groups",
  oidc_username_claim: "preferred_username",
  oidc_groups_claim: "groups",
  oidc_admin_groups: "printstash-admins",
  oidc_display_name: "Authentik",
  oidc_redirect_uri: "",
  oidc_allow_insecure_http: false,
};

// A faithful stand-in for the real endpoint: it applies the patch and reports
// that a secret is stored, but never echoes the secret back.
function savedConfig(payload: OidcConfigUpdate): OidcConfig {
  return {
    oidc_enabled: payload.oidc_enabled,
    oidc_issuer_url: payload.oidc_issuer_url,
    oidc_client_id: payload.oidc_client_id,
    oidc_scopes: payload.oidc_scopes,
    oidc_username_claim: payload.oidc_username_claim,
    oidc_groups_claim: payload.oidc_groups_claim,
    oidc_admin_groups: payload.oidc_admin_groups,
    oidc_display_name: payload.oidc_display_name,
    oidc_redirect_uri: payload.oidc_redirect_uri,
    oidc_allow_insecure_http: payload.oidc_allow_insecure_http,
    has_oidc_client_secret: true,
  };
}

describe("OidcSettingsCard", () => {
  it("loads and saves OIDC settings without replaying stored secret", async () => {
    const loadConfig = vi.fn<() => Promise<OidcConfig>>().mockResolvedValue(config);
    const saveConfig = vi
      .fn<(payload: OidcConfigUpdate) => Promise<OidcConfig>>()
      .mockImplementation((payload) => Promise.resolve(savedConfig(payload)));
    const user = userEvent.setup();
    render(<OidcSettingsCard loadConfig={loadConfig} saveConfig={saveConfig} />);

    expect(await screen.findByDisplayValue("Authentik")).toBeInTheDocument();
    expect(screen.getByLabelText("Client secret")).toHaveAttribute(
      "placeholder",
      "Configured — enter to replace",
    );

    await user.click(screen.getByRole("checkbox", { name: "Enable SSO login" }));
    await user.click(screen.getByRole("button", { name: /Save SSO settings/ }));

    await waitFor(() => expect(saveConfig).toHaveBeenCalled());
    expect(saveConfig).toHaveBeenCalledWith(
      expect.objectContaining({
        oidc_enabled: true,
        oidc_issuer_url: config.oidc_issuer_url,
        oidc_client_id: "printstash",
      }),
    );
    expect(saveConfig.mock.calls[0][0]).not.toHaveProperty("oidc_client_secret");
  });
});
