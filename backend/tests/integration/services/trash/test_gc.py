"""GC deletes exact row-owned keys and never infers ownership from directory walks.

Regression pack for discovery-based deletion: a configured ``data_dir`` may be
a mistakenly mounted user library, so an unclaimed path is never enough proof
that PrintStash may delete it.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.core.config import _overlay
from app.core.time import utcnow
from app.db.models import (
    Collection,
    Document,
    DocumentKind,
    File,
    FileType,
    Model,
    ShareLink,
    StorageDeleteIntent,
    VaultAuditFinding,
    VaultAuditFindingState,
    VaultAuditMode,
    VaultAuditRun,
    VaultAuditRunState,
    VaultAuditSeverity,
)
from app.services.trash import _cleanup_orphan_blobs, gc_soft_deleted
from tests.factories import build_model, build_stored_file, store_owned_bytes


def _write(key: str, data: bytes = b"x") -> str:
    p = Path(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return key


def _binary_document(session: Session, storage, name: str = "manual.pdf") -> Document:
    doc = Document(name=name, kind=DocumentKind.PDF)
    session.add(doc)
    session.commit()
    session.refresh(doc)
    doc.filename = name
    doc.size_bytes = 1
    session.add(doc)
    session.commit()
    store_owned_bytes(session, storage, storage.document_file_key(doc.id, name))
    return doc


def _open_namespace_escape(session: Session) -> None:
    run = VaultAuditRun(
        mode=VaultAuditMode.QUICK, state=VaultAuditRunState.COMPLETED, requested_by=1
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    session.add(
        VaultAuditFinding(
            run_id=run.id,
            code="managed_storage_namespace_escape",
            severity=VaultAuditSeverity.CRITICAL,
            resource_type="storage",
            resource_identifier="vault",
            state=VaultAuditFindingState.OPEN,
        )
    )
    session.commit()


def test_gc_preserves_document_blobs(db_session: Session, storage) -> None:
    doc = _binary_document(db_session, storage)
    key = storage.document_file_key(doc.id, doc.filename)

    _cleanup_orphan_blobs(db_session)

    assert Path(key).exists(), "GC deleted a live document's blob"


def test_gc_preserves_model_blobs(db_session: Session, storage) -> None:
    f = build_stored_file(
        db_session,
        storage,
        build_model(db_session, "widget", slug="widget"),
        filename="widget.stl",
    )

    _cleanup_orphan_blobs(db_session)

    assert Path(f.path).exists()


def test_gc_preserves_file_derivatives_while_artifact_exists(
    db_session: Session, storage
) -> None:
    artifact = build_stored_file(
        db_session,
        storage,
        build_model(db_session, "derived-live", slug="derived-live"),
        filename="derived-live.stl",
    )
    keys = (
        storage.thumbnail_key(artifact.id),
        storage.legacy_thumbnail_key(artifact.id),
        storage.stl_cache_key(artifact.sha256),
    )
    for key in keys:
        _write(key)

    _cleanup_orphan_blobs(db_session)

    assert all(Path(key).exists() for key in keys)


def test_gc_preserves_trashed_model_blobs(db_session: Session, storage) -> None:
    """A trashed model's bytes must survive until hard delete — restore needs them."""
    f = build_stored_file(
        db_session,
        storage,
        build_model(db_session, "trashed", slug="trashed"),
        filename="trashed.stl",
    )
    f.deleted_at = utcnow()
    db_session.add(f)
    db_session.commit()

    _cleanup_orphan_blobs(db_session)

    assert Path(f.path).exists()


def test_gc_preserves_document_blobs_when_models_exist(
    db_session: Session, storage
) -> None:
    """The two censuses must union, not shadow each other."""
    f = build_stored_file(
        db_session,
        storage,
        build_model(db_session, "widget", slug="widget"),
        filename="widget.stl",
    )
    doc = _binary_document(db_session, storage)

    _cleanup_orphan_blobs(db_session)

    assert Path(f.path).exists()
    assert Path(storage.document_file_key(doc.id, doc.filename)).exists()


