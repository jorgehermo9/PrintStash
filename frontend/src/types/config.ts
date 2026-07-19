export interface SetupStatus {
  configured: boolean;
  setup_token_required?: boolean;
  user_count: number;
  default_data_dir?: string;
  default_thumb_dir?: string;
  current_data_dir?: string;
  current_thumb_dir?: string;
  current_storage_backend?: string;
  current_s3_bucket?: string;
  current_s3_endpoint_url?: string;
  current_s3_region?: string;
  current_backup_retention_days?: number;
  current_backup_s3_bucket?: string;
  current_backup_s3_endpoint_url?: string;
  current_backup_s3_region?: string;
  configured_at?: string | null;
}

export interface SetupRequest {
  setup_token: string;
  username: string;
  password: string;
  email?: string;
  storage_backend?: string;
  data_dir?: string;
  thumb_dir?: string;
  s3_bucket?: string;
  s3_endpoint_url?: string;
  s3_region?: string;
  s3_access_key?: string;
  s3_secret_key?: string;
  backup_retention_days?: number;
  backup_s3_bucket?: string;
  backup_s3_endpoint_url?: string;
  backup_s3_region?: string;
  backup_s3_access_key?: string;
  backup_s3_secret_key?: string;
}

export interface SetupResponse {
  configured: boolean;
  user_id: number;
  username: string;
  storage_backend: string;
  data_dir: string;
  thumb_dir: string;
  access_token: string;
  token_type: string;
}

export interface VaultConfigRead {
  storage_backend: string;
  data_dir: string;
  thumb_dir: string;
  s3_bucket: string;
  s3_endpoint_url: string;
  s3_region: string;
  s3_access_key: string;
  s3_secret_key: string;
  has_s3_access_key: boolean;
  has_s3_secret_key: boolean;
  backup_retention_days: number;
  trash_retention_days: number;
  backup_s3_bucket: string;
  backup_s3_endpoint_url: string;
  backup_s3_region: string;
  backup_s3_access_key: string;
  backup_s3_secret_key: string;
  has_backup_s3_access_key: boolean;
  has_backup_s3_secret_key: boolean;
  has_backup_s3: boolean;
  auto_mark_known_good: boolean;
  external_libraries_enabled: boolean;
  currency: string;
  oidc_enabled: boolean;
  oidc_issuer_url: string;
  oidc_client_id: string;
  has_oidc_client_secret: boolean;
  oidc_scopes: string;
  oidc_username_claim: string;
  oidc_groups_claim: string;
  oidc_admin_groups: string;
  oidc_display_name: string;
  oidc_redirect_uri: string;
  oidc_allow_insecure_http: boolean;
}

export interface VaultConfigUpdate {
  storage_backend?: string;
  data_dir?: string;
  thumb_dir?: string;
  s3_bucket?: string;
  s3_endpoint_url?: string;
  s3_region?: string;
  s3_access_key?: string;
  s3_secret_key?: string;
  backup_retention_days?: number;
  trash_retention_days?: number;
  backup_s3_bucket?: string;
  backup_s3_endpoint_url?: string;
  backup_s3_region?: string;
  backup_s3_access_key?: string;
  backup_s3_secret_key?: string;
  auto_mark_known_good?: boolean;
  external_libraries_enabled?: boolean;
  currency?: string;
  oidc_enabled?: boolean;
  oidc_issuer_url?: string;
  oidc_client_id?: string;
  oidc_client_secret?: string;
  oidc_scopes?: string;
  oidc_username_claim?: string;
  oidc_groups_claim?: string;
  oidc_admin_groups?: string;
  oidc_display_name?: string;
  oidc_redirect_uri?: string;
  oidc_allow_insecure_http?: boolean;
}

export interface MakerWorldStatus {
  connected: boolean;
  updated_at: string | null;
}

export interface MakerWorldLoginRequest {
  account: string;
  password: string;
}

export interface MakerWorldVerifyRequest {
  login_token: string;
  code: string;
}

export interface MakerWorldTokenRequest {
  token: string;
}

// status: "ok" (connected), "need_email_code", or "need_tfa_code".
export interface MakerWorldLoginResponse {
  status: string;
  login_token: string | null;
  connected: boolean;
}

export type ExternalLibraryCollectionMode = "mirror" | "single";

// auto: watch only on local filesystems; events: always watch; off: never watch.
export type ExternalLibraryWatchMode = "auto" | "events" | "off";

// Detected filesystem class backing the root path.
export type ExternalLibraryFsKind = "local" | "network" | "unknown";

export interface ExternalLibraryScanSummary {
  added: number;
  updated: number;
  removed: number;
  skipped: number;
  errors: string[];
  error: string | null;
  aborted: boolean;
}

export interface ExternalLibrary {
  id: number;
  name: string;
  root_path: string;
  enabled: boolean;
  scan_interval_minutes: number;
  scan_schedule: string;
  watch_mode: ExternalLibraryWatchMode;
  fs_kind: ExternalLibraryFsKind | null;
  watch_active: boolean;
  collection_mode: ExternalLibraryCollectionMode;
  target_collection_id: number | null;
  last_scanned_at: string | null;
  // "partial" = scan completed but one or more files failed to index (terminal,
  // like "ok"); the backend uses it so a green status never hides a per-file error.
  last_scan_status: "ok" | "error" | "running" | "partial" | null;
  last_scan_summary: ExternalLibraryScanSummary | null;
}

export interface ExternalLibraryCreate {
  name: string;
  root_path: string;
  enabled?: boolean;
  scan_schedule?: string;
  watch_mode?: ExternalLibraryWatchMode;
  collection_mode?: ExternalLibraryCollectionMode;
  target_collection_id?: number | null;
}

export interface ExternalLibraryUpdate {
  name?: string;
  root_path?: string;
  enabled?: boolean;
  scan_schedule?: string;
  watch_mode?: ExternalLibraryWatchMode;
  collection_mode?: ExternalLibraryCollectionMode;
  target_collection_id?: number | null;
}
