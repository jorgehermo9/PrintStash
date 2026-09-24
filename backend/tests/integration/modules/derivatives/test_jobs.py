"""The derivative Jobs: one per producer group, pulled by the anti-join source.

A committed Artifact nudges every group that applies to it; the pass finds the
gap and a Job derives it. Cancelling a Job withdraws the kinds it had left
(they stay cancelled until a retry); a Job that fails outright fails the kinds
it had in flight, so none is left looking busy; a retry forgets the group's
unsuccessful rows so the source offers the Artifact again.
"""

from __future__ import annotations

import json

import pytest
from sqlmodel import Session, select

import app.modules.work as work
from app.core.time import utcnow
from app.db.models import ArtifactDerivative, DerivativeState, Job, JobState
from app.modules.derivatives import jobs as derivative_jobs
from app.modules.derivatives import producers
from app.modules.derivatives.kinds import (
    GCODE_DEFINITION,
    MESH_DEFINITION,
    METADATA,
    THUMBNAIL,
    TOOLPATH,
    TOOLPATH_DEFINITION,
)
from app.modules.derivatives.source import DerivativeSource, subject_key
from app.modules.work.catalog import DERIVE_LIGHT, DERIVE_NATIVE
from tests.factories import content
from tests.integration.api.v1._ingest_assertions import drain_work

DEFINITIONS = {
    definition.name: definition for definition in derivative_jobs.definitions()
}


def _states(session: Session, file_id: int) -> dict[str, DerivativeState]:
    session.expire_all()
    return {
        row.kind: row.state
        for row in session.exec(
            select(ArtifactDerivative).where(ArtifactDerivative.file_id == file_id)
        ).all()
    }


def _job(session: Session, definition: str) -> Job:
    session.expire_all()
    job = session.exec(select(Job).where(Job.kind == definition)).first()
    assert job is not None
    return job


class TestDefinitions:
    @pytest.mark.parametrize(
        ("name", "lane"),
        [
            (MESH_DEFINITION, DERIVE_NATIVE),
            (GCODE_DEFINITION, DERIVE_LIGHT),
            (TOOLPATH_DEFINITION, DERIVE_NATIVE),
        ],
    )
    def test_each_group_runs_in_the_lane_its_cost_needs(
        self, name: str, lane: str
    ) -> None:
        # Native renders and conversions share the memory-budgeted lane; a
        # G-code header read is light.
        assert DEFINITIONS[name].lane == lane

    def test_each_group_pulls_its_own_gaps(self) -> None:
        for name, definition in DEFINITIONS.items():
            assert isinstance(definition.source, DerivativeSource)
            assert definition.source.group.definition == name


class TestDerivation:
    def test_a_committed_mesh_converges_to_its_derivatives(
        self, db_session: Session, stored
    ) -> None:
        artifact = stored("cube.stl", content.binary_stl())

        derivative_jobs.nudge_for(artifact)
        drain_work()

        assert _states(db_session, artifact.id) == {
            METADATA: DerivativeState.READY,
            THUMBNAIL: DerivativeState.READY,
        }
        job = _job(db_session, MESH_DEFINITION)
        assert job.state == JobState.COMPLETED
        assert json.loads(job.status_json)["result"] == {
            "derivatives": {METADATA: "ready", THUMBNAIL: "ready"}
        }

    def test_a_committed_gcode_converges_to_its_derivatives(
        self, db_session: Session, stored
    ) -> None:
        artifact = stored("plate.gcode", content.gcode(marker="converges"))

        derivative_jobs.nudge_for(artifact)
        drain_work()

        assert _states(db_session, artifact.id) == {
            METADATA: DerivativeState.READY,
            THUMBNAIL: DerivativeState.SKIPPED,
        }

    def test_a_converged_library_starts_no_more_jobs(
        self, db_session: Session, stored
    ) -> None:
        artifact = stored("cube.stl", content.binary_stl())
        derivative_jobs.nudge_for(artifact)
        drain_work()

        work.nudge(MESH_DEFINITION)
        drain_work()

        assert (
            len(db_session.exec(select(Job).where(Job.kind == MESH_DEFINITION)).all())
            == 1
        )

    def test_a_producer_crash_fails_what_it_had_in_flight(
        self, db_session: Session, stored, monkeypatch
    ) -> None:
        # The runner settles the Job as failed; the rows it began must not stay
        # "running" (the Model would show a spinner until the stale window).
        artifact = stored("cube.stl", content.binary_stl())

        def crash(*_args, **_kwargs):
            raise RuntimeError("renderer segfaulted")

        monkeypatch.setattr(producers.ThumbnailEngine, "generate", crash)

        work.nudge(MESH_DEFINITION)
        drain_work()

        assert _job(db_session, MESH_DEFINITION).state == JobState.FAILED
        assert set(_states(db_session, artifact.id).values()) == {
            DerivativeState.FAILED
        }


