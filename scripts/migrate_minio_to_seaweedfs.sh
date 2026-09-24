#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
compose=(
  docker compose
  --project-directory "$repo_root"
  -f "$repo_root/docker-compose.advanced.yml"
  -f "$repo_root/deploy/minio-migration/compose.yml"
  --profile s3
  --profile minio-migration
)

"${compose[@]}" up -d --wait minio seaweedfs
"${compose[@]}" run --rm --no-deps minio-migrate

echo "Migration complete. The MinIO service and printstash_minio volume remain intact."
echo "After pointing PrintStash at SeaweedFS and validating it, stop MinIO with:"
echo "  docker compose -f docker-compose.advanced.yml -f deploy/minio-migration/compose.yml stop minio"
