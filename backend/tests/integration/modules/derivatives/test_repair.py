"""Repairing a broken derivative: forget it at its recipe, then derive it again.

An audit that finds a thumbnail missing from storage cannot trust its row's
"ready". A request path (an administrator's click) invalidates and nudges, so
the work happens in a Job; a caller already inside a Job (the audit's own
automatic repair) derives synchronously, because it must verify the output
before recording the finding as repaired.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session, select

import app.modules.work as work
from app.db.models import (
    ArtifactDerivative,
    DerivativeKind,
    DerivativeState,
    JobKind,
    Model,
)
from app.modules.derivatives import repair
from tests.factories import content


@pytest.fixture
def nudged(monkeypatch) -> list[str]:
    names: list[str] = []
    monkeypatch.setattr(work, "nudge", lambda name, **_: names.append(name))
    return names


def _kinds(session: Session, file_id: int) -> set[str]:
    session.expire_all()
    return {
        row.kind
        for row in session.exec(
            select(ArtifactDerivative).where(ArtifactDerivative.file_id == file_id)
        ).all()
    }


class TestRepresentative:
    def test_is_the_artifact_the_model_already_shows(
        self, db_session: Session, make_model, make_file
    ) -> None:
        model = make_model()
        make_file(model, filename="newer.stl")
        shown = make_file(model, filename="plate.gcode")
        model.thumbnail_file_id = shown.id
        db_session.add(model)
        db_session.commit()

        assert repair.representative(db_session, model.id) == shown

    def test_prefers_a_mesh_when_nothing_is_shown(
        self, db_session: Session, make_model, make_file
    ) -> None:
        # A render of the part represents a Model better than a G-code preview.
        model = make_model()
        mesh = make_file(model, filename="part.stl")
        make_file(model, filename="plate.gcode")

        assert repair.representative(db_session, model.id) == mesh

    def test_a_trashed_thumbnail_source_is_replaced(
        self, db_session: Session, make_model, make_file
    ) -> None:
        model = make_model()
        live = make_file(model, filename="part.stl")
        gone = make_file(model, filename="old.stl", trashed=True)
        model.thumbnail_file_id = gone.id
        db_session.add(model)
        db_session.commit()

        assert repair.representative(db_session, model.id) == live

    def test_a_trashed_model_has_none(
        self, db_session: Session, make_model, make_file
    ) -> None:
        model = make_model(trashed=True)
        make_file(model, filename="part.stl")

        assert repair.representative(db_session, model.id) is None

    def test_a_missing_model_has_none(self, db_session: Session) -> None:
        assert db_session.get(Model, 999_999) is None
        assert repair.representative(db_session, 999_999) is None


class TestRequest:
    def test_makes_the_kind_pending_for_its_producer(
        self, db_session: Session, make_model, make_file, make_derivative, nudged
    ) -> None:
        artifact = make_file(make_model(), filename="part.stl")
        make_derivative(artifact, DerivativeKind.METADATA)
        make_derivative(artifact, DerivativeKind.THUMBNAIL)

        assert repair.request(db_session, artifact, [DerivativeKind.THUMBNAIL]) is True

        assert _kinds(db_session, artifact.id) == {DerivativeKind.METADATA}
        assert nudged == [JobKind.DERIVATIVES_MESH]

    def test_a_kind_no_group_produces_for_the_artifact_nudges_nothing(
        self, db_session: Session, make_model, make_file, nudged
    ) -> None:
        artifact = make_file(make_model(), filename="part.stl")

        assert repair.request(db_session, artifact, ["toolpath"]) is False
        assert nudged == []


class TestNow:
    def test_derives_the_kind_again_in_this_thread(
        self, db_session: Session, stored, make_derivative
    ) -> None:
        artifact = stored("cube.stl", content.binary_stl())
        make_derivative(artifact, DerivativeKind.METADATA)
        make_derivative(
            artifact, DerivativeKind.THUMBNAIL, storage_key="thumbs/missing.webp"
        )

        outcome = repair.now(artifact.id, [DerivativeKind.THUMBNAIL])

        assert outcome == {DerivativeKind.THUMBNAIL: "ready"}
        db_session.expire_all()
        row = db_session.exec(
            select(ArtifactDerivative).where(
                ArtifactDerivative.file_id == artifact.id,
                ArtifactDerivative.kind == DerivativeKind.THUMBNAIL,
            )
        ).one()
        assert row.state == DerivativeState.READY
        assert row.storage_key != "thumbs/missing.webp"

    def test_the_producer_also_derives_whatever_else_is_owed(self, stored) -> None:
        # The G-code group reads the header once for both kinds, so repairing
        # one of a never-derived Artifact yields the other as well.
        artifact = stored("plate.gcode", content.gcode(marker="repair"))

        assert repair.now(artifact.id, [DerivativeKind.METADATA]) == {
            DerivativeKind.METADATA: "ready",
            DerivativeKind.THUMBNAIL: "skipped",
        }

    def test_a_trashed_artifact_is_not_repaired(self, make_model, make_file) -> None:
        artifact = make_file(make_model(), filename="part.stl", trashed=True)

        assert repair.now(artifact.id, [DerivativeKind.THUMBNAIL]) == {}
