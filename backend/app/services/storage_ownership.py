"""Persist and consume exact, operation-proven storage ownership."""

from __future__ import annotations

from sqlmodel import Session, select

from app.core.logging import get_logger
from app.db.models import OwnedStorageObject
from app.services.storage_backend import CreationReceipt, StorageBackend

logger = get_logger(__name__)


class UnsafeStorageDeleteError(RuntimeError):
    """The exact target could not be positively and currently proven owned."""


def record_creation(
    session: Session, receipt: CreationReceipt, *, object_kind: str
) -> OwnedStorageObject:
    existing = session.exec(
        select(OwnedStorageObject).where(
            OwnedStorageObject.backend == receipt.backend,
            OwnedStorageObject.namespace == receipt.namespace,
            OwnedStorageObject.key == receipt.key,
        )
    ).first()
    if existing is not None:
        # Atomic create-only publication proved the prior object is absent.
        # Refresh the stale receipt instead of violating the locator uniqueness
        # constraint (e.g. repair after an out-of-band thumbnail loss).
        existing.object_kind = object_kind
        existing.token = receipt.token
        existing.size_bytes = receipt.size
        existing.etag = receipt.etag
        existing.version_id = receipt.version_id
        existing.device = receipt.device
        existing.inode = receipt.inode
        existing.ctime_ns = receipt.ctime_ns
        session.add(existing)
        return existing
    row = OwnedStorageObject(
        backend=receipt.backend,
        namespace=receipt.namespace,
        key=receipt.key,
        object_kind=object_kind,
        token=receipt.token,
        size_bytes=receipt.size,
        etag=receipt.etag,
        version_id=receipt.version_id,
        device=receipt.device,
        inode=receipt.inode,
        ctime_ns=receipt.ctime_ns,
    )
    session.add(row)
    return row


def _receipt(row: OwnedStorageObject) -> CreationReceipt:
    return CreationReceipt(
        key=row.key,
        size=row.size_bytes,
        token=row.token,
        backend=row.backend,
        namespace=row.namespace,
        etag=row.etag,
        version_id=row.version_id,
        device=row.device,
        inode=row.inode,
        ctime_ns=row.ctime_ns,
    )


def require_owned_key(session: Session, backend: StorageBackend, key: str) -> None:
    candidates = session.exec(
        select(OwnedStorageObject).where(OwnedStorageObject.key == key)
    ).all()
    if not candidates:
        raise UnsafeStorageDeleteError("storage_ownership_unverified")
    for row in candidates:
        try:
            if backend.creation_matches(_receipt(row)):
                return
        except Exception as exc:
            raise UnsafeStorageDeleteError("storage_verification_failed") from exc
    raise UnsafeStorageDeleteError("storage_object_no_longer_matches_receipt")


def replace_owned_bytes(
    session: Session,
    backend: StorageBackend,
    key: str,
    data: bytes,
    *,
    object_kind: str,
) -> CreationReceipt:
    candidates = session.exec(
        select(OwnedStorageObject).where(OwnedStorageObject.key == key)
    ).all()
    for row in candidates:
        current = _receipt(row)
        if not backend.creation_matches(current):
            continue
        replacement = backend.replace_bytes(data, current)
        row.backend = replacement.backend
        row.namespace = replacement.namespace
        row.token = replacement.token
        row.size_bytes = replacement.size
        row.etag = replacement.etag
        row.version_id = replacement.version_id
        row.device = replacement.device
        row.inode = replacement.inode
        row.ctime_ns = replacement.ctime_ns
        row.object_kind = object_kind
        session.add(row)
        return replacement
    raise UnsafeStorageDeleteError("storage_ownership_unverified")


def delete_owned_key(
    session: Session,
    backend: StorageBackend,
    key: str,
    *,
    required_proof: bool = False,
) -> bool:
    """Delete *key* only if a persisted creation receipt still matches it."""
    candidates = session.exec(
        select(OwnedStorageObject).where(OwnedStorageObject.key == key)
    ).all()
    for row in candidates:
        try:
            removed = backend.rollback_create(_receipt(row))
        except Exception as exc:
            logger.exception(
                "owned storage delete failed",
                extra={"key": key, "object_kind": row.object_kind},
            )
            if required_proof:
                raise UnsafeStorageDeleteError("storage_delete_failed") from exc
            return False
        if removed:
            session.delete(row)
            logger.info(
                "owned storage object deleted",
                extra={"key": key, "object_kind": row.object_kind},
            )
            return True
        if required_proof:
            raise UnsafeStorageDeleteError("storage_object_no_longer_matches_receipt")
        return False
    logger.warning(
        "storage delete skipped: no matching positive ownership receipt",
        extra={"key": key},
    )
    if required_proof:
        raise UnsafeStorageDeleteError("storage_ownership_unverified")
    return False
