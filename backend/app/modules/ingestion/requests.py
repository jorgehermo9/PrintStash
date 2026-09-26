"""Ingest requests: the typed intent behind every ``ingest.*`` Job.

A route records an ``IngestRequest`` together with its queued Job (and, for
staged bytes, a staging lease owned by that Job) in one transaction, then
nudges. Each attempt of the Job rebuilds its work from these columns. A review
manifest the request produces (archive entries, model-page files, collection
members) is written back onto it, so a later selection can be served by any
process and survives a restart.
"""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session

from app.core.errors import ErrorKind, OperationError
from app.core.secrets import decrypt_secret, encrypt_secret
from app.db.models import IngestRequest, IngestRequestKind, JobKind, User, WorkPriority
from app.db.session import get_session_factory
from app.modules.work import service as work_service

# The Job Definition that carries out each kind of request; every kind has one.
DEFINITIONS: dict[IngestRequestKind, JobKind] = {
    IngestRequestKind.UPLOAD: JobKind.INGESTION_UPLOAD,
    IngestRequestKind.URL: JobKind.INGESTION_URL,
    IngestRequestKind.ARCHIVE_INSPECT: JobKind.INGESTION_ARCHIVE_INSPECT,
    IngestRequestKind.ARCHIVE_SELECTION: JobKind.INGESTION_ARCHIVE_SELECTION,
    IngestRequestKind.URL_SELECTION: JobKind.INGESTION_URL_SELECTION,
    IngestRequestKind.COLLECTION: JobKind.INGESTION_COLLECTION,
}


def subject_key(job_id: str) -> str:
    return f"ingest_request/{job_id}"


def create(
    session: Session,
    *,
    kind: IngestRequestKind,
    owner_user_id: int,
    selection: dict[str, Any] | None = None,
    credential: str | None = None,
    **columns: Any,
) -> IngestRequest:
    """Record a request and its queued Job in ``session``; the caller commits."""
    import uuid

    job_id = uuid.uuid4().hex
    work_service.request(
        session,
        definition=DEFINITIONS[kind],
        subject_key=subject_key(job_id),
        owner_user_id=owner_user_id,
        priority=WorkPriority.INTERACTIVE,
        job_id=job_id,
    )
    request = IngestRequest(
        job_id=job_id,
        kind=kind,
        owner_user_id=owner_user_id,
        selection_json=json.dumps(selection or {}, separators=(",", ":")),
        source_credential=encrypt_secret(credential) if credential else None,
        **columns,
    )
    session.add(request)
    session.flush()
    return request


def load(session: Session, job_id: str) -> IngestRequest:
    request = session.get(IngestRequest, job_id)
    if request is None:
        raise LookupError(f"ingest_request_missing:{job_id}")
    return request


def selection(request: IngestRequest) -> dict[str, Any]:
    value = json.loads(request.selection_json or "{}")
    return value if isinstance(value, dict) else {}


def credential(request: IngestRequest) -> str | None:
    return decrypt_secret(request.source_credential)


def clear_credential(job_id: str) -> None:
    with get_session_factory().scoped_session() as session:
        request = session.get(IngestRequest, job_id)
        if request is not None and request.source_credential is not None:
            request.source_credential = None
            session.add(request)
            session.commit()


def store_manifest(job_id: str, kind: str, payload: dict[str, Any]) -> None:
    """Record the review manifest this request produced."""
    with get_session_factory().scoped_session() as session:
        request = load(session, job_id)
        request.manifest_json = json.dumps(
            {"kind": kind, **payload}, separators=(",", ":"), default=str
        )
        session.add(request)
        session.commit()


def manifest_for(
    session: Session, token: str, *, kind: str, user: User
) -> tuple[IngestRequest, dict[str, Any]]:
    """The manifest behind a selection token, if ``user`` may use it.

    The token is the id of the Job that produced the manifest. Another user's
    manifest is reported as missing, never as forbidden.
    """
    request = session.get(IngestRequest, token)
    if request is None or (request.owner_user_id != user.id and not user.is_superuser):
        raise OperationError(f"{kind}_not_found", kind=ErrorKind.NOT_FOUND)
    manifest = json.loads(request.manifest_json or "{}")
    if not isinstance(manifest, dict) or manifest.get("kind") != kind:
        raise OperationError(f"{kind}_not_found", kind=ErrorKind.NOT_FOUND)
    if manifest.get("claimed"):
        raise OperationError(f"{kind}_already_claimed", kind=ErrorKind.CONFLICT)
    return request, manifest


def claim_manifest(session: Session, request: IngestRequest) -> None:
    """Mark a manifest used, in the caller's transaction, so it selects once."""
    manifest = json.loads(request.manifest_json or "{}")
    manifest["claimed"] = True
    request.manifest_json = json.dumps(manifest, separators=(",", ":"))
    session.add(request)