class TestNudgeFor:
    @pytest.mark.parametrize(
        ("filename", "definitions"),
        [
            ("part.stl", [MESH_DEFINITION]),
            ("plate.gcode", [GCODE_DEFINITION]),
            ("plate.bgcode", [GCODE_DEFINITION, TOOLPATH_DEFINITION]),
        ],
    )
    def test_nudges_every_group_that_applies(
        self, make_model, make_file, monkeypatch, filename: str, definitions
    ) -> None:
        nudged: list[str] = []
        monkeypatch.setattr(work, "nudge", lambda name, **_: nudged.append(name))

        derivative_jobs.nudge_for(make_file(make_model(), filename=filename))

        assert sorted(nudged) == sorted(definitions)


class TestCancel:
    def test_withdraws_every_kind_not_yet_finished(
        self, db_session: Session, make_model, make_file, make_derivative
    ) -> None:
        artifact = make_file(make_model(), filename="part.stl")
        make_derivative(artifact, METADATA)

        DEFINITIONS[MESH_DEFINITION].cancel(db_session, subject_key(artifact.id))
        db_session.commit()

        assert _states(db_session, artifact.id) == {
            METADATA: DerivativeState.READY,
            THUMBNAIL: DerivativeState.CANCELLED,
        }

    def test_a_cancelled_artifact_is_not_offered_again(
        self, db_session: Session, make_model, make_file
    ) -> None:
        artifact = make_file(make_model(), filename="part.stl")

        DEFINITIONS[MESH_DEFINITION].cancel(db_session, subject_key(artifact.id))
        db_session.commit()

        source = DEFINITIONS[MESH_DEFINITION].source
        assert source is not None
        assert source.pending(db_session, now=utcnow(), limit=10) == []

    def test_cancelling_a_trashed_artifact_does_nothing(
        self, db_session: Session, make_model, make_file
    ) -> None:
        artifact = make_file(make_model(), filename="part.stl", trashed=True)

        DEFINITIONS[MESH_DEFINITION].cancel(db_session, subject_key(artifact.id))
        db_session.commit()

        assert _states(db_session, artifact.id) == {}


class TestOnFailure:
    def test_fails_only_the_kinds_in_flight(
        self, db_session: Session, make_model, make_file, make_derivative
    ) -> None:
        artifact = make_file(make_model(), filename="part.stl")
        make_derivative(artifact, METADATA)
        make_derivative(artifact, THUMBNAIL, state=DerivativeState.RUNNING)

        DEFINITIONS[MESH_DEFINITION].on_failure(
            db_session, subject_key(artifact.id), "lost"
        )
        db_session.commit()

        assert _states(db_session, artifact.id) == {
            METADATA: DerivativeState.READY,
            THUMBNAIL: DerivativeState.FAILED,
        }


class TestRetry:
    def test_forgets_the_groups_unsuccessful_rows(
        self, db_session: Session, make_model, make_file, make_derivative
    ) -> None:
        artifact = make_file(make_model(), filename="plate.bgcode")
        make_derivative(artifact, METADATA)
        make_derivative(artifact, THUMBNAIL, state=DerivativeState.CANCELLED)
        make_derivative(
            artifact, TOOLPATH, state=DerivativeState.FAILED, exhausted=True
        )

        assert DEFINITIONS[GCODE_DEFINITION].retry(db_session, subject_key(artifact.id))
        db_session.commit()

        # The toolpath belongs to another group; its retry is its own.
        assert _states(db_session, artifact.id) == {
            METADATA: DerivativeState.READY,
            TOOLPATH: DerivativeState.FAILED,
        }

    def test_a_trashed_artifact_cannot_be_retried(
        self, db_session: Session, make_model, make_file
    ) -> None:
        artifact = make_file(make_model(), filename="part.stl", trashed=True)

        assert not DEFINITIONS[MESH_DEFINITION].retry(
            db_session, subject_key(artifact.id)
        )
