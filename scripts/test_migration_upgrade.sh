#!/usr/bin/env bash
# Proves `alembic upgrade head` from an old release's schema succeeds against
# the current migration chain, with no dangling foreign keys and no data loss
# afterward. Self-hosters upgrade in place — a broken migration here means a
# broken production install (see CLAUDE.md's rule against editing merged
# migrations).
#
# Usage: scripts/test_migration_upgrade.sh [from-tag]
#   from-tag defaults to the oldest release this branch still supports
#   upgrading from. Bump it forward as very old tags are dropped from support.
#
# Requires: git, uv. Run from the repo root.
set -euo pipefail

FROM_TAG="${1:-v0.7.0}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"; git -C "$REPO_ROOT" worktree remove --force "$WORKDIR/old" 2>/dev/null || true' EXIT

DB_FILE="$WORKDIR/vault.sqlite"
OLD_WORKTREE="$WORKDIR/old"

echo "==> Seeding a $FROM_TAG-era database at $DB_FILE"
git -C "$REPO_ROOT" worktree add --detach "$OLD_WORKTREE" "$FROM_TAG" >/dev/null

(
  cd "$OLD_WORKTREE/backend"
  uv sync --extra dev --frozen --quiet
  VAULT_DB_URL="sqlite:///$DB_FILE" uv run alembic upgrade head
  VAULT_DB_URL="sqlite:///$DB_FILE" uv run python - <<'PY'
from sqlmodel import Session, create_engine

from app.core.config import settings
from app.db.models import User
from app.modules.identity.auth import hash_password

engine = create_engine(settings.db_url)
with Session(engine) as session:
    session.add(
        User(
            username="migration-check",
            hashed_password=hash_password("Password123"),
            is_active=True,
            is_superuser=True,
        )
    )
    session.commit()
PY
)
git -C "$REPO_ROOT" worktree remove --force "$OLD_WORKTREE"

echo "==> Upgrading $DB_FILE to current head"
cd "$REPO_ROOT/backend"
uv sync --extra dev --frozen --quiet
VAULT_DB_URL="sqlite:///$DB_FILE" uv run alembic upgrade head

echo "==> Checking foreign key integrity"
VAULT_DB_URL="sqlite:///$DB_FILE" uv run python - <<'PY'
import sqlite3
import sys

from app.core.config import settings

path = settings.db_url.removeprefix("sqlite:///")
conn = sqlite3.connect(path)
conn.execute("PRAGMA foreign_keys=ON")
violations = conn.execute("PRAGMA foreign_key_check").fetchall()
if violations:
    print("foreign_key_check found violations:")
    for row in violations:
        print(row)
    sys.exit(1)
PY

echo "==> Verifying pre-upgrade data survived"
VAULT_DB_URL="sqlite:///$DB_FILE" uv run python - <<'PY'
from sqlmodel import Session, create_engine, select

from app.core.config import settings
from app.db.models import User

engine = create_engine(settings.db_url)
with Session(engine) as session:
    user = session.exec(
        select(User).where(User.username == "migration-check")
    ).first()
    assert user is not None, "seeded user vanished across the upgrade"
PY

echo "==> Boot smoke: app starts against the upgraded DB"
# The seeded DB has no SystemConfig storage overlay (that's only written by the
# setup wizard), so init_backend() falls back to the frozen Settings default
# under /data unless VAULT_DATA_ROOT points it elsewhere here — /data isn't
# writable on a bare CI runner (no Docker volume, no root).
VAULT_DB_URL="sqlite:///$DB_FILE" \
VAULT_JWT_SECRET="$(openssl rand -hex 32)" \
VAULT_DATA_ROOT="$WORKDIR/data" \
  uv run python -c "
from fastapi.testclient import TestClient
from app.main import app

with TestClient(app) as client:
    r = client.get('/api/v1/health')
    assert r.status_code == 200, r.text
"

echo "Migration upgrade check passed ($FROM_TAG -> HEAD)."
