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
``upload``
    Set the vault up, upload a mesh and wait for its derivatives to settle.
    Prints the Artifact's id, derivative states and thumbnail.
``rederive``
    Boot as a build whose mesh thumbnail recipe is one version newer, record
    the thumbnail the Artifact showed while the new one was being derived,
    and wait for the new recipe to settle.
``split_api``
    Serve as an API that runs no jobs (``VAULT_API_RUNS_JOBS=false``): set the
    vault up, open the events socket, upload a mesh and print
    ``{"job_id": ...}``; then wait for the workers the parent starts to finish
    it, and print the notices the socket received and the derivative states.
"""

from __future__ import annotations

import json
import os
import sys
import threading
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


def _set_up(client: TestClient) -> None:
    """Complete first-run setup and authenticate ``client`` as the owner."""
    from app.core.config import settings

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


def stall(marker: Path) -> None:
    from app.core.config import ensure_dirs
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
        _set_up(client)
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


def _settle(file_id: int) -> dict:
    """Wait for the Artifact's current-recipe derivatives to settle."""
    from sqlmodel import select

    from app.db.models import ArtifactDerivative, DerivativeState, File
    from app.db.session import get_session_factory
    from app.modules.derivatives import kinds

    deadline = time.monotonic() + _DEADLINE_S
    while True:
        with get_session_factory().scoped_session() as session:
            file = session.get(File, file_id)
            assert file is not None
            current = kinds.recipes_for(file)
            rows = [
                row
                for row in session.exec(
                    select(ArtifactDerivative).where(
                        ArtifactDerivative.file_id == file_id
                    )
                ).all()
                if current.get(row.kind) == row.recipe_version
            ]
            states = {row.kind: DerivativeState(row.state).value for row in rows}
            outcome = {
                "states": states,
                "reasons": {row.kind: row.failure_reason for row in rows},
                "recipes": {row.kind: row.recipe_version for row in rows},
                "thumbnail": file.thumbnail_path,
            }
        settled = set(states) == set(current) and all(
            state in {"ready", "failed"} for state in states.values()
        )
        if settled or time.monotonic() > deadline:
            return outcome
        time.sleep(0.25)


def converge(file_id: int) -> None:
    from app.core.config import ensure_dirs
    from app.main import app

    ensure_dirs()
    with TestClient(app):
        _emit(**_settle(file_id))


def upload() -> None:
    from app.core.config import ensure_dirs
    from app.main import app

    ensure_dirs()
    with TestClient(app) as client:
        _set_up(client)
        uploaded = client.post(
            "/api/v1/ingest/model",
            files={"file": ("recipe.stl", _STL, "application/sla")},
        )
        assert uploaded.status_code == 202, uploaded.text
        job_id = uploaded.json()["job_id"]
        deadline = time.monotonic() + _DEADLINE_S
        while (
            file_id := client.get(f"/api/v1/jobs/{job_id}").json().get("file_id")
        ) is None:
            assert time.monotonic() < deadline, "the upload never committed"
            time.sleep(0.25)
        _emit(file_id=file_id, **_settle(file_id))


def rederive(file_id: int) -> None:
    """Boot as the next build, whose thumbnail recipe changed.

    Records what the Artifact showed while the new thumbnail was being
    derived, then waits for the new recipe to settle.
    """
    from app.core.config import ensure_dirs
    from app.db.models import File
    from app.db.session import get_session_factory
    from app.main import app
    from app.modules.derivatives import kinds, producers

    mesh = kinds.group(kinds.MESH_DEFINITION)
    mesh.kinds[kinds.THUMBNAIL] = mesh.kinds[kinds.THUMBNAIL] + 1
    shown_while_deriving: list[str | None] = []
    original = producers.derive_mesh

    def observed(derived_id: int):
        with get_session_factory().scoped_session() as session:
            file = session.get(File, derived_id)
            shown_while_deriving.append(file.thumbnail_path if file else None)
        return original(derived_id)

    # Before the lifespan builds the catalog, which captures the producer.
    producers.derive_mesh = observed
    ensure_dirs()
    with TestClient(app):
        outcome = _settle(file_id)
    _emit(shown_while_deriving=shown_while_deriving, **outcome)


def split_api() -> None:
    from sqlmodel import select

    from app.core.config import ensure_dirs
    from app.db.models import ArtifactDerivative, DerivativeState
    from app.db.session import get_session_factory
    from app.main import app

    def give_up() -> None:
        # A notice that never arrives would block the socket read forever.
        print("split_api: no outcome before the deadline", file=sys.stderr)
        sys.stderr.flush()
        os._exit(3)

    watchdog = threading.Timer(_DEADLINE_S, give_up)
    watchdog.daemon = True
    watchdog.start()
    ensure_dirs()
    with TestClient(app) as client:
        _set_up(client)
        ticket = client.post("/api/v1/events/ticket").json()["ticket"]
        with client.websocket_connect(f"/api/v1/events/ws?ticket={ticket}") as ws:
            assert ws.receive_json() == {"type": "resync"}
            uploaded = client.post(
                "/api/v1/ingest/model",
                files={"file": ("split.stl", _STL, "application/sla")},
            )
            assert uploaded.status_code == 202, uploaded.text
            job_id = uploaded.json()["job_id"]
            _emit(job_id=job_id)
            # Only a worker can move this Job; each change reaches this socket
            # from another process, over NOTIFY.
            notices: list[dict] = []
            while True:
                notice = ws.receive_json()
                notices.append(notice)
                if notice.get("job_id") == job_id and notice.get("state") in {
                    "completed",
                    "failed",
                }:
                    break
        file_id = client.get(f"/api/v1/jobs/{job_id}").json()["file_id"]
        deadline = time.monotonic() + _DEADLINE_S
        while True:
            with get_session_factory().scoped_session() as session:
                states = {
                    row.kind: DerivativeState(row.state).value
                    for row in session.exec(
                        select(ArtifactDerivative).where(
                            ArtifactDerivative.file_id == file_id
                        )
                    ).all()
                }
            settled = states and all(
                state in {"ready", "failed"} for state in states.values()
            )
            if settled or time.monotonic() > deadline:
                break
            time.sleep(0.25)
        _emit(
            notices=[n for n in notices if n.get("job_id") == job_id],
            states=states,
        )


if __name__ == "__main__":
    role = sys.argv[1]
    if role == "stall":
        stall(Path(sys.argv[2]))
    elif role == "converge":
        converge(int(sys.argv[2]))
    elif role == "upload":
        upload()
    elif role == "rederive":
        rederive(int(sys.argv[2]))
    elif role == "split_api":
        split_api()
    else:
        raise SystemExit(f"unknown role {role}")
