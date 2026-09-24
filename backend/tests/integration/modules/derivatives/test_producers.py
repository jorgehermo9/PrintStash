"""Producers: read an Artifact's bytes once, derive what is owed, publish each.

A producer derives only the kinds still needed and records every outcome on the
kind's row: ready (with where the output lives), skipped (nothing to produce
for these bytes), or failed, and a failure is transient (backs off) unless
retrying the same bytes could never help. Viewers of the Model are told each
time one of its derivatives changes, so a placeholder turns into a thumbnail
without a reload.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.core.config import _overlay, settings
from app.core.errors import ErrorKind, OperationError
from app.db.models import (
    ArtifactDerivative,
    DerivativeState,
    File,
    FileType,
    Metadata,
    Model,
)
from app.modules.derivatives import producers
from app.modules.derivatives.kinds import METADATA, THUMBNAIL, TOOLPATH
from app.modules.media import toolpath
from app.modules.media.thumbnail_publication import ThumbnailPublicationError
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.work import events
from tests.factories import build_file, build_model, content
from tests.paths import FIXTURES_DIR


@pytest.fixture
def stored(db_session: Session):
    """An Artifact whose bytes are on the backend, as ingestion leaves it."""

    def build(filename: str, data: bytes, **fields) -> File:
        model = build_model(db_session, filename)
        row = build_file(
            db_session,
            model,
            filename=filename,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            **fields,
        )
        get_backend().write_bytes(data, row.path)
        return row

    return build


@pytest.fixture
def remove_blob_key():
    """Empty a storage key, the way a delete outside PrintStash would."""

    def remove(key: str) -> None:
        direct = get_backend().direct_path(key)
        assert direct is not None
        direct.unlink(missing_ok=True)

    return remove


@pytest.fixture
def announced() -> Iterator[list[dict]]:
    notices: list[dict] = []

    class Recorder:
        def publish_threadsafe(self, channel, payload):
            notices.append({"channel": channel, **payload})

    events.bind(Recorder())
    try:
        yield notices
    finally:
        events.bind(None)


def _rows(session: Session, file_id: int) -> dict[str, ArtifactDerivative]:
    session.expire_all()
    return {
        row.kind: row
        for row in session.exec(
            select(ArtifactDerivative).where(ArtifactDerivative.file_id == file_id)
        ).all()
    }


class TestDeriveMesh:
    def test_derives_every_mesh_kind_from_one_load(
        self, db_session: Session, stored, announced
    ) -> None:
        artifact = stored("cube.stl", content.binary_stl())

        outcome = producers.derive_mesh(artifact.id)

        assert outcome.kinds == {METADATA: "ready", THUMBNAIL: "ready"}
        rows = _rows(db_session, artifact.id)
        assert {kind: row.state for kind, row in rows.items()} == {
            METADATA: DerivativeState.READY,
            THUMBNAIL: DerivativeState.READY,
        }
        meta = db_session.exec(
            select(Metadata).where(Metadata.file_id == artifact.id)
        ).one()
        assert meta.triangle_count == 12
        published = db_session.get(File, artifact.id)
        assert published is not None and published.thumbnail_path
        assert get_backend().exists(published.thumbnail_path)
        assert rows[THUMBNAIL].storage_key == published.thumbnail_path

    def test_the_thumbnail_represents_its_model(
        self, db_session: Session, stored
    ) -> None:
        artifact = stored("cube.stl", content.binary_stl())

        producers.derive_mesh(artifact.id)

        model = db_session.get(Model, artifact.model_id)
        assert model is not None
        assert model.thumbnail_file_id == artifact.id

    def test_tells_the_models_viewers_what_changed(self, stored, announced) -> None:
        artifact = stored("cube.stl", content.binary_stl())

        producers.derive_mesh(artifact.id)

        assert {(n["channel"], n["kind"], n["state"]) for n in announced} == {
            (f"model:{artifact.model_id}", METADATA, "ready"),
            (f"model:{artifact.model_id}", THUMBNAIL, "ready"),
        }

    def test_derives_only_what_is_still_owed(
        self, db_session: Session, stored, make_derivative
    ) -> None:
        artifact = stored("cube.stl", content.binary_stl())
        make_derivative(artifact, METADATA)

        outcome = producers.derive_mesh(artifact.id)

        assert outcome.kinds == {THUMBNAIL: "ready"}
        assert (
            db_session.exec(
                select(Metadata).where(Metadata.file_id == artifact.id)
            ).first()
            is None
        )

    def test_a_fully_derived_artifact_is_left_alone(
        self, stored, make_derivative, announced
    ) -> None:
        artifact = stored("cube.stl", content.binary_stl())
        make_derivative(artifact, METADATA)
        make_derivative(artifact, THUMBNAIL)

        assert producers.derive_mesh(artifact.id).kinds == {}
        assert announced == []

    def test_a_trashed_artifact_is_not_derived(
        self, db_session: Session, stored
    ) -> None:
        from app.core.time import utcnow

        artifact = stored("cube.stl", content.binary_stl(), deleted_at=utcnow())

        assert producers.derive_mesh(artifact.id).kinds == {}
        assert _rows(db_session, artifact.id) == {}

    def test_unreadable_bytes_fail_transiently(
        self, db_session: Session, stored, remove_blob_key
    ) -> None:
        # The object may come back (a storage outage); a retry can succeed.
        artifact = stored("cube.stl", content.binary_stl())
        remove_blob_key(artifact.path)

        outcome = producers.derive_mesh(artifact.id)

        assert outcome.kinds == {METADATA: "failed", THUMBNAIL: "failed"}
        rows = _rows(db_session, artifact.id)
        assert all(row.failure_reason == "invalid_source" for row in rows.values())
        assert all(row.next_attempt_at is not None for row in rows.values())

    def test_a_render_that_cannot_work_is_terminal(
        self, db_session: Session, stored
    ) -> None:
        # Bytes that parse to no geometry will never render; retrying only
        # burns render capacity.
        artifact = stored("broken.stl", b"solid broken\nendsolid broken\n")

        outcome = producers.derive_mesh(artifact.id)

        assert outcome.kinds[THUMBNAIL] == "failed"
        row = _rows(db_session, artifact.id)[THUMBNAIL]
        assert row.attempts == settings.derivative_max_attempts
        assert row.next_attempt_at is None

    def test_a_storage_failure_while_publishing_is_transient(
        self, db_session: Session, stored, monkeypatch
    ) -> None:
        artifact = stored("cube.stl", content.binary_stl())

        def collision(*_args, **_kwargs):
            raise ThumbnailPublicationError("thumbnail_key_collision")

        monkeypatch.setattr(producers, "publish_thumbnail", collision)

        outcome = producers.derive_mesh(artifact.id)

        assert outcome.kinds == {METADATA: "ready", THUMBNAIL: "failed"}
        row = _rows(db_session, artifact.id)[THUMBNAIL]
        assert (row.failure_reason, row.next_attempt_at is not None) == (
            "storage",
            True,
        )


class TestDeriveGcode:
    def test_derives_every_gcode_kind_from_one_read(
        self, db_session: Session, stored
    ) -> None:
        data = (FIXTURES_DIR / "real_prusa_mk4_spatula.gcode").read_bytes()
        artifact = stored("spatula.gcode", data)

        outcome = producers.derive_gcode(artifact.id)

        assert outcome.kinds == {METADATA: "ready", THUMBNAIL: "ready"}
        meta = db_session.exec(
            select(Metadata).where(Metadata.file_id == artifact.id)
        ).one()
        assert meta.slicer_name
        rows = _rows(db_session, artifact.id)
        assert json.loads(rows[THUMBNAIL].output_json)["strategy"] == "embedded"

    def test_gcode_without_an_embedded_image_skips_its_thumbnail(
        self, db_session: Session, stored
    ) -> None:
        artifact = stored("plain.gcode", content.gcode(marker="plain"))

        outcome = producers.derive_gcode(artifact.id)

        assert outcome.kinds == {METADATA: "ready", THUMBNAIL: "skipped"}
        row = _rows(db_session, artifact.id)[THUMBNAIL]
        assert row.failure_reason == "no_embedded_thumbnail"

    def test_records_the_material_each_tool_needs(
        self, db_session: Session, stored
    ) -> None:
        from app.db.models import ArtifactMaterialRequirement

        data = (FIXTURES_DIR / "real_prusa_mk4_spatula.gcode").read_bytes()
        artifact = stored("spatula.gcode", data)

        producers.derive_gcode(artifact.id)

        requirements = db_session.exec(
            select(ArtifactMaterialRequirement).where(
                ArtifactMaterialRequirement.file_id == artifact.id
            )
        ).all()
        assert [req.tool_index for req in requirements] == [0]

    def test_a_profile_detection_failure_does_not_fail_the_metadata(
        self, db_session: Session, stored, monkeypatch
    ) -> None:
        from app.modules.printing import profile_detection

        def broken(*_args, **_kwargs):
            raise RuntimeError("profile table missing")

        monkeypatch.setattr(profile_detection, "upsert_detected_profiles", broken)
        artifact = stored("plain.gcode", content.gcode(marker="profiles"))

        assert producers.derive_gcode(artifact.id).kinds[METADATA] == "ready"

    def test_unreadable_bytes_fail_transiently(
        self, db_session: Session, stored, remove_blob_key
    ) -> None:
        artifact = stored("gone.gcode", content.gcode(marker="gone"))
        remove_blob_key(artifact.path)

        outcome = producers.derive_gcode(artifact.id)

        assert outcome.kinds == {METADATA: "failed", THUMBNAIL: "failed"}


class TestDeriveToolpath:
    @pytest.fixture
    def bgcode(self, stored):
        return stored(
            "plate.bgcode",
            b"GCDE\x01\x00\x00\x00\x01\x00" + b"\x00" * 16,
            file_type=FileType.GCODE,
        )

    # The converter subprocess itself is covered in media/test_toolpath.py;
    # here the boundary is stood in for so only the producer is under test.

    def test_publishes_a_converted_toolpath(
        self, db_session: Session, bgcode, monkeypatch
    ) -> None:
        def converted(_file, directory: Path) -> Path:
            output = directory / "input.gcode"
            output.write_bytes(b"G1 X1\n")
            return output

        monkeypatch.setattr(toolpath, "convert", converted)

        outcome = producers.derive_toolpath(bgcode.id)

        assert outcome.kinds == {TOOLPATH: "ready"}
        row = _rows(db_session, bgcode.id)[TOOLPATH]
        assert row.storage_key is not None
        assert get_backend().read_bytes(row.storage_key) == b"G1 X1\n"

    @pytest.mark.parametrize(
        "kind", [ErrorKind.UNPROCESSABLE, ErrorKind.TOO_LARGE, ErrorKind.NOT_FOUND]
    )
    def test_a_container_that_cannot_convert_is_terminal(
        self, db_session: Session, bgcode, monkeypatch, kind: ErrorKind
    ) -> None:
        def refused(*_args):
            raise OperationError("toolpath_invalid_bgcode", kind=kind)

        monkeypatch.setattr(toolpath, "convert", refused)

        outcome = producers.derive_toolpath(bgcode.id)

        assert outcome.kinds == {TOOLPATH: "failed"}
        row = _rows(db_session, bgcode.id)[TOOLPATH]
        assert (row.failure_reason, row.next_attempt_at) == (
            "toolpath_invalid_bgcode",
            None,
        )

    def test_a_vanished_source_is_transient(
        self, db_session: Session, bgcode, remove_blob_key
    ) -> None:
        remove_blob_key(bgcode.path)

        outcome = producers.derive_toolpath(bgcode.id)

        assert outcome.kinds == {TOOLPATH: "failed"}
        row = _rows(db_session, bgcode.id)[TOOLPATH]
        assert row.failure_reason == "file_blob_unavailable"
        assert row.next_attempt_at is not None

    def test_a_missing_converter_is_transient(
        self, db_session: Session, bgcode
    ) -> None:
        # Installing the converter fixes it; the derivative should come back.
        _overlay["bgcode_executable"] = "/nonexistent/printstash-bgcode"

        outcome = producers.derive_toolpath(bgcode.id)

        assert outcome.kinds == {TOOLPATH: "failed"}
        row = _rows(db_session, bgcode.id)[TOOLPATH]
        assert row.failure_reason == "toolpath_converter_unavailable"
        assert row.next_attempt_at is not None

    def test_a_toolpath_already_derived_is_left_alone(
        self, bgcode, make_derivative
    ) -> None:
        make_derivative(bgcode, TOOLPATH)

        assert producers.derive_toolpath(bgcode.id).kinds == {}


class TestOutcome:
    def test_reports_its_kinds_in_a_stable_order(self) -> None:
        outcome = producers.Outcome({THUMBNAIL: "ready", METADATA: "failed"})

        assert list(outcome.as_result()["derivatives"]) == [METADATA, THUMBNAIL]
