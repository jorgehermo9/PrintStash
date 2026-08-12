"""Trash lifecycle for the library — the single owner of soft-delete semantics.

Soft-delete → restore → expiry → hard delete (rows + explicitly owned blobs)
all live here. Query-side filtering uses ``app.db.scopes.live/trashed``.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Iterable

from sqlmodel import Session, delete, select

from app.core.config import settings
from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import (
    Collection,
    Document,
    File,
    FileType,
    Metadata,
    Model,
    Printer,
    PrinterFile,
    PrintJob,
    Tag,
    User,
)
from app.db.scopes import live, trashed
from app.db.session import get_session_factory
from app.services.storage_backend import get_backend
from app.services.storage_ownership import (
    UnsafeStorageDeleteError,
    delete_owned_key,
    require_owned_key,
)

logger = get_logger(__name__)
_DOCUMENT_IMAGE_RE = re.compile(
    r"/api/v1/documents/(\d+)/images/([0-9a-f]{64}\.(?:png|jpe?g|gif|webp))"
)
_COLLECTION_IMAGE_RE = re.compile(
    r"/api/v1/collections/(\d+)/images/([0-9a-f]{64}\.(?:png|jpe?g|gif|webp))"
)


def _preflight_primary_keys(
    session: Session, keys: Iterable[str]
) -> None:
    backend = get_backend()
    exact_keys = list(dict.fromkeys(keys))
    if not exact_keys:
        return
    # Abort read-only/permission failures before deleting the first byte.
    try:
        backend.verify_destructive_access(exact_keys)
    except Exception as exc:
        raise UnsafeStorageDeleteError("storage_delete_access_unverified") from exc
    for key in exact_keys:
        require_owned_key(session, backend, key)


def trash_expires_at(
    deleted_at: datetime | None, retention_days: int
) -> datetime | None:
    if deleted_at is None or retention_days < 0:
        return None
    return deleted_at + timedelta(days=retention_days)


def soft_delete_model(session: Session, model: Model) -> None:
    """Move a model to the trash."""
    model.deleted_at = utcnow()
    model.updated_at = utcnow()
    session.add(model)
    session.commit()


def soft_delete_models(session: Session, models: Iterable[Model]) -> None:
    """Move several models to the trash without committing.

    Caller is responsible for the single ``session.commit()`` so a batch is
    persisted atomically.
    """
    now = utcnow()
    for model in models:
        model.deleted_at = now
        model.updated_at = now
        session.add(model)


def restore_model(session: Session, model: Model) -> None:
    """Bring a model back from the trash. No-op when it is live."""
    if model.deleted_at is None:
        return
    model.deleted_at = None
    model.deleted_by = None
    model.updated_at = utcnow()
    session.add(model)
    session.commit()


def hard_delete_file(
    session: Session,
    file_row: File,
    *,
    maintain_revision_invariant: bool = True,
    ownership_preflighted: bool = False,
) -> None:
    """Permanently remove one Artifact and every vault-owned dependent.

    Linked external bytes belong to the user and are never deleted. The caller
    owns the surrounding transaction and commit.
    """
    if file_row.id is None:
        return

    backend = get_backend()
    file_id = int(file_row.id)
    if not file_row.is_external:
        if not ownership_preflighted:
            _preflight_primary_keys(session, [file_row.path])
        # Once a multi-key purge starts, a late storage failure must leak the
        # uncertain remainder rather than roll back DB rows after earlier exact
        # objects were already removed.
        delete_owned_key(session, backend, file_row.path)
    delete_owned_key(session, backend, backend.thumbnail_key(file_id))
    delete_owned_key(session, backend, backend.legacy_thumbnail_key(file_id))
    shared_cache_owner = session.exec(
        select(File.id).where(
            File.id != file_id,
            File.sha256 == file_row.sha256,
        )
    ).first()
    if shared_cache_owner is None and file_row.sha256:
        delete_owned_key(session, backend, backend.stl_cache_key(file_row.sha256))

    model = session.get(Model, file_row.model_id)
    if model is not None and model.thumbnail_file_id == file_id:
        model.thumbnail_file_id = None
        model.thumbnail_path = None
        model.updated_at = utcnow()
        session.add(model)

    was_live_recommended = (
        maintain_revision_invariant
        and file_row.file_type == FileType.GCODE
        and file_row.deleted_at is None
        and file_row.is_recommended
    )
    if was_live_recommended:
        file_row.is_recommended = False
        session.add(file_row)
        session.flush()
        replacement = session.exec(
            select(File)
            .where(
                File.model_id == file_row.model_id,
                File.id != file_id,
                File.file_type == FileType.GCODE,
                live(File),
            )
            .order_by(File.version.desc())  # type: ignore[attr-defined]
        ).first()
        if replacement is not None:
            replacement.is_recommended = True
            session.add(replacement)

    session.exec(delete(PrinterFile).where(PrinterFile.file_id == file_id))
    session.exec(delete(PrintJob).where(PrintJob.file_id == file_id))
    session.exec(delete(Metadata).where(Metadata.file_id == file_id))
    session.delete(file_row)


def hard_delete_document(
    session: Session, document: Document, *, ownership_preflighted: bool = False
) -> None:
    """Permanently remove a Document row and every vault-owned blob."""
    if document.id is None:
        return
    backend = get_backend()
    if document.filename:
        document_key = backend.document_file_key(document.id, document.filename)
        if not ownership_preflighted:
            _preflight_primary_keys(session, [document_key])
        delete_owned_key(session, backend, document_key)
    for document_id, name in _DOCUMENT_IMAGE_RE.findall(document.body or ""):
        if int(document_id) == document.id:
            delete_owned_key(
                session, backend, backend.document_image_key(document.id, name)
            )
    session.delete(document)


def restore_document(session: Session, document: Document) -> None:
    document.deleted_at = None
    document.deleted_by = None
    document.updated_at = utcnow()
    session.add(document)


def hard_delete_collection(session: Session, collection: Collection) -> None:
    """Permanently remove a Collection and its explicitly referenced images."""
    if collection.id is None:
        return
    backend = get_backend()
    for collection_id, name in _COLLECTION_IMAGE_RE.findall(collection.readme or ""):
        if int(collection_id) == collection.id:
            delete_owned_key(
                session, backend, backend.collection_image_key(collection.id, name)
            )
    session.delete(collection)


def hard_delete_model(
    session: Session, model: Model, *, ownership_preflighted: bool = False
) -> None:
    """Permanently remove a model, related DB rows, and stored blobs."""
    if model.id is None:
        return

    file_rows = session.exec(select(File).where(File.model_id == model.id)).all()
    # Verify every required primary before deleting the first byte. This avoids
    # a mixed legacy/missing model producing a partially applied hard delete.
    if not ownership_preflighted:
        _preflight_primary_keys(
            session,
            [file_row.path for file_row in file_rows if not file_row.is_external],
        )
    model.thumbnail_file_id = None
    model.thumbnail_path = None
    session.add(model)
    session.flush()
    for file_row in file_rows:
        hard_delete_file(
            session,
            file_row,
            maintain_revision_invariant=False,
            ownership_preflighted=True,
        )
    session.flush()

    # Don't bulk-delete the tag links here: ``Model.tags`` is a link_model
    # (many-to-many) relationship, so deleting the model already removes its
    # ModelTagLink rows. Doing both makes the ORM's cascade try to delete rows
    # this manual DELETE already removed -> StaleDataError on commit (purging any
    # *tagged* model, including the expired-trash cron, would 500).
    session.delete(model)


def hard_delete_expired_models(session: Session, retention_days: int) -> list[int]:
    if retention_days < 0:
        return []

    cutoff = utcnow() - timedelta(days=retention_days)
    models = session.exec(
        select(Model).where(
            trashed(Model),
            Model.deleted_at <= cutoff,  # type: ignore[operator]
        )
    ).all()
    model_ids = [int(model.id) for model in models if model.id is not None]
    if model_ids:
        file_rows = session.exec(
            select(File).where(File.model_id.in_(model_ids))  # type: ignore[attr-defined]
        ).all()
        # Preflight the entire batch before deleting the first object. One
        # legacy or remounted item must preserve every model in this purge.
        _preflight_primary_keys(
            session,
            [file_row.path for file_row in file_rows if not file_row.is_external],
        )
    purged_ids = [model.id for model in models if model.id is not None]
    for model in models:
        hard_delete_model(session, model, ownership_preflighted=True)
    return [int(model_id) for model_id in purged_ids]


def _cleanup_orphan_blobs(session: Session) -> int:
    """Never infer ownership by walking configured storage.

    A local ``data_dir`` can be a mistakenly mounted user library, and absence
    from the database is not proof that PrintStash created a file.  Destructive
    cleanup is therefore limited to exact keys held by rows being hard-deleted
    above.  Failed writes clean up their own exact destinations at the write
    site.  Keep this compatibility seam (and the result field) as a no-op so
    older callers cannot accidentally reintroduce discovery-based deletion.
    """
    del session
    return 0


def gc_soft_deleted(retention_days: int | None = None) -> dict[str, int]:
    """Hourly GC: purge expired trash rows and their exact owned blob keys.

    No-ops while a backup restore is in progress — restore replaces the DB
    file and disposes the engine, so a GC pass racing it would run queries
    against a database that no longer matches its connection.
    """
    from app.services.backup import restore_in_progress

    if restore_in_progress():
        logger.info("gc skipped: backup restore in progress")
        return {"rows": 0, "orphan_blobs": 0}
    effective_retention = (
        int(settings.trash_retention_days) if retention_days is None else retention_days
    )
    if effective_retention < 0:
        logger.info("gc skipped: trash retention is disabled")
        return {"rows": 0, "orphan_blobs": 0}
    cutoff = utcnow() - timedelta(days=effective_retention)
    purged = {"rows": 0, "orphan_blobs": 0}
    with get_session_factory().scoped_session() as session:
        expired_models = session.exec(
            select(Model).where(
                trashed(Model),
                Model.deleted_at <= cutoff,  # type: ignore[operator]
            )
        ).all()
        expired_documents = session.exec(
            select(Document).where(
                trashed(Document),
                Document.deleted_at < cutoff,  # type: ignore[operator]
            )
        ).all()
        expired_files = session.exec(
            select(File).where(
                trashed(File),
                File.deleted_at < cutoff,  # type: ignore[operator]
            )
        ).all()

        expired_model_ids = {
            int(model.id) for model in expired_models if model.id is not None
        }
        model_files = (
            session.exec(
                select(File).where(  # type: ignore[attr-defined]
                    File.model_id.in_(expired_model_ids)
                )
            ).all()
            if expired_model_ids
            else []
        )
        standalone_expired_files = [
            file_row
            for file_row in expired_files
            if file_row.model_id not in expired_model_ids
        ]

        # Preflight every required primary in the whole maintenance pass.
        # This runs before any byte deletion or row mutation so one uncertain
        # target cannot leave a partially purged batch.
        backend = get_backend()
        primary_keys = [
            file_row.path
            for file_row in [*model_files, *standalone_expired_files]
            if not file_row.is_external
        ]
        primary_keys.extend(
            backend.document_file_key(document.id, document.filename)
            for document in expired_documents
            if document.id is not None and document.filename
        )
        _preflight_primary_keys(session, primary_keys)

        for model in expired_models:
            hard_delete_model(session, model, ownership_preflighted=True)
        purged["rows"] += len(expired_models)
        for document in expired_documents:
            hard_delete_document(session, document, ownership_preflighted=True)
        purged["rows"] += len(expired_documents)
        for file_row in standalone_expired_files:
            hard_delete_file(session, file_row, ownership_preflighted=True)
        purged["rows"] += len(standalone_expired_files)
        expired_collections = session.exec(
            select(Collection).where(
                trashed(Collection),
                Collection.deleted_at < cutoff,  # type: ignore[operator]
            )
        ).all()
        for collection in expired_collections:
            hard_delete_collection(session, collection)
        purged["rows"] += len(expired_collections)
        for model in (Tag, Printer, User):
            result = session.exec(
                delete(model).where(
                    trashed(model),
                    model.deleted_at < cutoff,  # type: ignore[attr-defined]
                )
            )
            purged["rows"] += int(result.rowcount or 0)
        session.commit()
        purged["orphan_blobs"] = _cleanup_orphan_blobs(session)
    logger.info(
        "gc complete: rows=%s orphan_blobs=%s", purged["rows"], purged["orphan_blobs"]
    )
    return purged
