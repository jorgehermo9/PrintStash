"""Public-API scenario run in an installation with related packages removed.

This process runs the production composition end to end, including the real
DBOS job engine, so every accepted request is followed to its Job's outcome
the way a client does: by polling ``GET /jobs/{id}``.
"""

import hashlib
import io
import json
import struct
import time
import zipfile
from importlib.util import find_spec

from fastapi.testclient import TestClient

from app.core.config import ensure_dirs, settings
from app.main import app
from tests.paths import BACKEND_DIR, TESTDATA_DIR, require_fixtures

_DEADLINE_S = 90.0


def run() -> None:
    assert find_spec("app.modules.similarity") is None
    assert find_spec("app.modules.inference") is None
    meshes = [
        TESTDATA_DIR / "benchy" / "3dbenchy.stl",
        TESTDATA_DIR / "Spatula_Printables_IS.3mf",
    ]
    require_fixtures(*meshes)
    ensure_dirs()
    with TestClient(app) as client:
        from app.modules.work.catalog import get_catalog

        # Without the package, similarity work is not even defined.
        assert not any(
            name.startswith("similarity.") for name in get_catalog().definitions
        )

        def request(method, path, *, status=200, **kwargs):
            response = client.request(method, f"/api/v1{path}", **kwargs)
            assert response.status_code == status, response.text
            return response

        def finished(job_id: str) -> dict:
            deadline = time.monotonic() + _DEADLINE_S
            while True:
                job = request("GET", f"/jobs/{job_id}").json()
                if job["state"] in {"completed", "failed", "cancelled"}:
                    return job
                assert time.monotonic() < deadline, job
                time.sleep(0.1)

        def derived(file_id: int) -> None:
            deadline = time.monotonic() + _DEADLINE_S
            while True:
                rows = request("GET", f"/files/{file_id}/derivatives").json()
                if all(row["state"] in {"ready", "skipped"} for row in rows):
                    return
                assert not any(row["state"] == "failed" for row in rows), rows
                assert time.monotonic() < deadline, rows
                time.sleep(0.1)

        client.headers["Origin"] = "http://testserver"
        preparation = request("POST", "/setup/session").json()
        client.headers["X-PrintStash-Setup-CSRF"] = preparation["csrf"]
        setup = request(
            "POST",
            "/setup",
            status=201,
            json={
                "username": "owner",
                "password": "Password123",
                "storage_backend": "local",
                "data_dir": str(settings.data_dir),
                "thumb_dir": str(settings.thumb_dir),
            },
        ).json()
        client.headers["Authorization"] = f"Bearer {setup['access_token']}"
        request("GET", "/models/1/similar-text", status=503)
        model_ids = []
        revisions = {}
        for mesh in meshes:
            uploaded = request(
                "POST",
                "/ingest/model",
                status=202,
                files={
                    "file": (mesh.name, mesh.read_bytes(), "application/octet-stream")
                },
            ).json()
            job = finished(uploaded["job_id"])
            assert job["state"] == "completed", job
            derived(job["file_id"])
            model_ids.append(job["model_id"])
            gcode = (
                (
                    BACKEND_DIR / "tests/fixtures/real_orca_ender3_benchy.gcode"
                ).read_bytes()
                + f"\n; Independent Family Model {job['model_id']}\n".encode()
            )
            sliced = request(
                "POST",
                "/ingest/orca",
                status=202,
                data={"source_hash": hashlib.sha256(mesh.read_bytes()).hexdigest()},
                files={"file": (f"{mesh.stem}.gcode", gcode, "text/plain")},
            ).json()
            revision = finished(sliced["job_id"])
            assert revision["state"] == "completed", revision
            assert revision["model_id"] == job["model_id"]
            revisions[job["model_id"]] = (revision["file_id"], gcode)

        family = request(
            "POST",
            "/families",
            status=201,
            json={
                "name": "Manual workshop",
                "description": "Independent assembly options",
                "canonical_model_id": model_ids[0],
                "members": [{"model_id": model_id} for model_id in model_ids],
            },
        ).json()
        path = f"/families/{family['id']}"
        members = request("GET", f"{path}/members").json()["items"]
        assert len(members) == 2
        for member, mesh in zip(members, meshes, strict=True):
            assert member["source_file_count"] == 1
            assert member["gcode_revision_count"] == 1
            preview = member["preview_file"]
            assert preview["metadata"]["triangle_count"] > 6000
            download = request("GET", f"/files/{preview['id']}/download")
            assert download.content == mesh.read_bytes()
            converted = request("GET", f"/files/{preview['id']}/stl").content
            faces = struct.unpack_from("<I", converted, 80)[0]
            assert faces > 6000
            assert len(converted) == 84 + faces * 50
        family = request(
            "POST",
            f"{path}/canonical",
            json={
                "version": family["version"],
                "member_id": members[1]["id"],
                "previous_role": "repaired",
            },
        ).json()
        assert family["canonical_model_id"] == model_ids[1]
        for query in ("Manual workshop", "Independent assembly options"):
            page = request("GET", "/families/browse", params={"q": query}).json()
            assert page["total"] == 1
            assert page["items"][0]["family"]["member_count"] == 2
        saved = request(
            "POST",
            "/saved-views",
            status=201,
            json={
                "name": "Workshop",
                "filters": {
                    "family_id": family["id"],
                    "browse": "families_collapsed",
                },
            },
        ).json()
        assert (
            request("GET", f"/saved-views/{saved['id']}").json()["filters"]["family_id"]
            == family["id"]
        )
        multipart = request(
            "POST", "/multipart-models", status=201, json={"name": "Workshop kit"}
        ).json()
        multipart = request(
            "PUT",
            f"/multipart-models/{multipart['id']}/parts",
            json={
                "parts": [
                    {
                        "name": "Option",
                        "choices": [{"model_id": mid} for mid in model_ids],
                    }
                ],
            },
        ).json()
        assert [choice["id"] for choice in multipart["parts"][0]["models"]] == model_ids
        request("DELETE", path, params={"version": family["version"]}, status=204)
        request("GET", path, status=404)
        for mid in model_ids:
            model = request("GET", f"/models/{mid}").json()
            assert len(model["files"]) == 2
            revision_id, original = revisions[mid]
            assert request("GET", f"/files/{revision_id}/download").content == original
        restored = request(
            "POST", f"{path}/restore", json={"version": family["version"] + 1}
        ).json()
        assert restored["omitted_member_ids"] == []
        archive = request("GET", "/models/library-archive").content
        with zipfile.ZipFile(io.BytesIO(archive)) as portable:
            manifest = json.loads(portable.read("manifest.json"))
        assert manifest["format"] == "printstash-library-v2"
        assert len(manifest["families"]) == 1
        imported = request(
            "POST",
            "/models/library-import",
            status=202,
            files={
                "file": ("library.zip", archive, "application/zip"),
            },
        ).json()
        job = finished(imported["job_id"])
        assert job["state"] == "completed", job
        assert request("GET", "/families").json()["total"] == 1
        assert request("GET", path).json()["canonical_model_id"] == model_ids[1]
    print("family-independent-flow-complete")


if __name__ == "__main__":
    run()
