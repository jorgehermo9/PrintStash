"""Ingest requests: the typed intent every ``ingest.*`` Job is rebuilt from.

A request is recorded together with its queued Job. Its credential is stored
encrypted and can be cleared once used. The review manifest it produces is
written back onto it, and a selection token only resolves for its owner (or
an admin), for the manifest kind it was issued for, and only until claimed.
Anything else reads as missing, never as forbidden.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session

from app.core.errors import OperationError
from app.db.models import IngestRequest, IngestRequestKind, Job, JobState
from app.modules.ingestion import requests


@pytest.fixture
def owner(make_user):
    return make_user()


def _create(session: Session, owner, **kwargs) -> IngestRequest:
    request = requests.create(
        session, kind=IngestRequestKind.URL, owner_user_id=owner.id, **kwargs
    )
    session.commit()
    return request


def _store(session: Session, job_id: str, kind: str, payload: dict) -> None:
    # The Job writes the manifest from its own session.
    requests.store_manifest(job_id, kind, payload)
    session.expire_all()


def _reread(session: Session, job_id: str) -> IngestRequest:
    session.expire_all()
    return requests.load(session, job_id)


class TestCreate:
    def test_records_the_request_with_its_queued_job(
        self, db_session: Session, owner
    ) -> None:
        request = _create(db_session, owner, selection={"files": [1]})

        job = db_session.get(Job, request.job_id)
        assert job is not None
        assert (job.kind, job.subject_key, job.state) == (
            "ingest.url",
            f"ingest_request/{request.job_id}",
            JobState.QUEUED,
        )
        assert requests.selection(request) == {"files": [1]}

    def test_stores_the_credential_encrypted(self, db_session: Session, owner) -> None:
        request = _create(db_session, owner, credential="s3cret-token")

        assert request.source_credential != "s3cret-token"
        assert requests.credential(request) == "s3cret-token"

    def test_a_cleared_credential_is_gone(self, db_session: Session, owner) -> None:
        request = _create(db_session, owner, credential="s3cret-token")

        requests.clear_credential(request.job_id)

        assert _reread(db_session, request.job_id).source_credential is None

    def test_clearing_a_request_that_is_gone_is_a_no_op(self) -> None:
        requests.clear_credential("missing")

    def test_a_selection_that_is_not_an_object_reads_as_empty(
        self, db_session: Session, owner
    ) -> None:
        request = _create(db_session, owner)
        request.selection_json = "[1, 2]"

        assert requests.selection(request) == {}

    def test_a_missing_request_cannot_be_loaded(self, db_session: Session) -> None:
        with pytest.raises(LookupError, match="ingest_request_missing"):
            requests.load(db_session, "missing")


class TestManifest:
    def test_the_owner_resolves_the_manifest_it_produced(
        self, db_session: Session, owner
    ) -> None:
        request = _create(db_session, owner)
        _store(db_session, request.job_id, "archive", {"entries": ["a.stl"]})

        _, manifest = requests.manifest_for(
            db_session, request.job_id, kind="archive", user=owner
        )

        assert manifest == {"kind": "archive", "entries": ["a.stl"]}

    def test_an_admin_resolves_another_users_manifest(
        self, db_session: Session, owner, make_user
    ) -> None:
        request = _create(db_session, owner)
        _store(db_session, request.job_id, "archive", {"entries": []})

        _, manifest = requests.manifest_for(
            db_session, request.job_id, kind="archive", user=make_user(superuser=True)
        )

        assert manifest["kind"] == "archive"

    def test_another_users_manifest_reads_as_missing(
        self, db_session: Session, owner, make_user
    ) -> None:
        request = _create(db_session, owner)
        _store(db_session, request.job_id, "archive", {"entries": []})

        with pytest.raises(OperationError) as refused:
            requests.manifest_for(
                db_session, request.job_id, kind="archive", user=make_user()
            )

        assert refused.value.code == "archive_not_found"

    def test_a_manifest_of_another_kind_reads_as_missing(
        self, db_session: Session, owner
    ) -> None:
        request = _create(db_session, owner)
        _store(db_session, request.job_id, "url", {"files": []})

        with pytest.raises(OperationError) as refused:
            requests.manifest_for(
                db_session, request.job_id, kind="archive", user=owner
            )

        assert refused.value.code == "archive_not_found"

    def test_a_claimed_manifest_cannot_be_used_again(
        self, db_session: Session, owner
    ) -> None:
        request = _create(db_session, owner)
        _store(db_session, request.job_id, "archive", {"claimed": True})

        with pytest.raises(OperationError) as refused:
            requests.manifest_for(
                db_session, request.job_id, kind="archive", user=owner
            )

        assert refused.value.code == "archive_already_claimed"

    def test_claiming_a_manifest_uses_it_up(self, db_session: Session, owner) -> None:
        request = _create(db_session, owner)
        _store(db_session, request.job_id, "archive", {"entries": []})
        claimed, _ = requests.manifest_for(
            db_session, request.job_id, kind="archive", user=owner
        )

        requests.claim_manifest(db_session, claimed)
        db_session.commit()

        with pytest.raises(OperationError) as refused:
            requests.manifest_for(
                db_session, request.job_id, kind="archive", user=owner
            )
        assert refused.value.code == "archive_already_claimed"
