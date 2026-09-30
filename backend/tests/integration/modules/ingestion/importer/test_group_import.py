"""Job-state coverage for ``import_resolved_groups`` (collection fan-out).

Integration rather than unit, despite what it looks like: the Job is a ``jobs``
row, and that row has a foreign key to `users`. It lived under `tests/unit/`
while foreign keys were unenforced and an owner id of `1` could refer to nobody;
the tier guard rejected it the moment enforcement came back on.

The regression these guard: a collection where every member fails to download
must report the job as ``failed`` (not ``completed``), so the UI stops showing a
silently-broken import as success.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session

from app.db.models import JobKind, User
from app.modules.ingestion import importer
from app.modules.ingestion.importer import ResolvedGroup
from app.modules.work.jobs import jobs
from tests.factories import build_job, build_user


def _run(session: Session, owner: User, groups: list[ResolvedGroup]) -> object:
    """Import *groups* as *owner* and return the resulting job status.

    The owner is passed in rather than hardcoded because `jobs.owner_user_id` is
    a foreign key: an id that merely happens to be free is refused, here and in
    production.
    """
    job = build_job(session, kind=JobKind.INGESTION_COLLECTION, owner=owner)
    importer.import_resolved_groups(
        job_id=job.id,
        groups=groups,
        collection="Test",
        tags=None,
        actor_user_id=owner.id,
        session_factory=lambda: None,  # never used: no group has staged files
    )
    return jobs.get(job.id)


@pytest.fixture
def owner(db_session: Session) -> User:
    """The user these import jobs belong to."""
    return build_user(db_session, "importer-owner")


class TestRunGroupImport:
    def test_all_members_failing_marks_job_failed(
        self, db_session: Session, owner: User
    ) -> None:
        job = _run(
            db_session,
            owner,
            [
                ResolvedGroup(
                    source_url="u1", title="A", error="makerworld_login_required"
                ),
                ResolvedGroup(
                    source_url="u2", title="B", error="makerworld_login_required"
                ),
            ],
        )
        assert job is not None
        assert job.state == "failed"
        # Members agree on one error -> surface it (UI shows the login message).
        assert job.error == "makerworld_login_required"
        assert job.result["imported"] == 0

    def test_mixed_member_errors_use_generic_code(
        self, db_session: Session, owner: User
    ) -> None:
        job = _run(
            db_session,
            owner,
            [
                ResolvedGroup(
                    source_url="u1", title="A", error="makerworld_login_required"
                ),
                ResolvedGroup(source_url="u2", title="B", error="no_importable_files"),
            ],
        )
        assert job is not None
        assert job.state == "failed"
        assert job.error == "collection_import_failed"

    def test_empty_group_without_error_still_fails(
        self, db_session: Session, owner: User
    ) -> None:
        job = _run(db_session, owner, [ResolvedGroup(source_url="u1", title="A")])
        assert job is not None
        assert job.state == "failed"
        # No explicit member error falls back to the per-member default, which is the
        # single distinct code here, so it surfaces rather than the generic one.
        assert job.error == "no_importable_files"
