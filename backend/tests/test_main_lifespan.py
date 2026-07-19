"""Boots the real app lifespan (startup + shutdown), not just handler-level tests.

Every other test in the suite gets its FastAPI ``app`` fixture pre-wired
(``app.state.printer_hub`` set manually, no ``with TestClient(app) as client``),
so ``app/main.py``'s ``lifespan()`` — DB init, storage init, background task
wiring, graceful shutdown — had no direct coverage (58% per the 0.11 audit).
This starts it for real via Starlette's TestClient context-manager protocol.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import _overlay
from app.db.models import User
from app.services import storage_backend
from app.services.auth import create_access_token, hash_password


@pytest.fixture
def _local_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _overlay.update(
        {
            "storage_backend": "local",
            "data_dir": tmp_path / "files",
            "thumb_dir": tmp_path / "thumbs",
            "backup_dir": tmp_path / "backups",
            "staging_dir": tmp_path / "staging",
        }
    )
    monkeypatch.setattr(storage_backend, "_backend", None)
    yield
    for field in ("storage_backend", "data_dir", "thumb_dir", "backup_dir", "staging_dir"):
        _overlay.pop(field, None)


def test_lifespan_starts_background_tasks_and_shuts_down_cleanly(
    _local_storage: None, db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.main import app

    user = User(
        username="lifespan-admin",
        hashed_password=hash_password("Password123"),
        is_active=True,
        is_superuser=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token = create_access_token(user.id, user.username, scope="admin")
    headers = {"Authorization": f"Bearer {token}"}

    with TestClient(app) as client:
        # Background tasks wired onto app.state by the real lifespan, not the
        # per-test fixture shortcut.
        for attr in (
            "printer_hub",
            "library_watcher",
            "gc_task",
            "external_scan_task",
            "notification_task",
            "fleet_scheduler_task",
        ):
            assert hasattr(app.state, attr), f"app.state.{attr} not set by lifespan"
        assert not app.state.fleet_scheduler_task.done()
        assert not app.state.gc_task.done()

        response = client.get("/api/v1/health/details", headers=headers)
        assert response.status_code == 200
        body = response.json()
        # Not asserting overall body["status"] == "ok": components like backup
        # (none configured) legitimately report degraded on a fresh install —
        # this test is about the scheduler/storage wiring lifespan sets up,
        # not full green health.
        assert body["components"]["fleet_scheduler"]["ok"] is True
        assert body["components"]["fleet_scheduler"]["running"] is True
        assert body["components"]["storage"]["ok"] is True

        liveness = client.get("/api/v1/health")
        assert liveness.status_code == 200

    # Shutdown (TestClient.__exit__) must cancel every background task.
    assert app.state.fleet_scheduler_task.cancelled()
    assert app.state.gc_task.cancelled()
    assert app.state.external_scan_task.cancelled()
    assert app.state.notification_task.cancelled()
