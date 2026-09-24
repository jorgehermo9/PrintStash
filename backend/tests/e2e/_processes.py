"""Vaults for E2E tests that run PrintStash in child processes on real DBOS.

In-process E2E flows use the inline engine. These build what a deployment
has instead: a database of its own (a SQLite file, or a fresh database on the
run's PostgreSQL container) and the environment every child process of that
vault is started with.
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, make_url

from app.db.url import normalize_database_url
from tests.containers import postgres_url
from tests.paths import BACKEND_DIR


def fresh_postgres_database(prefix: str) -> str:
    """Create an empty database on the run's server and return its URL."""
    server = make_url(normalize_database_url(postgres_url()))
    name = f"{prefix}_{uuid4().hex[:12]}"
    admin = create_engine(server, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
    admin.dispose()
    return server.set(database=name).render_as_string(hide_password=False)


def vault_environment(tmp_path: Path, db_url: str) -> dict[str, str]:
    """The environment every process of one vault shares.

    A dead executor is recognised after one stale window and the tick finds it
    on the next pass: both are kept short so recovery is quick.
    """
    environment = {
        **os.environ,
        "PYTHONPATH": str(BACKEND_DIR),
        "VAULT_DB_URL": db_url,
        "VAULT_SETUP_MODE": "trusted_network",
        "VAULT_SETUP_ALLOWED_HOSTS": "testserver",
        "VAULT_SECRETS_KEY": "job-engine-e2e-key",
        "VAULT_SECRETS_KEY_FILE": str(tmp_path / "secrets-key"),
        "VAULT_JOBS_EXECUTOR_STALE_SECONDS": "10",
        "VAULT_JOBS_RECONCILE_INTERVAL_SECONDS": "10",
        "VAULT_FENCE_HEARTBEAT_SECONDS": "2",
    }
    for key in ("DATA_DIR", "THUMB_DIR", "STAGING_DIR", "BACKUP_DIR"):
        directory = tmp_path / key.lower()
        directory.mkdir(exist_ok=True)
        environment[f"VAULT_{key}"] = str(directory)
    environment["VAULT_ARTIFACT_CACHE_ROOT"] = str(tmp_path / "cache")
    return environment
