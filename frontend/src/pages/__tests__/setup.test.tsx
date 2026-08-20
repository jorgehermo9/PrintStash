import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SetupPage from "@/pages/setup";

const mocks = vi.hoisted(() => ({
  completeSetup: vi.fn(),
  getSetupStatus: vi.fn(),
  router: { replace: vi.fn() },
  storeLogin: vi.fn(),
}));

vi.mock("@/lib/navigation", () => ({
  useRouter: () => mocks.router,
}));
vi.mock("@/lib/api", () => ({
  completeSetup: mocks.completeSetup,
  getSetupStatus: mocks.getSetupStatus,
}));
vi.mock("@/lib/auth", () => ({ storeLogin: mocks.storeLogin }));
vi.mock("@/components/theme-toggle", () => ({ ThemeToggle: () => null }));
vi.mock("@/components/brand-mark", () => ({ BrandMark: () => <span /> }));

const status = {
  configured: false,
  setup_token_required: true,
  user_count: 0,
  default_data_dir: "/data/files",
  default_thumb_dir: "/data/thumbs",
  current_data_dir: "/data/files",
  current_thumb_dir: "/data/thumbs",
  current_storage_backend: "local",
  current_s3_bucket: "",
  current_s3_endpoint_url: "",
  current_s3_region: "auto",
  current_backup_retention_days: 30,
  current_backup_s3_bucket: "",
  current_backup_s3_endpoint_url: "",
  current_backup_s3_region: "auto",
  configured_at: null,
};

async function reachStorage() {
  const user = userEvent.setup();
  render(<SetupPage />);
  await screen.findByRole("heading", { name: "Welcome to PrintStash" });
  await user.type(screen.getByLabelText("Setup token"), "operator-setup-token-123");
  await user.type(screen.getByLabelText("Username"), "admin");
  await user.type(screen.getByLabelText("Password"), "Password123");
  await user.type(screen.getByLabelText("Confirm password"), "Password123");
  await user.click(screen.getByRole("button", { name: "Next" }));
  return user;
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.getSetupStatus.mockResolvedValue(status);
  mocks.completeSetup.mockResolvedValue({
    configured: true,
    user_id: 1,
    username: "admin",
    storage_backend: "local",
    data_dir: "/data/files",
    thumb_dir: "/data/thumbs",
    access_token: "token",
    token_type: "bearer",
  });
});

describe("first-run setup", () => {
  it("validates account fields inline before advancing", async () => {
    const user = userEvent.setup();
    render(<SetupPage />);
    await screen.findByRole("heading", { name: "Welcome to PrintStash" });
    await user.type(screen.getByLabelText("Setup token"), "operator-setup-token-123");
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Username must be at least 3 characters");
    expect(mocks.completeSetup).not.toHaveBeenCalled();
  });

  it("authenticates and enters empty library after successful setup", async () => {
    const user = await reachStorage();
    await user.click(screen.getByRole("button", { name: "Complete setup" }));

    await waitFor(() =>
      expect(mocks.storeLogin).toHaveBeenCalledWith(
        "token",
        expect.objectContaining({ username: "admin" }),
      ),
    );
    expect(mocks.completeSetup).toHaveBeenCalledWith(
      expect.objectContaining({ setup_token: "operator-setup-token-123" }),
    );
    expect(mocks.router.replace).toHaveBeenCalledWith("/");
  });

  it("preserves values after recoverable failure and allows safe retry", async () => {
    mocks.completeSetup
      .mockRejectedValueOnce(new Error('HTTP 400: {"detail":"data_dir_not_writable"}'))
      .mockResolvedValueOnce({
        configured: true,
        user_id: 1,
        username: "admin",
        storage_backend: "local",
        data_dir: "/data/files",
        thumb_dir: "/data/thumbs",
        access_token: "token",
      });
    const user = await reachStorage();
    await user.clear(screen.getByLabelText("Data directory"));
    await user.type(screen.getByLabelText("Data directory"), "/recoverable/path");
    await user.click(screen.getByRole("button", { name: "Complete setup" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "cannot write to the data directory",
    );
    expect(screen.getByLabelText("Data directory")).toHaveValue("/recoverable/path");
    await user.click(screen.getByRole("button", { name: "Complete setup" }));
    await waitFor(() => expect(mocks.completeSetup).toHaveBeenCalledTimes(2));
  });

  it("explains that an existing library cannot be used as private vault storage", async () => {
    mocks.completeSetup.mockRejectedValueOnce(
      new Error('HTTP 400: {"detail":"data_dir_not_empty"}'),
    );
    const user = await reachStorage();

    await user.click(screen.getByRole("button", { name: "Complete setup" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("dedicated empty directory");
    expect(screen.getByRole("alert")).toHaveTextContent("External Libraries");
  });

  it("blocks duplicate completion submissions while request is active", async () => {
    let resolve!: (value: unknown) => void;
    mocks.completeSetup.mockReturnValue(
      new Promise((done) => {
        resolve = done;
      }),
    );
    const user = await reachStorage();
    const submit = screen.getByRole("button", { name: "Complete setup" });
    await user.dblClick(submit);
    expect(mocks.completeSetup).toHaveBeenCalledTimes(1);
    resolve({
      configured: true,
      user_id: 1,
      username: "admin",
      storage_backend: "local",
      data_dir: "/data/files",
      thumb_dir: "/data/thumbs",
      access_token: "token",
    });
  });

  it("keeps optional off-site backup settings collapsed until requested", async () => {
    const user = await reachStorage();

    expect(screen.getByLabelText("Backup bucket")).not.toBeVisible();
    await user.click(screen.getByText("Off-site backup"));
    expect(screen.getByLabelText("Backup bucket")).toBeVisible();
  });
});
