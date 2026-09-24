"""One PrintStash process on the real DBOS engine, driven by ``tests/e2e``.

Each invocation boots the production composition (lifespan, DBOS, the
reconciler) against the vault the environment points at, performs one role,
and prints one JSON line the parent reads:

``stall``
    Set the vault up, upload a mesh, and hold its mesh derivative mid-step
    until the parent kills the process. Prints ``{"file_id": ...}`` once the
    derivative is running.
``converge``
    Boot on the same vault and wait for that Artifact's derivatives to settle,
    the way a restarted process recovers work a dead one left behind. Prints
    the derivative states.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

_DEADLINE_S = float(os.environ.get("JOB_ENGINE_DEADLINE_S", "120"))
_STL = (
    b"solid cube\n"
    + b"".join(
        b"facet normal 0 0 1\nouter loop\nvertex %d 0 0\nvertex 0 %d 0\n"
        b"vertex 0 0 %d\nendloop\nendfacet\n" % (n, n, n)
        for n in range(1, 5)
    )
    + b"endsolid cube\n"
)


def _emit(**payload) -> None:
    print(json.dumps(payload), flush=True)


def stall(marker: Path) -> None:
    from app.core.config import ensure_dirs, settings
    from app.main import app
    from app.modules.derivatives import producers

    def held(file_id: int):
        marker.write_text(str(file_id))
        while True:  # the parent kills this process here
            time.sleep(1)

    # Before the lifespan builds the catalog, which captures the producer.
    producers.derive_mesh = held
    ensure_dirs()
    with TestClient(app) as client:
        client.headers["Origin"] = "http://testserver"
        csrf = client.post("/api/v1/setup/session").json()["csrf"]
        client.headers["X-PrintStash-Setup-CSRF"] = csrf
        setup = client.post(
            "/api/v1/setup",
            json={
                "username": "owner",
                "password": "Password123",
                "storage_backend": "local",
                "data_dir": str(settings.data_dir),
                "thumb_dir": str(settings.thumb_dir),
            },
        )
        assert setup.status_code == 201, setup.text
        client.headers["Authorization"] = f"Bearer {setup.json()['access_token']}"
        uploaded = client.post(
            "/api/v1/ingest/model",
            files={"file": ("crash.stl", _STL, "application/sla")},
        )
        assert uploaded.status_code == 202, uploaded.text
        deadline = time.monotonic() + _DEADLINE_S
        while not marker.exists():
            assert time.monotonic() < deadline, "derivative never started"
            time.sleep(0.1)
        _emit(file_id=int(marker.read_text()))
        while True:
            time.sleep(1)


def converge(file_id: int) -> None:
    from sqlmodel import select

    from app.core.config import ensure_dirs
    from app.db.models import ArtifactDerivative, DerivativeState
    from app.db.session import get_session_factory
    from app.main import app

    ensure_dirs()
    with TestClient(app):
        deadline = time.monotonic() + _DEADLINE_S
        while True:
            with get_session_factory().scoped_session() as session:
                rows = session.exec(
                    select(ArtifactDerivative).where(
                        ArtifactDerivative.file_id == file_id
                    )
                ).all()
                states = {row.kind: DerivativeState(row.state).value for row in rows}
                reasons = {row.kind: row.failure_reason for row in rows}
            if states and all(
                state in {"ready", "failed"} for state in states.values()
            ):
                break
            if time.monotonic() > deadline:
                break
            time.sleep(0.25)
        _emit(states=states, reasons=reasons)


if __name__ == "__main__":
    role = sys.argv[1]
    if role == "stall":
        stall(Path(sys.argv[2]))
    elif role == "converge":
        converge(int(sys.argv[2]))
    else:
        raise SystemExit(f"unknown role {role}")
