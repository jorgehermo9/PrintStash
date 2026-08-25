"""E2E: G-code ingestion, end to end through the real pipeline.

Uploads a real OrcaSlicer fixture through the public ingest endpoint, waits for
the background job to finish, and asserts the model was persisted with parsed
slicer metadata. Re-uploading the same bytes must dedup by content hash rather
than create a second model.
"""

from __future__ import annotations

import asyncio
import io
from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from sqlmodel import select

from app.core.config import _overlay
from app.core.time import utcnow
from app.db.models import BackgroundJob, InboxItem, InboxItemState, User
from app.services.jobs import registry
from app.services.setup_token import current_setup_token

pytestmark = pytest.mark.e2e

FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "real_orca_ender3_benchy.gcode"
)


async def _setup_and_login(api, tmp_path) -> dict[str, str]:
    r = await api.post(
        "/api/v1/setup",
        json={
            "setup_token": current_setup_token(),
            "username": "owner",
            "password": "Password123",
            "storage_backend": "local",
            "data_dir": str(tmp_path / "files"),
            "thumb_dir": str(tmp_path / "thumbs"),
        },
    )
    assert r.status_code == 201, r.text
    # Storage backend is normally initialised in the app lifespan (not run here).
    from app.services.storage_backend import init_backend

    init_backend()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _upload(api, headers, *, model_name: str) -> dict:
    r = await api.post(
        "/api/v1/ingest/orca",
        files={"file": (FIXTURE.name, FIXTURE.read_bytes(), "text/plain")},
        data={"model_name": model_name},
        headers=headers,
    )
    assert r.status_code == 202, r.text
    return r.json()


async def _await_job(api, headers, job_id: str) -> dict:
    for _ in range(50):
        r = await api.get(f"/api/v1/ingest/jobs/{job_id}", headers=headers)
        assert r.status_code == 200, r.text
        job = r.json()
        if job["state"] in ("completed", "failed", "duplicate"):
            return job
        await asyncio.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish: {job}")


@pytest.mark.asyncio
async def test_gcode_upload_parses_metadata_and_dedups(api, tmp_path, e2e_db):
    headers = await _setup_and_login(api, tmp_path)

    job = await _await_job(
        api, headers, (await _upload(api, headers, model_name="Benchy"))["job_id"]
    )
    assert job["state"] == "completed", job

    # The model now exists and is listable.
    listing = await api.get("/api/v1/models", headers=headers)
    assert listing.status_code == 200, listing.text
    models = listing.json()
    assert any(m["name"] == "Benchy" for m in models), models

    # Parsed slicer metadata is attached to the persisted file.
    from sqlmodel import select

    from app.db.models import Metadata

    meta = e2e_db.exec(select(Metadata)).first()
    assert meta is not None, "expected extracted metadata row"
    # The OrcaSlicer benchy fixture carries a real layer height + slicer name.
    assert (meta.slicer_name or "").lower().startswith("orca") or meta.layer_height_mm

    # Re-uploading identical bytes dedups by content hash (no second model).
    dup = await _await_job(
        api, headers, (await _upload(api, headers, model_name="Benchy Copy"))["job_id"]
    )
    assert dup["state"] in ("duplicate", "completed"), dup
    listing2 = (await api.get("/api/v1/models", headers=headers)).json()
    benchies = [m for m in listing2 if m["name"] in ("Benchy", "Benchy Copy")]
    assert len(benchies) == 1, f"dedup failed, got {benchies}"


@pytest.mark.asyncio
async def test_over_cap_mesh_upload_has_a_visible_thumbnail(
    api, tmp_path, e2e_db, monkeypatch
):
    """The headline #67 flow persists a useful fallback through the real API."""
    import trimesh

    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1_000)
    mesh = trimesh.creation.icosphere(subdivisions=7, radius=10.0)
    assert len(mesh.faces) > 100_000
    stl = mesh.export(file_type="stl")
    headers = await _setup_and_login(api, tmp_path)
    owner = e2e_db.exec(select(User).where(User.username == "owner")).one()
    expired_job = BackgroundJob(
        id="issue-67-expired-inbox-job",
        owner_user_id=owner.id,
        state="completed",
        status_json='{"state":"completed"}',
        finished_at=utcnow() - timedelta(hours=2),
    )
    e2e_db.add(expired_job)
    e2e_db.flush()
    e2e_db.add(
        InboxItem(
            owner_user_id=owner.id,
            state=InboxItemState.COMPLETED,
            background_job_id=expired_job.id,
        )
    )
    e2e_db.commit()
    monkeypatch.setattr(registry, "_last_persisted_prune_at", float("-inf"))

    uploaded = await api.post(
        "/api/v1/ingest/model",
        files={"file": ("issue-67-dense.stl", stl, "application/sla")},
        data={"model_name": "Issue 67 Dense"},
        headers=headers,
    )
    assert uploaded.status_code == 202, uploaded.text
    job = await _await_job(api, headers, uploaded.json()["job_id"])

    assert job["state"] == "completed", job
    assert job["thumbnail_status"] == "fallback_generated", job
    file_id = job["file_id"]
    thumbnail = await api.get(f"/api/v1/files/{file_id}/thumbnail", headers=headers)
    assert thumbnail.status_code == 200, thumbnail.text
    assert thumbnail.headers["content-type"] == "image/webp"

    with Image.open(io.BytesIO(thumbnail.content)) as image:
        pixels = np.asarray(image.convert("RGBA"))
    visible = pixels[:, :, 3] > 20
    assert visible.mean() > 0.08
    assert float(pixels[:, :, :3][visible].std()) > 8.0
