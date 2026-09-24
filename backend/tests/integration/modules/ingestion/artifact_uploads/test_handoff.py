"""A verified resumable upload becomes an Artifact exactly through its Job.

Finalizing an upload records a queued ``ingest.artifact_upload`` Job that owns the
verified staged bytes; the Job commits them (a new Model's Artifact, or a revision
of an existing Model) and mirrors its outcome onto the upload session. If this goes
red, a verified upload can be left ``ingesting`` forever, a failed commit can report
success to the client, or the staged bytes can outlive the upload they belonged to.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.core.config import settings
from app.db.models import (
    ArtifactUploadSession,
    ArtifactUploadState,
    File,
    FileType,
    JobState,
    StagingLease,
    User,
)
from app.db.session import get_session_factory
from app.modules.ingestion import staging_leases
from app.modules.ingestion.artifact_uploads import handoff
from app.modules.work import service as work_service
from app.modules.work.jobs import jobs
from app.modules.work.submission import nudge
from app.runtime.engine.inline import InlineJobEngine
from tests._env import use_local_storage
from tests.factories import build_artifact_upload
from tests.factories.protocols import MakeJob, MakeModel, MakeUser

STL = b"solid handoff\nendsolid handoff\n"
GCODE = b"; handoff revision\nG28\n"


@pytest.fixture
def owner(make_user: MakeUser, tmp_path: Path) -> User:
    use_local_storage(tmp_path)
    return make_user("handoff-owner", superuser=True)


def _verified(
    session: Session,
    make_job: MakeJob,
    owner: User,
    *,
    content: bytes = STL,
    stage: bool = True,
    **upload: object,
) -> tuple[ArtifactUploadSession, str]:
    """An upload whose bytes verified and whose finalize queued the Job."""
    row = build_artifact_upload(
        session, owner, state=ArtifactUploadState.INGESTING, **upload
    )
    job = make_job(
        kind=handoff.DEFINITION,
        owner=owner,
        subject=handoff.subject_key(row.id),
    )
    row.job_id = job.id
    session.add(row)
    session.commit()
    if stage:
        staged = (
            settings.incoming_dir / "artifact-uploads" / row.id / "assembled.upload"
        )
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_bytes(content)
        staging_leases.create_job_lease(
            session,
            job_id=job.id,
            owner_user_id=owner.id,
            path=staged,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            check_capacity=False,
        )
        session.commit()
    return row, job.id


def _run(engine: InlineJobEngine, session: Session) -> None:
    nudge(handoff.DEFINITION)
    engine.drain()
    session.expire_all()


class TestRunVerifiedUploadIngestion:
    def test_commits_a_verified_model_upload_as_an_artifact(
        self,
        db_session: Session,
        make_job: MakeJob,
        owner: User,
        work_engine: InlineJobEngine,
    ) -> None:
        _verified(db_session, make_job, owner, filename="bracket.stl")

        _run(work_engine, db_session)

        artifact = db_session.exec(
            select(File).where(File.original_filename == "bracket.stl")
        ).one()
        assert artifact.sha256 == hashlib.sha256(STL).hexdigest()

    def test_completes_the_upload_session(
        self,
        db_session: Session,
        make_job: MakeJob,
        owner: User,
        work_engine: InlineJobEngine,
    ) -> None:
        upload, _job_id = _verified(db_session, make_job, owner)

        _run(work_engine, db_session)

        assert db_session.get(ArtifactUploadSession, upload.id).state == (
            ArtifactUploadState.COMPLETED
        )

    def test_releases_the_staged_bytes_of_a_completed_upload(
        self,
        db_session: Session,
        make_job: MakeJob,
        owner: User,
        work_engine: InlineJobEngine,
    ) -> None:
        _upload, job_id = _verified(db_session, make_job, owner)

        _run(work_engine, db_session)

        assert (
            db_session.exec(
                select(StagingLease).where(StagingLease.job_id == job_id)
            ).all()
            == []
        )

    def test_attaches_a_revision_upload_to_its_model(
        self,
        db_session: Session,
        make_job: MakeJob,
        make_model: MakeModel,
        owner: User,
        work_engine: InlineJobEngine,
    ) -> None:
        model = make_model("Revised")
        _verified(
            db_session,
            make_job,
            owner,
            content=GCODE,
            purpose="revision",
            target_role="revision",
            target_id=str(model.id),
            filename="revised.gcode",
            request_json='{"revision_label":"faster"}',
        )

        _run(work_engine, db_session)

        revision = db_session.exec(
            select(File).where(
                File.model_id == model.id, File.file_type == FileType.GCODE
            )
        ).one()
        assert revision.revision_label == "faster"

    def test_fails_a_revision_for_a_trashed_model(
        self,
        db_session: Session,
        make_job: MakeJob,
        make_model: MakeModel,
        owner: User,
        work_engine: InlineJobEngine,
    ) -> None:
        model = make_model("Gone", trashed=True)
        _upload, job_id = _verified(
            db_session,
            make_job,
            owner,
            content=GCODE,
            purpose="revision",
            target_role="revision",
            target_id=str(model.id),
            filename="revised.gcode",
        )

        _run(work_engine, db_session)

        status = jobs.get(job_id)
        assert status is not None
        assert (status.state, status.error) == (
            "failed",
            "artifact_revision_ingestion_failed",
        )

    def test_fails_an_unsupported_purpose_without_a_retry(
        self,
        db_session: Session,
        make_job: MakeJob,
        owner: User,
        work_engine: InlineJobEngine,
    ) -> None:
        upload, _job_id = _verified(
            db_session,
            make_job,
            owner,
            purpose="external_writeback",
            filename="notes.txt",
        )

        _run(work_engine, db_session)

        row = db_session.get(ArtifactUploadSession, upload.id)
        assert (row.state, row.retryable) == (ArtifactUploadState.FAILED, False)

    def test_fails_the_job_when_the_staged_bytes_expired(
        self,
        db_session: Session,
        make_job: MakeJob,
        owner: User,
        work_engine: InlineJobEngine,
    ) -> None:
        _upload, job_id = _verified(db_session, make_job, owner, stage=False)

        _run(work_engine, db_session)

        status = jobs.get(job_id)
        assert status is not None
        assert (status.state, status.error) == ("failed", "staging_expired")

    def test_ignores_a_session_that_no_longer_owns_ingestion(
        self, db_session: Session, make_job: MakeJob, owner: User
    ) -> None:
        upload, job_id = _verified(db_session, make_job, owner)
        upload.state = ArtifactUploadState.FAILED
        db_session.add(upload)
        db_session.commit()

        handoff.run_verified_upload_ingestion(
            upload_id=upload.id, job_id=job_id, session_factory=get_session_factory()
        )

        assert (
            db_session.exec(
                select(File).where(File.original_filename == upload.filename)
            ).all()
            == []
        )


class TestCancel:
    def test_cancelling_the_job_fails_the_upload(
        self, db_session: Session, make_job: MakeJob, owner: User
    ) -> None:
        upload, job_id = _verified(db_session, make_job, owner)

        work_service.cancel(job_id, actor=owner)

        db_session.expire_all()
        row = db_session.get(ArtifactUploadSession, upload.id)
        assert (row.state, row.error_code) == (
            ArtifactUploadState.FAILED,
            "artifact_upload_cancelled",
        )

    def test_cancelling_the_job_marks_it_cancelled(
        self, db_session: Session, make_job: MakeJob, owner: User
    ) -> None:
        _upload, job_id = _verified(db_session, make_job, owner)

        work_service.cancel(job_id, actor=owner)

        status = jobs.get(job_id)
        assert status is not None and status.state == JobState.CANCELLED.value
