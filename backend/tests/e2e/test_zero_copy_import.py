"""E2E: an upload becomes its library file without being copied.

With staging on the library's mount (the single-volume layout), an import's
staged bytes are hard-linked into place, so a multi-gigabyte upload costs no
second copy and no second allocation. The proof is physical: the upload's
staged inode is recorded durably when it is verified, and the stored library
file read back from disk must be that same inode, with no other name.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.db.models import ArtifactUploadSession, File
from app.modules.storage.storage_backend.runtime import init_backend

PAYLOAD = b"999\nzero-copy e2e\n0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n"


async def _upload(api, headers: dict[str, str]) -> str:
    created = await api.post(
        "/api/v1/artifact-uploads",
        json={
            "purpose": "model",
            "target_role": "new_model",
            "filename": "drawing.dxf",
            "media_type": "image/vnd.dxf",
            "size_bytes": len(PAYLOAD),
            "sha256": hashlib.sha256(PAYLOAD).hexdigest(),
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    upload_id = created.json()["id"]
    chunk = await api.put(
        f"/api/v1/artifact-uploads/{upload_id}/chunks/0",
        params={
            "offset": 0,
            "length": len(PAYLOAD),
            "sha256": hashlib.sha256(PAYLOAD).hexdigest(),
        },
        content=PAYLOAD,
        headers=headers,
    )
    assert chunk.status_code == 200, chunk.text
    finalized = await api.post(
        f"/api/v1/artifact-uploads/{upload_id}/finalize", headers=headers
    )
    assert finalized.status_code == 200, finalized.text
    return upload_id


async def _await_upload(api, headers: dict[str, str], upload_id: str) -> None:
    for _ in range(100):
        r = await api.get(f"/api/v1/artifact-uploads/{upload_id}", headers=headers)
        assert r.status_code == 200, r.text
        if r.json()["state"] in ("completed", "failed"):
            assert r.json()["state"] == "completed", r.text
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"upload {upload_id} did not finish: {r.json()}")


def _stored_path(e2e_db: Session) -> Path:
    digest = hashlib.sha256(PAYLOAD).hexdigest()
    return Path(e2e_db.exec(select(File).where(File.sha256 == digest)).one().path)


class TestZeroCopyImport:
    @pytest.mark.asyncio
    async def test_stores_the_staged_inode_itself(
        self, api, superuser_headers: dict[str, str], e2e_db: Session
    ) -> None:
        init_backend()

        upload_id = await _upload(api, superuser_headers)
        await _await_upload(api, superuser_headers, upload_id)

        upload = e2e_db.exec(
            select(ArtifactUploadSession).where(ArtifactUploadSession.id == upload_id)
        ).one()
        staged_inode = json.loads(str(upload.staging_identity_json))["inode"]
        assert os.stat(_stored_path(e2e_db)).st_ino == staged_inode

    @pytest.mark.asyncio
    async def test_leaves_the_stored_file_a_single_name(
        self, api, superuser_headers: dict[str, str], e2e_db: Session
    ) -> None:
        # A second name left in staging would alias library bytes.
        init_backend()

        upload_id = await _upload(api, superuser_headers)
        await _await_upload(api, superuser_headers, upload_id)

        assert os.stat(_stored_path(e2e_db)).st_nlink == 1

    @pytest.mark.asyncio
    async def test_reports_zero_copy_imports_to_the_administrator(
        self, api, superuser_headers: dict[str, str]
    ) -> None:
        init_backend()

        r = await api.get("/api/v1/config", headers=superuser_headers)

        assert r.status_code == 200, r.text
        assert r.json()["storage_probe_diagnostics"]["staged_hardlink"] is True
