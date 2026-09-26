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

# Every app path, the SQLite database included, derives from this one root;
# drop any per-directory override a developer shell exports.
unset VAULT_DB_URL VAULT_DATA_DIR VAULT_THUMB_DIR VAULT_STAGING_DIR VAULT_BACKUP_DIR
export VAULT_DATA_ROOT="$DATA_ROOT"
export VAULT_JWT_SECRET="e2e-real-secret-at-least-32-bytes"
export VAULT_SECRETS_KEY="e2e-real-secrets-key"
export VAULT_RESTART_ENABLED="true"

cd "$BACKEND_DIR"
if [ -x .venv/bin/python ]; then
  PY=(.venv/bin/python)
else
  PY=(uv run python)
fi

if [ -z "${VAULT_BGCODE_EXECUTABLE:-}" ]; then
  VAULT_BGCODE_EXECUTABLE="$("${PY[@]}" -m tests.bgcode_support "$DATA_ROOT/converter")"
  export VAULT_BGCODE_EXECUTABLE
fi

# The container's own first boot step: prepare the data root, then migrate.
"${PY[@]}" -m app.db.migrate

# Mirror the official container's restart policy so the real-browser suite can
# exercise the Settings restart flow without weakening production behaviour.
# A graceful uvicorn shutdown exits zero and is relaunched; crashes stay red.
child_pid=""
stop_backend() {
  if [ -n "$child_pid" ]; then
    kill -TERM "$child_pid" 2>/dev/null || true
    wait "$child_pid" 2>/dev/null || true
  fi
  exit 0
}
trap stop_backend TERM INT

while true; do
  "${PY[@]}" -m uvicorn app.main:app --port "$PORT" --host 127.0.0.1 &
  child_pid=$!
  if wait "$child_pid"; then
    exit_code=0
  else
    exit_code=$?
  fi
  child_pid=""
  if [ "$exit_code" -eq 0 ] || [ "$exit_code" -eq 143 ]; then
    # Uvicorn may report a handled SIGTERM as either a clean exit or 128+TERM.
    # Both are restart requests here; every other status is a real crash.
    continue
  else
    exit "$exit_code"
  fi
done
