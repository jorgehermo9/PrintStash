#!/bin/sh
set -eu

source_bucket="${MINIO_MIGRATION_SOURCE_BUCKET:?source bucket is required}"
target_bucket="${SEAWEEDFS_MIGRATION_TARGET_BUCKET:?target bucket is required}"

echo "Checking MinIO source bucket..."
rclone lsf "minio:${source_bucket}" --max-depth 1 >/dev/null

echo "Ensuring SeaweedFS target bucket exists..."
rclone mkdir "seaweedfs:${target_bucket}"

echo "Copying source objects without deleting either side..."
rclone copy "minio:${source_bucket}" "seaweedfs:${target_bucket}" \
  --metadata \
  --fast-list \
  --checkers 8 \
  --transfers 4 \
  --retries 5 \
  --low-level-retries 10 \
  --stats 10s

echo "Downloading both sides to verify object content..."
rclone check "minio:${source_bucket}" "seaweedfs:${target_bucket}" \
  --download \
  --one-way \
  --checkers 8 \
  --retries 5 \
  --low-level-retries 10

echo "MinIO to SeaweedFS migration verified. Source objects were not modified."