def test_gc_preserves_unclaimed_files_in_local_storage(
    db_session: Session, storage
) -> None:
    """A configured local root may already contain user-managed files.

    Absence from PrintStash's database is not proof that PrintStash owns a file,
    even when its path resembles the vault layout.  Scheduled maintenance must
    therefore never discover and delete local files by walking ``data_dir``.
    """
    existing_library_file = _write(
        str(
            Path(storage.blob_key("gone", 1, "gone.stl")).parents[2]
            / "My Library"
            / "part.stl"
        )
    )
    printstash_shaped_file = _write(storage.blob_key("gone", 1, "gone.stl"))
    document_shaped_file = _write(storage.document_file_key(999, "gone.pdf"))

    removed = _cleanup_orphan_blobs(db_session)

    assert Path(existing_library_file).exists()
    assert Path(printstash_shaped_file).exists()
    assert Path(document_shaped_file).exists()
    assert removed == 0


def test_gc_does_not_guess_ownership_after_document_row_is_missing(
    db_session: Session, storage
) -> None:
    doc = _binary_document(db_session, storage)
    key = storage.document_file_key(doc.id, doc.filename)
    db_session.delete(doc)
    db_session.commit()

    _cleanup_orphan_blobs(db_session)

    assert Path(key).exists()


def test_gc_ignores_markdown_documents(db_session: Session, storage) -> None:
    """Markdown docs own no blob — they must not contribute a bogus key."""
    doc = Document(name="notes", kind=DocumentKind.MARKDOWN, body="# hi")
    db_session.add(doc)
    db_session.commit()

    assert _cleanup_orphan_blobs(db_session) == 0


def test_gc_hard_deletes_expired_document_and_its_blob(
    db_session: Session, storage
) -> None:
    doc = _binary_document(db_session, storage)
    document_id = doc.id
    key = storage.document_file_key(doc.id, doc.filename)
    doc.deleted_at = utcnow()
    db_session.add(doc)
    db_session.commit()

    result = gc_soft_deleted(retention_days=0)

    assert result["rows"] >= 1
    assert not Path(key).exists()
    db_session.expire_all()
    assert db_session.get(Document, document_id) is None


def test_gc_hard_deletes_expired_artifact_and_its_derivatives(
    db_session: Session, storage
) -> None:
    artifact = build_stored_file(
        db_session,
        storage,
        build_model(db_session, "derived-expired", slug="derived-expired"),
        filename="derived-expired.stl",
    )
    artifact_id = artifact.id
    keys = (
        artifact.path,
        storage.thumbnail_key(artifact.id),
        storage.legacy_thumbnail_key(artifact.id),
        storage.stl_cache_key(artifact.sha256),
    )
    for key in keys[1:]:
        store_owned_bytes(db_session, storage, key)
    artifact.deleted_at = utcnow() - timedelta(days=1)
    db_session.add(artifact)
    db_session.commit()

    gc_soft_deleted(retention_days=0)

    assert all(not Path(key).exists() for key in keys)
    db_session.expire_all()
    assert db_session.get(File, artifact_id) is None


def test_negative_retention_disables_gc_and_preserves_owned_bytes(
    db_session: Session, storage
) -> None:
    artifact = build_stored_file(
        db_session,
        storage,
        build_model(db_session, "retention-disabled", slug="retention-disabled"),
        filename="retention-disabled.stl",
    )
    artifact.deleted_at = utcnow() - timedelta(days=365)
    db_session.add(artifact)
    db_session.commit()

    result = gc_soft_deleted(retention_days=-1)

    assert result == {"rows": 0, "orphan_blobs": 0}
    assert Path(artifact.path).exists()
    db_session.expire_all()
    assert db_session.get(File, artifact.id) is not None


def test_hard_delete_aborts_when_owned_storage_is_suddenly_unmounted(
    db_session: Session, storage, tmp_path: Path
) -> None:
    artifact = build_stored_file(
        db_session,
        storage,
        build_model(db_session, "unmounted", slug="unmounted"),
        filename="unmounted.stl",
    )
    artifact.deleted_at = utcnow() - timedelta(days=1)
    db_session.add(artifact)
    db_session.commit()
    mounted_root = Path(_overlay["data_dir"])
    detached_root = tmp_path / "detached-files"
    mounted_root.rename(detached_root)
    mounted_root.mkdir()

    result = gc_soft_deleted(retention_days=0)

    assert result["resources_blocked"] == 1
    db_session.expire_all()
    assert db_session.get(File, artifact.id) is not None
    assert (detached_root / "unmounted" / "v1" / "unmounted.stl").exists()


