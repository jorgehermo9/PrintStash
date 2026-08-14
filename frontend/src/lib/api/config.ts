import { getJson, sendAction, sendJson } from "@/lib/api/request";
import {
  MakerWorldLoginRequest,
  MakerWorldLoginResponse,
  MakerWorldStatus,
  MakerWorldTokenRequest,
  MakerWorldVerifyRequest,
  SetupRequest,
  SetupResponse,
  SetupStatus,
  VaultConfigRead,
  VaultConfigUpdate,
  IngestResponse,
} from "@/types";

export function getSetupStatus(): Promise<SetupStatus> {
  return getJson<SetupStatus>("/api/v1/setup/status");
}

export function completeSetup(body: SetupRequest): Promise<SetupResponse> {
  return sendJson<SetupResponse>("/api/v1/setup", "POST", body);
}

export function getVaultConfig(): Promise<VaultConfigRead> {
  return getJson<VaultConfigRead>("/api/v1/config");
}

export function getHealthDetails<T>(): Promise<T> {
  return getJson<T>("/api/v1/health/details", { fresh: true });
}

export interface ReleaseStatus {
  status: "update_available" | "up_to_date" | "unavailable";
  current_version: string;
  latest_version: string | null;
  update_available: boolean;
  release_url: string | null;
  published_at: string | null;
  checked_at: string;
}

export function getLatestRelease(refresh = false): Promise<ReleaseStatus> {
  const query = refresh ? "?refresh=true" : "";
  return getJson<ReleaseStatus>(`/api/v1/health/releases/latest${query}`, { fresh: true });
}

export function updateVaultConfig(body: VaultConfigUpdate): Promise<VaultConfigRead> {
  return sendJson<VaultConfigRead>("/api/v1/config", "PUT", body);
}

export function rebuildModelThumbnails(): Promise<IngestResponse> {
  return sendJson<IngestResponse>(
    "/api/v1/files/thumbnails/rebuild?force=true",
    "POST",
    {},
  );
}

export function getMakerWorldStatus(): Promise<MakerWorldStatus> {
  return getJson<MakerWorldStatus>("/api/v1/config/makerworld");
}

export function makerWorldLogin(
  body: MakerWorldLoginRequest,
): Promise<MakerWorldLoginResponse> {
  return sendJson<MakerWorldLoginResponse>(
    "/api/v1/config/makerworld/login",
    "POST",
    body,
  );
}

export function makerWorldVerify(
  body: MakerWorldVerifyRequest,
): Promise<MakerWorldLoginResponse> {
  return sendJson<MakerWorldLoginResponse>(
    "/api/v1/config/makerworld/verify",
    "POST",
    body,
  );
}

export function makerWorldSetToken(
  body: MakerWorldTokenRequest,
): Promise<MakerWorldStatus> {
  return sendJson<MakerWorldStatus>(
    "/api/v1/config/makerworld/token",
    "POST",
    body,
  );
}

export function makerWorldDisconnect(): Promise<void> {
  return sendAction("/api/v1/config/makerworld", "DELETE");
}
