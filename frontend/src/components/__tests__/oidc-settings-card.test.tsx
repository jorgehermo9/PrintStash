import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

import { OidcSettingsCard } from "@/components/oidc-settings-card";
import { getVaultConfig, updateVaultConfig } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  getVaultConfig: vi.fn(),
  updateVaultConfig: vi.fn(),
}));

vi.mock("@/lib/toast", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const config = {
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

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getVaultConfig).mockResolvedValue(config as never);
  vi.mocked(updateVaultConfig).mockImplementation(async (payload) => ({
    ...config,
    ...payload,
    has_oidc_client_secret: true,
  }) as never);
});

it("loads and saves OIDC settings without replaying stored secret", async () => {
  const user = userEvent.setup();
  render(<OidcSettingsCard />);

  expect(await screen.findByDisplayValue("Authentik")).toBeInTheDocument();
  expect(screen.getByLabelText("Client secret")).toHaveAttribute(
    "placeholder",
    "Configured — enter to replace",
  );

  await user.click(screen.getByRole("checkbox", { name: "Enable SSO login" }));
  await user.click(screen.getByRole("button", { name: /Save SSO settings/ }));

  await waitFor(() => expect(updateVaultConfig).toHaveBeenCalled());
  expect(updateVaultConfig).toHaveBeenCalledWith(
    expect.objectContaining({
      oidc_enabled: true,
      oidc_issuer_url: config.oidc_issuer_url,
      oidc_client_id: "printstash",
    }),
  );
  expect(vi.mocked(updateVaultConfig).mock.calls[0][0]).not.toHaveProperty(
    "oidc_client_secret",
  );
});