def test_hard_delete_aborts_on_read_only_storage_and_preserves_row(
    db_session: Session,
    storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = build_stored_file(
        db_session,
        storage,
        build_model(db_session, "readonly", slug="readonly"),
        filename="readonly.stl",
    )
    artifact.deleted_at = utcnow() - timedelta(days=1)
    db_session.add(artifact)
    db_session.commit()
    import tempfile

    original_mkstemp = tempfile.mkstemp

    def denied(*args, **kwargs):
        if Path(kwargs.get("dir", "")) == Path(artifact.path).parent:
            raise PermissionError("read-only mount")
        return original_mkstemp(*args, **kwargs)

    monkeypatch.setattr("app.services.storage_backend.tempfile.mkstemp", denied)
    result = gc_soft_deleted(retention_days=0)

    assert result["resources_blocked"] == 1
    db_session.expire_all()
    assert db_session.get(File, artifact.id) is not None
    assert Path(artifact.path).exists()


def test_gc_skips_legacy_candidate_without_blocking_verifiable_candidates(
    db_session: Session, storage
) -> None:
    first = build_stored_file(
        db_session,
        storage,
        build_model(db_session, "owned-first", slug="owned-first"),
        filename="owned-first.stl",
    )
    first.deleted_at = utcnow() - timedelta(days=1)
    db_session.add(first)

    legacy_model = Model(name="Legacy", slug="legacy", hash="legacy-hash")
    db_session.add(legacy_model)
    db_session.commit()
    db_session.refresh(legacy_model)
    legacy_path = storage.blob_key("legacy", 1, "legacy.stl")
    _write(legacy_path, b"legacy-user-bytes")
    legacy = File(
        model_id=legacy_model.id,
        path=legacy_path,
        original_filename="legacy.stl",
        file_type=FileType.STL,
        version=1,
        size_bytes=17,
        sha256="legacy-file-hash",
        deleted_at=utcnow() - timedelta(days=1),
    )
    db_session.add(legacy)
    db_session.commit()
    db_session.refresh(legacy)
    first_id = first.id
    first_path = first.path

    result = gc_soft_deleted(retention_days=0)

    db_session.expire_all()
    assert result["resources_blocked"] == 1
    assert not Path(first_path).exists()
    assert Path(legacy_path).read_bytes() == b"legacy-user-bytes"
    assert db_session.get(File, first_id) is None
    assert db_session.get(File, legacy.id) is not None


def test_gc_adopts_and_purges_pre_ledger_artifact_with_matching_content(
    db_session: Session, storage
) -> None:
    model = Model(name="Legacy owned", slug="legacy-owned", hash="legacy-owned-hash")
    db_session.add(model)
    db_session.commit()
    db_session.refresh(model)
    content = b"artifact created before the ownership ledger"
    legacy_path = storage.blob_key("legacy-owned", 1, "legacy.stl")
    _write(legacy_path, content)
    artifact = File(
        model_id=model.id,
        path=legacy_path,
        original_filename="legacy.stl",
        file_type=FileType.STL,
        version=1,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )
    model.deleted_at = utcnow() - timedelta(days=1)
    db_session.add_all([model, artifact])
    db_session.commit()
    model_id = model.id
    artifact_id = artifact.id

    result = gc_soft_deleted(retention_days=0)

    assert result["rows"] == 1
    assert result["storage_completed"] == 1
    assert not Path(legacy_path).exists()
    db_session.expire_all()
    assert db_session.get(Model, model_id) is None
    assert db_session.get(File, artifact_id) is None


def test_hard_delete_late_storage_failure_leaks_remainder_without_db_rollback(
    db_session: Session, storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.trash import hard_delete_model

    first = build_stored_file(
        db_session,
        storage,
        build_model(db_session, "two-file-purge", slug="two-file-purge"),
        filename="two-file-purge.stl",
    )
    model = db_session.get(Model, first.model_id)
    assert model is not None
    second_key = store_owned_bytes(
        db_session,
        storage,
        storage.blob_key("two-file-purge", 2, "second.stl"),
        b"second",
    ).key
    second = File(
        model_id=model.id,
        path=second_key,
        original_filename="second.stl",
        file_type=FileType.STL,
        version=2,
        size_bytes=6,
        sha256="second-file-hash",
    )
    db_session.add(second)
    db_session.commit()
    db_session.refresh(second)

    real_rollback = storage.rollback_create
    deletes = 0

    def fail_second(receipt):
        nonlocal deletes
        deletes += 1
        if deletes == 2:
            raise OSError("storage became unavailable")
        return real_rollback(receipt)

    monkeypatch.setattr(storage, "rollback_create", fail_second)

    hard_delete_model(db_session, model)
    assert deletes == 0, "storage deletion must never happen before SQL commit"
    db_session.commit()

    from app.services.storage_deletion import process_storage_delete_intents

    result = process_storage_delete_intents()

    assert not Path(first.path).exists()
    assert Path(second_key).read_bytes() == b"second"
    assert result.completed == 1
    assert result.pending == 1
    db_session.expire_all()
    assert db_session.get(Model, model.id) is None
    assert db_session.get(File, first.id) is None
    assert db_session.get(File, second.id) is None


def test_hard_delete_rollback_preserves_blob_and_discards_intent(
    db_session: Session, storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.trash import hard_delete_model

    artifact = build_stored_file(
        db_session,
        storage,
        build_model(db_session, "rollback-safe", slug="rollback-safe"),
        filename="rollback-safe.stl",
    )
    artifact_key = artifact.path
    model = db_session.get(Model, artifact.model_id)
    assert model is not None
    calls = 0

    def _unexpected_delete(_receipt):
        nonlocal calls
        calls += 1
        return True

    monkeypatch.setattr(storage, "rollback_create", _unexpected_delete)

    hard_delete_model(db_session, model)
    assert calls == 0
    assert db_session.exec(select(StorageDeleteIntent)).all()
    db_session.rollback()

    assert calls == 0
    assert Path(artifact.path).exists()
    assert db_session.get(Model, model.id) is not None
    assert (
        db_session.exec(
            select(StorageDeleteIntent).where(
                StorageDeleteIntent.resource_kind == "file",
                StorageDeleteIntent.key == artifact_key,
            )
        ).all()
        == []
    )


def test_hard_delete_resolves_share_link_before_model_delete(
    db_session: Session, storage
) -> None:
    from app.services.storage_deletion import process_storage_delete_intents
    from app.services.trash import hard_delete_model

    artifact = build_stored_file(
        db_session,
        storage,
        build_model(db_session, "shared-purge", slug="shared-purge"),
        filename="shared-purge.stl",
    )
    model = db_session.get(Model, artifact.model_id)
    assert model is not None
    model.deleted_at = utcnow()
    db_session.add(model)
    db_session.add(
        ShareLink(
            model_id=model.id,
            token_hash="f" * 64,
            expires_at=utcnow() + timedelta(days=1),
        )
    )
    db_session.commit()

    hard_delete_model(db_session, model)
    assert Path(artifact.path).exists()
    db_session.commit()
    process_storage_delete_intents()

    assert db_session.get(Model, model.id) is None
    assert not Path(artifact.path).exists()


def test_gc_preserves_shared_stl_cache_until_last_artifact_is_purged(
    db_session: Session, storage
) -> None:
    expired = build_stored_file(
        db_session,
        storage,
        build_model(db_session, "cache-expired", slug="cache-expired"),
        filename="cache-expired.stl",
    )
    survivor = build_stored_file(
        db_session,
        storage,
        build_model(db_session, "cache-survivor", slug="cache-survivor"),
        filename="cache-survivor.stl",
    )
    survivor.sha256 = expired.sha256
    expired.deleted_at = utcnow() - timedelta(days=1)
    db_session.add(expired)
    db_session.add(survivor)
    db_session.commit()
    cache_key = store_owned_bytes(
        db_session, storage, storage.stl_cache_key(expired.sha256)
    ).key

    gc_soft_deleted(retention_days=0)

    assert Path(cache_key).exists()
    db_session.expire_all()
    assert db_session.get(File, survivor.id) is not None


def test_gc_preserves_unreferenced_collection_images(
    db_session: Session, storage
) -> None:
    collection = Collection(name="Docs", slug="docs", path="docs")
    db_session.add(collection)
    db_session.commit()
    db_session.refresh(collection)
    unreferenced = _write(storage.collection_image_key(collection.id, "gone.png"))

    removed = _cleanup_orphan_blobs(db_session)

    assert removed == 0
    assert Path(unreferenced).exists()


def test_gc_preserves_referenced_collection_images(
    db_session: Session, storage
) -> None:
    collection = Collection(name="Docs", slug="docs", path="docs")
    db_session.add(collection)
    db_session.commit()
    db_session.refresh(collection)
    name = "a" * 64 + ".png"
    collection.readme = f"![diagram](/api/v1/collections/{collection.id}/images/{name})"
    db_session.add(collection)
    db_session.commit()
    key = store_owned_bytes(
        db_session, storage, storage.collection_image_key(collection.id, name)
    ).key

    _cleanup_orphan_blobs(db_session)

    assert Path(key).exists()


def test_gc_does_not_infer_ownership_from_expired_collection_namespace(
    db_session: Session, storage
) -> None:
    collection = Collection(
        name="Old docs",
        slug="old-docs",
        path="old-docs",
        deleted_at=utcnow() - timedelta(days=1),
    )
    db_session.add(collection)
    db_session.commit()
    db_session.refresh(collection)
    collection_id = collection.id
    key = _write(storage.collection_image_key(collection.id, "unlinked.png"))

    gc_soft_deleted(retention_days=0)

    assert Path(key).exists()
    db_session.expire_all()
    assert db_session.get(Collection, collection_id) is None


def test_gc_hard_deletes_expired_collection_referenced_image(
    db_session: Session, storage
) -> None:
    name = "a" * 64 + ".png"
    collection = Collection(
        name="Old docs",
        slug="old-docs",
        path="old-docs",
        deleted_at=utcnow() - timedelta(days=1),
    )
    db_session.add(collection)
    db_session.commit()
    db_session.refresh(collection)
    collection.readme = f"![diagram](/api/v1/collections/{collection.id}/images/{name})"
    db_session.add(collection)
    db_session.commit()
    key = store_owned_bytes(
        db_session, storage, storage.collection_image_key(collection.id, name)
    ).key

    gc_soft_deleted(retention_days=0)

    assert not Path(key).exists()


class TestGcSoftDeleted:
    """The interlock: an open namespace-escape finding stops the GC deleting.

    `managed_storage_namespace_escape` means the vault may be pointed at a
    directory PrintStash does not own — somebody's mounted model library. While
    that is on the books no ownership receipt proves anything, so the hourly GC
    must refuse every expired row rather than empty the trash into a stranger's
    files. Each resource kind is its own loop with its own `except`, so each one
    is asserted separately: a single miss would let the GC keep deleting one kind
    of resource through an open escape finding.
    """

    def test_refuses_to_purge_an_expired_model(
        self, db_session: Session, storage
    ) -> None:
        artifact = build_stored_file(
            db_session,
            storage,
            build_model(db_session, "escape-model", slug="escape-model"),
            filename="escape-model.stl",
        )
        model = db_session.get(Model, artifact.model_id)
        assert model is not None
        model.deleted_at = utcnow() - timedelta(days=1)
        db_session.add(model)
        db_session.commit()
        _open_namespace_escape(db_session)

        result = gc_soft_deleted(retention_days=0)

        assert result["resources_blocked"] == 1
        db_session.expire_all()
        assert db_session.get(Model, model.id) is not None
        assert Path(artifact.path).exists()

    def test_refuses_to_purge_an_expired_document(
        self, db_session: Session, storage
    ) -> None:
        document = _binary_document(db_session, storage)
        key = storage.document_file_key(document.id, document.filename)
        document.deleted_at = utcnow() - timedelta(days=1)
        db_session.add(document)
        db_session.commit()
        _open_namespace_escape(db_session)

        result = gc_soft_deleted(retention_days=0)

        assert result["resources_blocked"] == 1
        db_session.expire_all()
        assert db_session.get(Document, document.id) is not None
        assert Path(key).exists()

    def test_refuses_to_purge_an_expired_standalone_artifact(
        self, db_session: Session, storage
    ) -> None:
        artifact = build_stored_file(
            db_session,
            storage,
            build_model(db_session, "escape-artifact", slug="escape-artifact"),
            filename="escape-artifact.stl",
        )
        artifact.deleted_at = utcnow() - timedelta(days=1)
        db_session.add(artifact)
        db_session.commit()
        _open_namespace_escape(db_session)

        result = gc_soft_deleted(retention_days=0)

        assert result["resources_blocked"] == 1
        db_session.expire_all()
        assert db_session.get(File, artifact.id) is not None
        assert Path(artifact.path).exists()

    def test_refuses_to_purge_an_expired_collection(self, db_session: Session) -> None:
        collection = Collection(
            name="Escape docs",
            slug="escape-docs",
            path="escape-docs",
            deleted_at=utcnow() - timedelta(days=1),
        )
        db_session.add(collection)
        db_session.commit()
        db_session.refresh(collection)
        _open_namespace_escape(db_session)

        result = gc_soft_deleted(retention_days=0)

        assert result["resources_blocked"] == 1
        db_session.expire_all()
        assert db_session.get(Collection, collection.id) is not None
