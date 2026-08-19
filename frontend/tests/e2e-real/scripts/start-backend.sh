#!/usr/bin/env bash
# Launch the REAL FastAPI backend against a throwaway SQLite DB + temp data dirs,
# so the Playwright "real" suite drives the actual API, services, and persistence
# instead of a mock. State is wiped on every launch — each run starts empty and
# the auth helper seeds the first admin through /setup.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/../../../../backend" && pwd)"
DATA_ROOT="${PLAYWRIGHT_REAL_DATA_DIR:-$SCRIPT_DIR/../.data}"
PORT="${PLAYWRIGHT_REAL_API_PORT:-8410}"

rm -rf "$DATA_ROOT"
mkdir -p "$DATA_ROOT/files" "$DATA_ROOT/thumbs" "$DATA_ROOT/staging" "$DATA_ROOT/backups"

export VAULT_DB_URL="sqlite:///$DATA_ROOT/test.sqlite"
export VAULT_DATA_DIR="$DATA_ROOT/files"
export VAULT_THUMB_DIR="$DATA_ROOT/thumbs"
export VAULT_STAGING_DIR="$DATA_ROOT/staging"
export VAULT_BACKUP_DIR="$DATA_ROOT/backups"
export VAULT_JWT_SECRET="e2e-real-secret-at-least-32-bytes"
export VAULT_SECRETS_KEY="e2e-real-secrets-key"

cd "$BACKEND_DIR"
if [ -x .venv/bin/python ]; then
  PY=(.venv/bin/python)
  ALEMBIC=(.venv/bin/alembic)
else
  PY=(uv run python)
  ALEMBIC=(uv run alembic)
fi

"${ALEMBIC[@]}" upgrade head
exec "${PY[@]}" -m uvicorn app.main:app --port "$PORT" --host 127.0.0.1
