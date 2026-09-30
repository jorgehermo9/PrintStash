"""Orca intake attaches slices only when native lineage names one exact source.

The hook runs on another machine, so the server must treat its context as
untrusted lookup evidence: exact, live, editable, unique, and single-object.
Anything else either remains a standalone plate in permissive mode or is
rejected before bytes are staged; it must never attach to the first object by
accident.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import settings
from app.db.models import File, FileRevisionStatus, FileType, Job, Metadata, Model
from app.db.scopes import live
from app.schemas.orca import OrcaNativeContext
from tests._env import use_local_storage
from tests.factories import bearer
from tests.integration.api.v1._ingest_assertions import completed_job
from tests.paths import FIXTURES_DIR

GCODE = (FIXTURES_DIR / "real_orca_ender3_benchy.gcode").read_bytes()


@pytest.fixture
def single_context() -> dict:
    return {
        "version": 1,
        "classification": "single_object",
        "source": {
            "filename": "3dbenchy.stl",
            "basename": "3dbenchy",
            "first_object_name": "3dbenchy.stl",
            "object_count": 1,
            "instance_count": 1,
            "plate_name": None,
            "project_name": None,
            "object_labels": [
                {"name": "3dbenchy.stl", "object_id": "0", "copy_index": 0}
            ],
        },
        "slicer": {"name": "OrcaSlicer", "version": "2.3.2"},
        "printer": {"model": "Ender-3 V3 SE", "preset": "Ender profile"},
        "filaments": [{"type": "PLA", "preset": "Generic PLA"}],
        "process": {"preset": "Balanced Quality"},
        "print_stats": {"estimated_time_s": 4296},
        "field_sources": {"source.filename": "gcode_object_label"},
    }


def _orca_data(context: dict, **extra: str) -> dict[str, str]:
    return {"native_context": json.dumps(context), **extra}


class TestIngestOrcaNativeContext:
    def test_attaches_a_single_object_export_to_its_source_model(
        self,
        tmp_path: Path,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
        make_model,
        make_file,
        single_context: dict,
    ) -> None:
        use_local_storage(tmp_path)
        model = make_model("3DBenchy")
        make_file(model, file_type=FileType.STL, filename="3dbenchy.stl")

        payload = completed_job(
            client,
            client.post(
                "/api/v1/ingest/orca",
                headers=auth_headers,
                files={"file": ("benchy.gcode", GCODE, "text/plain")},
                data=_orca_data(single_context),
            ),
        )

        revision = db_session.get(File, payload["file_id"])
        assert revision is not None
        assert revision.model_id == model.id

    def test_attaches_to_an_existing_3mf_source_model(
        self,
        tmp_path: Path,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
        make_model,
        make_file,
        single_context: dict,
    ) -> None:
        use_local_storage(tmp_path)
        model = make_model("Workshop project")
        make_file(model, file_type=FileType.THREE_MF, filename="workshop.3mf")
        single_context["source"]["filename"] = "workshop.3mf"
        single_context["source"]["basename"] = "workshop"
        single_context["source"]["first_object_name"] = "workshop.3mf"
        single_context["source"]["object_labels"][0]["name"] = "workshop.3mf"

        payload = completed_job(
            client,
            client.post(
                "/api/v1/ingest/orca",
                headers=auth_headers,
                files={"file": ("workshop.gcode", GCODE, "text/plain")},
                data=_orca_data(single_context),
            ),
        )

        revision = db_session.get(File, payload["file_id"])
        assert revision is not None
        assert revision.model_id == model.id

    def test_marks_an_attached_export_as_needing_test(
        self,
        tmp_path: Path,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
        make_model,
        make_file,
        single_context: dict,
    ) -> None:
        use_local_storage(tmp_path)
        model = make_model("3DBenchy")
        make_file(model, file_type=FileType.STL, filename="3dbenchy.stl")

        payload = completed_job(
            client,
            client.post(
                "/api/v1/ingest/orca",
                headers=auth_headers,
                files={"file": ("benchy.gcode", GCODE, "text/plain")},
                data=_orca_data(single_context),
            ),
        )

        revision = db_session.get(File, payload["file_id"])
        assert revision is not None
        assert revision.revision_status == FileRevisionStatus.NEEDS_TEST

    def test_does_not_recommend_an_attached_export_implicitly(
        self,
        tmp_path: Path,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
        make_model,
        make_file,
        single_context: dict,
    ) -> None:
        use_local_storage(tmp_path)
        model = make_model("3DBenchy")
        make_file(model, file_type=FileType.STL, filename="3dbenchy.stl")

        payload = completed_job(
            client,
            client.post(
                "/api/v1/ingest/orca",
                headers=auth_headers,
                files={"file": ("benchy.gcode", GCODE, "text/plain")},
                data=_orca_data(single_context),
            ),
        )

        revision = db_session.get(File, payload["file_id"])
        assert revision is not None
        assert revision.is_recommended is False

    def test_honors_the_explicit_revision_contract(
        self,
        tmp_path: Path,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
        make_model,
        make_file,
        single_context: dict,
    ) -> None:
        use_local_storage(tmp_path)
        model = make_model("3DBenchy")
        make_file(model, file_type=FileType.STL, filename="3dbenchy.stl")
        data = _orca_data(
            single_context,
            revision_label="PETG baseline",
            revision_status="known_good",
            revision_notes="Validated on the workshop printer",
            is_recommended="true",
        )

        payload = completed_job(
            client,
            client.post(
                "/api/v1/ingest/orca",
                headers=auth_headers,
                files={"file": ("benchy.gcode", GCODE, "text/plain")},
                data=data,
            ),
        )

        revision = db_session.get(File, payload["file_id"])
        assert revision is not None
        assert (
            revision.revision_label,
            revision.revision_status,
            revision.revision_notes,
            revision.is_recommended,
        ) == (
            "PETG baseline",
            FileRevisionStatus.KNOWN_GOOD,
            "Validated on the workshop printer",
            True,
        )

    def test_retains_the_normalized_context_on_the_revision(
        self,
        tmp_path: Path,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
        make_model,
        make_file,
        single_context: dict,
    ) -> None:
        use_local_storage(tmp_path)
        model = make_model("3DBenchy")
        make_file(model, file_type=FileType.STL, filename="3dbenchy.stl")
        payload = completed_job(
            client,
            client.post(
                "/api/v1/ingest/orca",
                headers=auth_headers,
                files={"file": ("benchy.gcode", GCODE, "text/plain")},
                data=_orca_data(single_context),
            ),
        )

        metadata = db_session.exec(
            select(Metadata).where(Metadata.file_id == payload["file_id"])
        ).one()

        assert json.loads(metadata.native_context_json or "null") == single_context

    def test_exposes_the_normalized_context_in_model_detail(
        self,
        tmp_path: Path,
        client: TestClient,
        auth_headers: dict[str, str],
        make_model,
        make_file,
        single_context: dict,
    ) -> None:
        use_local_storage(tmp_path)
        model = make_model("3DBenchy")
        make_file(model, file_type=FileType.STL, filename="3dbenchy.stl")
        payload = completed_job(
            client,
            client.post(
                "/api/v1/ingest/orca",
                headers=auth_headers,
                files={"file": ("benchy.gcode", GCODE, "text/plain")},
                data=_orca_data(single_context),
            ),
        )

        response = client.get(f"/api/v1/models/{model.id}", headers=auth_headers)
        revision = next(
            row for row in response.json()["files"] if row["id"] == payload["file_id"]
        )

        assert revision["metadata"][
            "native_context"
        ] == OrcaNativeContext.model_validate(single_context).model_dump(mode="json")

    def test_accepts_a_gcode_3mf_container(
        self,
        tmp_path: Path,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
        make_model,
        make_file,
        single_context: dict,
    ) -> None:
        use_local_storage(tmp_path)
        model = make_model("3DBenchy")
        make_file(model, file_type=FileType.STL, filename="3dbenchy.stl")
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("Metadata/plate_1.gcode", GCODE)

        payload = completed_job(
            client,
            client.post(
                "/api/v1/ingest/orca",
                headers=auth_headers,
                files={
                    "file": (
                        "benchy.gcode.3mf",
                        archive.getvalue(),
                        "model/3mf",
                    )
                },
                data=_orca_data(single_context),
            ),
        )

        revision = db_session.get(File, payload["file_id"])
        assert revision is not None
        assert revision.file_type == FileType.GCODE

    def test_keeps_legacy_context_free_uploads_standalone_in_permissive_mode(
        self,
        tmp_path: Path,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
    ) -> None:
        use_local_storage(tmp_path)

        payload = completed_job(
            client,
            client.post(
                "/api/v1/ingest/orca",
                headers=auth_headers,
                files={"file": ("legacy-plate.gcode", GCODE, "text/plain")},
            ),
        )

        plate = db_session.get(Model, payload["model_id"])
        revision = db_session.get(File, payload["file_id"])
        assert plate is not None
        assert revision is not None
        assert plate.name == "legacy-plate"
        assert revision.model_id == plate.id

    def test_rejects_an_unknown_source(
        self,
        tmp_path: Path,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
        single_context: dict,
    ) -> None:
        use_local_storage(tmp_path)
        response = client.post(
            "/api/v1/ingest/orca",
            headers=auth_headers,
            files={"file": ("benchy.gcode", GCODE, "text/plain")},
            data=_orca_data(single_context),
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "orca_source_not_found"
        assert db_session.exec(select(Job)).all() == []
        assert list(settings.incoming_dir.iterdir()) == []

    def test_rejects_an_ambiguous_source(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        make_model,
        make_file,
        single_context: dict,
    ) -> None:
        first = make_model("First Benchy")
        make_file(first, file_type=FileType.STL, filename="3dbenchy.stl")
        second = make_model("Second Benchy")
        make_file(second, file_type=FileType.STL, filename="3dbenchy.stl")

        response = client.post(
            "/api/v1/ingest/orca",
            headers=auth_headers,
            files={"file": ("benchy.gcode", GCODE, "text/plain")},
            data=_orca_data(single_context),
        )

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "orca_source_ambiguous"

    def test_ignores_a_trashed_source_artifact(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        make_model,
        make_file,
        single_context: dict,
    ) -> None:
        model = make_model("Trashed source")
        make_file(
            model,
            file_type=FileType.STL,
            filename="3dbenchy.stl",
            trashed=True,
        )

        response = client.post(
            "/api/v1/ingest/orca",
            headers=auth_headers,
            files={"file": ("benchy.gcode", GCODE, "text/plain")},
            data=_orca_data(single_context),
        )

        assert response.status_code == 404, response.text

    def test_hides_a_source_the_caller_cannot_edit(
        self,
        client: TestClient,
        make_user,
        make_collection,
        make_model,
        make_file,
        single_context: dict,
    ) -> None:
        collection = make_collection("Private")
        model = make_model("Private Benchy", collection=collection)
        make_file(model, file_type=FileType.STL, filename="3dbenchy.stl")
        user = make_user("orca-outsider")

        response = client.post(
            "/api/v1/ingest/orca",
            headers=bearer(user, scope="write"),
            files={"file": ("benchy.gcode", GCODE, "text/plain")},
            data=_orca_data(single_context),
        )

        assert response.status_code == 404, response.text

    def test_keeps_a_multi_object_plate_standalone_in_permissive_mode(
        self,
        tmp_path: Path,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
        make_model,
        make_file,
        single_context: dict,
    ) -> None:
        use_local_storage(tmp_path)
        first_model = make_model("First object")
        make_file(first_model, file_type=FileType.STL, filename="3dbenchy.stl")
        single_context["classification"] = "multi_object"
        single_context["source"]["object_count"] = 2
        single_context["source"]["project_name"] = "Two-part plate"
        single_context["source"]["object_labels"].append(
            {"name": "stand.stl", "object_id": "1", "copy_index": 0}
        )

        payload = completed_job(
            client,
            client.post(
                "/api/v1/ingest/orca",
                headers=auth_headers,
                files={"file": ("plate.gcode", GCODE, "text/plain")},
                data=_orca_data(single_context),
            ),
        )

        plate = db_session.get(Model, payload["model_id"])
        assert plate is not None
        assert plate.id != first_model.id
        assert plate.name == "Two-part plate"

    def test_rejects_a_multi_object_plate_in_strict_mode(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        single_context: dict,
    ) -> None:
        single_context["classification"] = "multi_object"
        single_context["source"]["object_count"] = 2

        response = client.post(
            "/api/v1/ingest/orca",
            headers=auth_headers,
            files={"file": ("plate.gcode", GCODE, "text/plain")},
            data=_orca_data(single_context, strict_mapping="true"),
        )

        assert response.status_code == 422, response.text
        assert response.json()["detail"] == "orca_multi_object_not_attachable"

    def test_rejects_a_legacy_source_hash_without_native_resolution(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        single_context: dict,
    ) -> None:
        single_context["classification"] = "unknown"
        single_context["source"]["filename"] = None
        single_context["source"]["object_count"] = None
        single_context["source"]["object_labels"] = []

        response = client.post(
            "/api/v1/ingest/orca",
            headers=auth_headers,
            files={"file": ("plate.gcode", GCODE, "text/plain")},
            data=_orca_data(single_context, source_hash="a" * 64),
        )

        assert response.status_code == 422, response.text
        assert response.json()["detail"] == "orca_source_identity_conflict"

    def test_rejects_missing_native_identity_in_strict_mode(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.post(
            "/api/v1/ingest/orca",
            headers=auth_headers,
            files={"file": ("plate.gcode", GCODE, "text/plain")},
            data={"strict_mapping": "true"},
        )

        assert response.status_code == 422, response.text
        assert response.json()["detail"] == "orca_source_metadata_required"

    def test_rejects_malformed_native_context(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.post(
            "/api/v1/ingest/orca",
            headers=auth_headers,
            files={"file": ("plate.gcode", GCODE, "text/plain")},
            data={"native_context": '{"version":2}'},
        )

        assert response.status_code == 422, response.text
        assert response.json()["detail"] == "orca_native_context_invalid"

    def test_rejects_an_invalid_submission_id(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        make_model,
        make_file,
        single_context: dict,
    ) -> None:
        model = make_model("3DBenchy")
        make_file(model, file_type=FileType.STL, filename="3dbenchy.stl")

        response = client.post(
            "/api/v1/ingest/orca",
            headers=auth_headers,
            files={"file": ("benchy.gcode", GCODE, "text/plain")},
            data=_orca_data(single_context, submission_id="not-a-sha256"),
        )

        assert response.status_code == 422, response.text
        assert response.json()["detail"] == "orca_submission_id_invalid"

    def test_repeating_a_submission_creates_one_revision(
        self,
        tmp_path: Path,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
        make_model,
        make_file,
        single_context: dict,
    ) -> None:
        use_local_storage(tmp_path)
        model = make_model("3DBenchy")
        make_file(model, file_type=FileType.STL, filename="3dbenchy.stl")
        data = _orca_data(single_context, submission_id="a" * 64)
        first = completed_job(
            client,
            client.post(
                "/api/v1/ingest/orca",
                headers=auth_headers,
                files={"file": ("benchy.gcode", GCODE, "text/plain")},
                data=data,
            ),
        )

        second = completed_job(
            client,
            client.post(
                "/api/v1/ingest/orca",
                headers=auth_headers,
                files={"file": ("benchy.gcode", GCODE, "text/plain")},
                data=data,
            ),
        )

        revisions = db_session.exec(
            select(File).where(
                File.model_id == model.id,
                File.file_type == FileType.GCODE,
                live(File),
            )
        ).all()
        assert second["file_id"] == first["file_id"]
        assert [revision.id for revision in revisions] == [first["file_id"]]

    @pytest.mark.parametrize(
        "data",
        [
            pytest.param(
                {"native_context": '{"version":2}'},
                id="malformed-context",
            ),
            pytest.param(
                {"strict_mapping": "true"},
                id="strict-without-context",
            ),
        ],
    )
    def test_client_rejections_do_not_stage_bytes(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
        data: dict[str, str],
        tmp_path: Path,
    ) -> None:
        use_local_storage(tmp_path)
        response = client.post(
            "/api/v1/ingest/orca",
            headers=auth_headers,
            files={"file": ("plate.gcode", GCODE, "text/plain")},
            data=data,
        )

        staged = (
            list(settings.incoming_dir.iterdir())
            if settings.incoming_dir.exists()
            else []
        )
        assert response.status_code == 422
        assert db_session.exec(select(Job)).all() == []
        assert staged == []
