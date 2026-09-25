"""The ``inference.model_download`` Job: an administrator installs a local model.

Acquisition is explicit and one at a time: a download is requested by an
administrator, runs as a Job (so it survives the request, reports progress and
is cancelled like any other Job), and rechecks that it is still allowed as it
goes. The model lands in the model cache every process reads.
"""

from __future__ import annotations

import importlib.util
import time

from sqlmodel import Session

from app.core.errors import ErrorKind, OperationError
from app.db.models import Job, JobKind, LaneName, User
from app.db.session import get_session_factory
from app.modules.work.contracts import JobContext, JobDefinition, JobOutcome, Step

# Every download shares one subject, so the active-subject index makes "one at
# a time" a database guarantee rather than a check two requests can both pass.
# The model a Job installs is its result's ``model_id``.
DOWNLOAD_SUBJECT = "model/download"
_RUNTIME_MODULES = ("onnxruntime", "onnx", "tokenizers")


def _allowed(session: Session, actor_id: int | None) -> bool:
    from app.modules.search.configuration import settings

    flags = settings(session)
    actor = session.get(User, actor_id) if actor_id is not None else None
    return bool(
        actor
        and actor.is_active
        and actor.is_superuser
        and flags.enabled
        and flags.local_models_enabled
        and flags.download_enabled
    )


def request(session: Session, actor: User, key: str) -> str:
    """Queue a download of curated model ``key``; returns its Job id.

    The caller commits and nudges. Refused while another download is active.
    """
    from app.modules.inference.model_registry import require
    from app.modules.search.configuration import settings
    from app.modules.work import service as work_service
    from app.modules.work.jobs import ActiveJobExists

    flags = settings(session)
    if not actor.is_superuser or not actor.is_active:
        raise OperationError("admin_required", kind=ErrorKind.FORBIDDEN)
    if (
        not flags.enabled
        or not flags.local_models_enabled
        or not flags.download_enabled
    ):
        raise OperationError("embedding_download_disabled", kind=ErrorKind.CONFLICT)
    entry = require(key)
    if not all(importlib.util.find_spec(name) for name in _RUNTIME_MODULES):
        raise OperationError("embedding_runtime_unavailable", kind=ErrorKind.CONFLICT)
    try:
        return work_service.request(
            session,
            definition=JobKind.INFERENCE_MODEL_DOWNLOAD,
            subject_key=DOWNLOAD_SUBJECT,
            owner_user_id=actor.id,
            status={"result": {"model_id": entry.id}},
        )
    except ActiveJobExists:
        raise OperationError(
            "embedding_download_busy", kind=ErrorKind.CONFLICT
        ) from None


def _model_of(job_id: str) -> str:
    """The model a download Job was requested for; ``request`` always records it."""
    from app.modules.work.jobs import jobs

    status = jobs.get(job_id)
    if status is None or status.result is None:
        raise RuntimeError(f"download_job_without_model:{job_id}")
    model = status.result.get("model_id")
    if not isinstance(model, str):
        raise RuntimeError(f"download_job_without_model:{job_id}")
    return model


def _download(ctx: JobContext) -> None:
    from printstash_core.inference import EmbeddingError

    from app.modules.inference.model_acquisition import Acquisition
    from app.modules.inference.model_registry import require

    identity = _model_of(ctx.job_id)
    entry = require(identity)
    sessions = get_session_factory()
    with sessions.scoped_session() as session:
        row = session.get(Job, ctx.job_id)
        actor_id = row.owner_user_id if row is not None else None
    checked, allowed, received, reported = 0.0, False, 0, 0.0

    def enabled() -> bool:
        nonlocal checked, allowed
        if time.monotonic() - checked > 0.25:
            checked = time.monotonic()
            with sessions.scoped_session() as session:
                allowed = _allowed(session, actor_id)
        return allowed

    def progress(size: int) -> None:
        nonlocal received, reported
        received += size
        if time.monotonic() - reported > 0.5:
            reported = time.monotonic()
            ctx.update(
                processed=received,
                total=entry.size,
                progress=min(99, 100 * received / entry.size),
            )

    ctx.update(
        label="Downloading model", total=entry.size, result={"model_id": identity}
    )
    try:
        Acquisition(sessions).install(
            identity, enabled=enabled, cancelled=ctx.cancelled, progress=progress
        )
    except Exception as exc:  # noqa: BLE001 - the Job records a safe code
        if ctx.cancelled():
            # Cancelled through the Jobs API: that is the outcome, not a failure.
            return
        code = (
            exc.code if isinstance(exc, EmbeddingError) else "embedding_download_failed"
        )
        ctx.finish(
            JobOutcome.FAILED,
            error=code,
            retryable=True,
            result={"model_id": identity, "error_code": code},
        )
        return
    ctx.update(progress=100, processed=entry.size, result={"model_id": identity})


def definitions() -> list[JobDefinition]:
    return [
        JobDefinition(
            name=JobKind.INFERENCE_MODEL_DOWNLOAD,
            lane=LaneName.NETWORK,
            steps=(
                Step(f"{JobKind.INFERENCE_MODEL_DOWNLOAD.value}.install", _download),
            ),
            # A new download is a new request: it re-checks consent and space.
            retry=lambda _session, _subject: False,
            # It writes only the model cache, which restore does not govern.
            mutating=False,
            label="Model downloads",
        )
    ]
