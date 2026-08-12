"""Storage backend abstraction: local filesystem and S3-compatible stores."""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import BinaryIO, Iterator

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _copy_stream_create_only(src: BinaryIO, dest: Path) -> Path:
    """Fully stage and fsync a stream, then publish *dest* atomically/no-replace."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".printstash-download-", dir=dest.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as destination:
            shutil.copyfileobj(src, destination)
            destination.flush()
            os.fsync(destination.fileno())
        try:
            os.link(temp, dest, follow_symlinks=False)
        except FileExistsError as exc:
            raise StorageCollisionError(str(dest)) from exc
        return dest
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            logger.warning(
                "storage download temp cleanup failed", extra={"path": str(temp)}
            )


@dataclass(frozen=True)
class StorageObjectInfo:
    size: int
    etag: str | None = None


class StorageCollisionError(FileExistsError):
    """A create-only write found an object already present at its exact key."""


@dataclass(frozen=True)
class CreationReceipt:
    """Positive evidence that one storage operation created one exact object.

    The local fingerprint prevents rollback cleanup from unlinking a file that
    replaced our object after creation. Remote stores use a per-operation token
    written into object metadata for the same purpose.
    """

    key: str
    size: int
    token: str
    backend: str
    namespace: str
    etag: str | None = None
    device: int | None = None
    inode: int | None = None
    ctime_ns: int | None = None


class StorageBackend(ABC):
    """Abstract interface for vault file operations.

    Keys are opaque identifiers: for the local backend they are absolute
    filesystem paths; for S3 they are object keys within the bucket.

    Callers must never branch on the concrete backend type. Anything that
    needs a real filesystem path uses ``local_path()``; anything moving a
    staged upload into the vault uses ``move_in()``; HTTP handlers deciding
    between file and streaming responses use ``direct_path()``.
    """

    @abstractmethod
    def blob_key(self, slug: str, version: int, filename: str) -> str: ...

    @abstractmethod
    def thumbnail_key(self, file_id: int) -> str: ...

    @abstractmethod
    def legacy_thumbnail_key(self, file_id: int) -> str:
        """PNG key used before thumbnails moved to WebP. Read/delete only —
        new thumbnails are always written under ``thumbnail_key``."""

    @abstractmethod
    def stl_cache_key(self, sha256: str) -> str:
        """Key for a derived-STL preview cached by source sha256."""

    @abstractmethod
    def collection_image_key(self, collection_id: int, name: str) -> str:
        """Key for an image embedded in a collection's readme. ``name`` is a
        server-generated ``{sha256}.{ext}`` — never raw user input."""

    @abstractmethod
    def document_file_key(self, document_id: int, name: str) -> str:
        """Key for a Document's binary blob (PDF/other). ``name`` is a sanitised
        filename — never raw user input."""

    @abstractmethod
    def document_image_key(self, document_id: int, name: str) -> str:
        """Key for an image embedded in a markdown Document. ``name`` is a
        server-generated ``{sha256}.{ext}`` — never raw user input."""

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    def write_stream(self, src: BinaryIO, key: str) -> int:
        """Compatibility create-only write; callers needing proof use create_*()."""
        return self.create_stream(src, key).size

    def write_bytes(self, data: bytes, key: str) -> int:
        """Compatibility create-only write; never replaces an existing key."""
        return self.create_bytes(data, key).size

    def create_stream(self, src: BinaryIO, key: str) -> CreationReceipt:
        """Create *key* without replacement.

        Adapters must provide a backend-native atomic conditional create. A
        check-then-upload compatibility fallback would silently reintroduce the
        overwrite race this contract exists to prevent.
        """
        del src, key
        raise NotImplementedError("atomic_create_not_supported")

    def create_bytes(self, data: bytes, key: str) -> CreationReceipt:
        from io import BytesIO

        return self.create_stream(BytesIO(data), key)

    def replace_stream(
        self, src: BinaryIO, receipt: CreationReceipt
    ) -> CreationReceipt:
        """Atomically replace an object only while positive proof still matches."""
        del src, receipt
        raise NotImplementedError("atomic_replace_not_supported")

    def replace_bytes(
        self, data: bytes, receipt: CreationReceipt
    ) -> CreationReceipt:
        from io import BytesIO

        return self.replace_stream(BytesIO(data), receipt)

    def rollback_create(self, receipt: CreationReceipt) -> bool:
        """Remove a just-created object only when its receipt still matches.

        Compatibility adapters cannot positively verify their random token, so
        they fail closed and leak the uncertain object.
        """
        del receipt
        return False

    def creation_matches(self, receipt: CreationReceipt) -> bool:
        """Return whether the exact object still matches positive proof."""
        del receipt
        return False

    def verify_destructive_access(self, keys: list[str]) -> None:
        """Prove delete capability without touching any pre-existing object."""
        del keys
        raise NotImplementedError("destructive_access_probe_not_supported")

    @abstractmethod
    def move(self, src_key: str, dest_key: str) -> None: ...

    @abstractmethod
    def stat_size(self, key: str) -> int: ...

    def object_info(self, key: str) -> StorageObjectInfo | None:
        """Return existence, size, and a cache validator through one seam."""
        if not self.exists(key):
            return None
        return StorageObjectInfo(size=self.stat_size(key))

    @abstractmethod
    def read_bytes(self, key: str) -> bytes: ...

    @abstractmethod
    def stream_chunks(
        self, key: str, chunk_size: int = 1024 * 1024
    ) -> Iterator[bytes]: ...

    @abstractmethod
    def download_to_path(self, key: str, dest: Path) -> Path: ...

    @abstractmethod
    def upload_file(self, src: Path, key: str) -> None: ...

    @abstractmethod
    def ensure_setup(self) -> None: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def list_keys(self, prefix: str = "") -> list[str]: ...

    @abstractmethod
    def walk_keys(self, prefix: str = "") -> Iterator[str]: ...

    @abstractmethod
    def usage(self, prefix: str = "") -> dict: ...

    @abstractmethod
    def presigned_download_url(self, key: str, filename: str) -> str | None: ...

    @abstractmethod
    def health_probe(self) -> dict: ...

    @abstractmethod
    def direct_path(self, key: str) -> Path | None:
        """Return the on-disk path for *key*, or None when the backend has
        no direct filesystem representation (S3)."""
        ...

    @contextmanager
    def local_path(self, key: str) -> Iterator[Path]:
        """Yield a local filesystem path for *key*.

        Local backend yields the real path. Remote backends download to a
        temporary file and remove it on exit. The single owner of the
        temp-file lifecycle — callers never manage cleanup.
        """
        direct = self.direct_path(key)
        if direct is not None:
            yield direct
            return
        fd, name = tempfile.mkstemp(suffix=Path(key).suffix)
        os.close(fd)
        tmp = Path(name)
        tmp.unlink()
        try:
            self.download_to_path(key, tmp)
            yield tmp
        finally:
            tmp.unlink(missing_ok=True)

    def move_in(self, src: Path, dest_key: str) -> CreationReceipt:
        """Move a local staged file into the vault at *dest_key*.

        Concrete local storage overrides this with create-only placement;
        remote backends upload and then remove the staged file.
        """
        with src.open("rb") as incoming:
            receipt = self.create_stream(incoming, dest_key)
        try:
            src.unlink()
        except OSError:
            # Destination publication already succeeded. Returning its receipt
            # lets the caller commit ownership (or roll it back precisely);
            # failing here would strand an untracked destination. A duplicate
            # staging file is the data-preserving failure mode.
            logger.warning(
                "storage move-in left staged source after successful create",
                extra={"source": str(src), "destination": dest_key},
            )
        return receipt


# ---------------------------------------------------------------------------
# Local filesystem backend
# ---------------------------------------------------------------------------


class LocalStorageBackend(StorageBackend):
    @staticmethod
    def _assert_no_managed_escape(path: Path) -> None:
        """Reject a key lexically inside a managed root that resolves outside it."""
        lexical = path.expanduser().absolute()
        roots = [settings.data_dir, settings.thumb_dir]
        backup_root = getattr(settings, "backup_dir", None)
        if backup_root is not None:
            roots.append(backup_root)
        for configured_root in roots:
            lexical_root = Path(configured_root).expanduser().absolute()
            if lexical == lexical_root or lexical.is_relative_to(lexical_root):
                resolved_root = lexical_root.resolve(strict=False)
                resolved = lexical.resolve(strict=False)
                if resolved != resolved_root and not resolved.is_relative_to(
                    resolved_root
                ):
                    raise StorageCollisionError("managed_storage_symlink_escape")
                return

    @staticmethod
    def _owned_namespace(path: Path) -> str | None:
        resolved = path.resolve(strict=False)
        roots: list[tuple[str, Path]] = [
            ("data", settings.data_dir),
            ("thumb", settings.thumb_dir),
        ]
        backup_root = getattr(settings, "backup_dir", None)
        if backup_root is not None:
            roots.append(("backup", backup_root))
        for role, configured_root in roots:
            root = Path(configured_root).resolve(strict=False)
            if resolved == root or resolved.is_relative_to(root):
                return f"{role}:{root}"
        return None

    def direct_path(self, key: str) -> Path | None:
        return Path(key)

    @staticmethod
    def _relocated_receipt(
        receipt: CreationReceipt, path: Path
    ) -> CreationReceipt:
        # rename(2) updates ctime on Linux. Device/inode/size still prove that
        # quarantine captured the same object selected by the preflight check.
        current = path.stat(follow_symlinks=False)
        return replace(receipt, key=str(path), ctime_ns=current.st_ctime_ns)

    def blob_key(self, slug: str, version: int, filename: str) -> str:
        return str(settings.data_dir / slug / f"v{version}" / filename)

    def thumbnail_key(self, file_id: int) -> str:
        return str(settings.thumb_dir / f"{file_id}.webp")

    def legacy_thumbnail_key(self, file_id: int) -> str:
        return str(settings.thumb_dir / f"{file_id}.png")

    def stl_cache_key(self, sha256: str) -> str:
        return str(settings.thumb_dir / "stl-cache" / f"{sha256}.stl")

    def collection_image_key(self, collection_id: int, name: str) -> str:
        return str(
            settings.thumb_dir / "collection-images" / str(collection_id) / name
        )

    def document_file_key(self, document_id: int, name: str) -> str:
        return str(settings.data_dir / "documents" / str(document_id) / name)

    def document_image_key(self, document_id: int, name: str) -> str:
        return str(
            settings.thumb_dir / "document-images" / str(document_id) / name
        )

    def exists(self, key: str) -> bool:
        return Path(key).exists()

    def write_stream(self, src: BinaryIO, key: str) -> int:
        return self.create_stream(src, key).size

    def write_bytes(self, data: bytes, key: str) -> int:
        return self.create_bytes(data, key).size

    def create_stream(self, src: BinaryIO, key: str) -> CreationReceipt:
        # A remote-compatible subclass may override ``direct_path`` while
        # inheriting this class. Keep it on the generic seam rather than
        # interpreting its opaque key as a local filesystem path.
        if self.direct_path(key) is None:
            return StorageBackend.create_stream(self, src, key)

        dest = Path(key)
        self._assert_no_managed_escape(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".printstash-create-", dir=dest.parent)
        temp = Path(temp_name)
        written = 0
        try:
            with os.fdopen(fd, "wb") as out:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    written += len(chunk)
                out.flush()
                os.fsync(out.fileno())
            try:
                # link(2) is an atomic no-replace publication on the same
                # filesystem. Readers never observe the partial temp file.
                os.link(temp, dest, follow_symlinks=False)
            except FileExistsError as exc:
                raise StorageCollisionError(str(dest)) from exc
            # Dropping the temporary hard link changes ctime/link-count. Capture
            # the fingerprint only after the destination is the sole link.
            temp.unlink()
            stat = dest.stat(follow_symlinks=False)
            return CreationReceipt(
                key=str(dest),
                size=written,
                token=uuid.uuid4().hex,
                backend="local",
                namespace=self._owned_namespace(dest)
                or f"external:{dest.parent.resolve(strict=False)}",
                device=stat.st_dev,
                inode=stat.st_ino,
                ctime_ns=stat.st_ctime_ns,
            )
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                logger.warning("storage create temp cleanup failed", extra={"path": str(temp)})

    def _quarantine_owned(self, receipt: CreationReceipt) -> Path | None:
        """Move the current exact inode aside before any unlink or replacement.

        POSIX has no unlink-if-inode-still-matches primitive. A check followed
        by unlink has a TOCTOU window that could remove a newly mounted or
        concurrently replaced path. Renaming into a random same-directory
        quarantine is atomic and non-destructive; only the moved inode is then
        eligible for deletion.
        """
        if not self.creation_matches(receipt):
            return None
        dest = Path(receipt.key)
        fd, quarantine_name = tempfile.mkstemp(
            prefix=".printstash-quarantine-", dir=dest.parent
        )
        os.close(fd)
        quarantine = Path(quarantine_name)
        moved = False
        try:
            # The only overwritten inode is the empty placeholder just created
            # by this operation. Whichever inode is at dest is preserved at the
            # quarantine path for a second proof check.
            os.replace(dest, quarantine)
            moved = True
            moved_receipt = self._relocated_receipt(receipt, quarantine)
            if self.creation_matches(moved_receipt):
                return quarantine

            # The path changed after the first check. Restore without replacing
            # anything that may now occupy the original destination.
            try:
                os.link(quarantine, dest, follow_symlinks=False)
            except FileExistsError as exc:
                logger.critical(
                    "storage quarantine preserved a raced object for recovery",
                    extra={"destination": str(dest), "quarantine": str(quarantine)},
                )
                raise StorageCollisionError(str(dest)) from exc
            quarantine.unlink()
            moved = False
            return None
        finally:
            if not moved:
                quarantine.unlink(missing_ok=True)

    def rollback_create(self, receipt: CreationReceipt) -> bool:
        quarantine = self._quarantine_owned(receipt)
        if quarantine is None:
            logger.warning(
                "storage rollback skipped: destination no longer matches receipt",
                extra={"key": receipt.key},
            )
            return False
        moved_receipt = self._relocated_receipt(receipt, quarantine)
        if not self.creation_matches(moved_receipt):
            logger.critical(
                "storage quarantine changed before deletion; preserving it",
                extra={"quarantine": str(quarantine)},
            )
            return False
        quarantine.unlink()
        return True

    def replace_stream(
        self, src: BinaryIO, receipt: CreationReceipt
    ) -> CreationReceipt:
        dest = Path(receipt.key)
        fd, temp_name = tempfile.mkstemp(prefix=".printstash-replace-", dir=dest.parent)
        temp = Path(temp_name)
        written = 0
        try:
            with os.fdopen(fd, "wb") as out:
                while chunk := src.read(1024 * 1024):
                    out.write(chunk)
                    written += len(chunk)
                out.flush()
                os.fsync(out.fileno())
            quarantine = self._quarantine_owned(receipt)
            if quarantine is None:
                raise StorageCollisionError(receipt.key)
            try:
                # Atomic no-replace publication. If another process claims the
                # path after quarantine, both its file and our old owned inode
                # survive; the replacement aborts.
                os.link(temp, dest, follow_symlinks=False)
            except FileExistsError as exc:
                logger.critical(
                    "storage replacement collision preserved old quarantine",
                    extra={"destination": str(dest), "quarantine": str(quarantine)},
                )
                raise StorageCollisionError(receipt.key) from exc
            temp.unlink()
            stat_result = dest.stat(follow_symlinks=False)
            replacement_receipt = CreationReceipt(
                key=str(dest),
                size=written,
                token=uuid.uuid4().hex,
                backend="local",
                namespace=receipt.namespace,
                device=stat_result.st_dev,
                inode=stat_result.st_ino,
                ctime_ns=stat_result.st_ctime_ns,
            )
            try:
                quarantine.unlink()
            except OSError:
                # The new object and receipt are durable. Preserve an uncertain
                # old quarantine rather than failing and orphaning the new one.
                logger.exception(
                    "storage replacement left an owned quarantine",
                    extra={"quarantine": str(quarantine)},
                )
            return replacement_receipt
        finally:
            temp.unlink(missing_ok=True)

    def creation_matches(self, receipt: CreationReceipt) -> bool:
        if receipt.backend != "local":
            return False
        path = Path(receipt.key)
        current_namespace = self._owned_namespace(path)
        if current_namespace is None or current_namespace != receipt.namespace:
            logger.warning(
                "storage delete skipped: key is outside its recorded current root",
                extra={"key": receipt.key},
            )
            return False
        try:
            stat = path.stat(follow_symlinks=False)
        except FileNotFoundError:
            return False
        if (
            stat.st_dev != receipt.device
            or stat.st_ino != receipt.inode
            or stat.st_ctime_ns != receipt.ctime_ns
            or stat.st_size != receipt.size
        ):
            return False
        return True

    def verify_destructive_access(self, keys: list[str]) -> None:
        # Probe every distinct parent because nested ACLs/read-only submounts
        # can differ beneath one configured root. mkstemp is O_EXCL: cleanup
        # targets only the inode this probe just created.
        if any(self.direct_path(key) is None for key in keys):
            return super().verify_destructive_access(keys)
        for parent in {Path(key).parent for key in keys}:
            fd, probe_name = tempfile.mkstemp(
                prefix=".printstash-delete-probe-", dir=parent
            )
            os.close(fd)
            Path(probe_name).unlink()

    def move(self, src_key: str, dest_key: str) -> None:
        del src_key, dest_key
        raise RuntimeError("unchecked_storage_move_disabled")

    def stat_size(self, key: str) -> int:
        return Path(key).stat().st_size

    def object_info(self, key: str) -> StorageObjectInfo | None:
        try:
            stat = Path(key).stat()
        except FileNotFoundError:
            return None
        return StorageObjectInfo(
            size=stat.st_size,
            etag=f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"',
        )

    def read_bytes(self, key: str) -> bytes:
        return Path(key).read_bytes()

    def stream_chunks(self, key: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        with Path(key).open("rb") as fh:
            while True:
                chunk = fh.read(chunk_size)
                if not chunk:
                    break
                yield chunk

    def download_to_path(self, key: str, dest: Path) -> Path:
        with Path(key).open("rb") as source:
            return _copy_stream_create_only(source, dest)

    def upload_file(self, src: Path, key: str) -> None:
        with src.open("rb") as source:
            self.create_stream(source, key)

    def ensure_setup(self) -> None:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.thumb_dir.mkdir(parents=True, exist_ok=True)

    def delete(self, key: str) -> None:
        del key
        raise RuntimeError("unchecked_storage_delete_disabled")

    def list_keys(self, prefix: str = "") -> list[str]:
        root = Path(prefix) if prefix else settings.data_dir
        if not root.exists():
            return []
        return [str(p) for p in root.rglob("*") if p.is_file()]

    def walk_keys(self, prefix: str = "") -> Iterator[str]:
        root = Path(prefix) if prefix else settings.data_dir
        if not root.exists():
            return
        for p in root.rglob("*"):
            if p.is_file():
                yield str(p)

    def usage(self, prefix: str = "") -> dict:
        root = Path(prefix) if prefix else settings.data_dir
        total_size = 0
        object_count = 0
        if root.exists():
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    total_size += path.stat().st_size
                    object_count += 1
                except OSError:
                    continue
        return {
            "backend": "local",
            "prefix": str(root),
            "object_count": object_count,
            "total_size_bytes": total_size,
        }

    def presigned_download_url(self, key: str, filename: str) -> str | None:
        return None

    def health_probe(self) -> dict:
        data_ok = settings.data_dir.exists()
        thumb_ok = settings.thumb_dir.exists()
        return {
            "backend": "local",
            "ok": data_ok and thumb_ok,
            "data_dir": str(settings.data_dir),
            "thumb_dir": str(settings.thumb_dir),
        }


# ---------------------------------------------------------------------------
# S3-compatible backend (AWS S3, Cloudflare R2, SeaweedFS, MinIO, etc.)
# ---------------------------------------------------------------------------


class S3StorageBackend(StorageBackend):  # pragma: no cover — needs a real S3-compatible
    # endpoint; verified against SeaweedFS in the storage-s3 CI job (see docs).
    def __init__(self) -> None:
        import boto3
        from botocore.config import Config as BotoConfig

        if not settings.s3_bucket:
            raise RuntimeError("VAULT_S3_BUCKET is required when storage_backend=s3")

        client_kwargs: dict = {
            "service_name": "s3",
            "region_name": settings.s3_region or "auto",
            "aws_access_key_id": settings.s3_access_key or None,
            "aws_secret_access_key": settings.s3_secret_key or None,
            "config": BotoConfig(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
            ),
        }
        if settings.s3_endpoint_url:
            client_kwargs["endpoint_url"] = settings.s3_endpoint_url

        self._client = boto3.client(**client_kwargs)
        self._bucket = settings.s3_bucket

        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        import botocore.exceptions

        try:
            self._client.head_bucket(Bucket=self._bucket)
            logger.info("s3: bucket %r found", self._bucket)
        except botocore.exceptions.ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in ("404", "NoSuchBucket", "NotFound"):
                logger.info("s3: creating bucket %r", self._bucket)
                location = (
                    {"LocationConstraint": settings.s3_region}
                    if settings.s3_region and settings.s3_region != "auto"
                    else {}
                )
                self._client.create_bucket(
                    Bucket=self._bucket, CreateBucketConfiguration=location
                )
            else:
                raise

    def _apply_lifecycle_policy(self) -> None:
        expiration_days = int(settings.s3_lifecycle_expiration_days or 0)
        transition_days = int(settings.s3_lifecycle_transition_days or 0)
        if expiration_days <= 0 and transition_days <= 0:
            return
        rule: dict = {
            "ID": "vault-data-lifecycle",
            "Status": "Enabled",
            "Filter": {"Prefix": self._prefix()},
        }
        if transition_days > 0:
            rule["Transitions"] = [
                {
                    "Days": transition_days,
                    "StorageClass": settings.s3_transition_storage_class,
                }
            ]
        if expiration_days > 0:
            rule["Expiration"] = {"Days": expiration_days}
        self._client.put_bucket_lifecycle_configuration(
            Bucket=self._bucket,
            LifecycleConfiguration={"Rules": [rule]},
        )

    def _prefix(self) -> str:
        return "vault-data/"

    def direct_path(self, key: str) -> Path | None:
        return None

    def blob_key(self, slug: str, version: int, filename: str) -> str:
        return f"{self._prefix()}files/{slug}/v{version}/{filename}"

    def thumbnail_key(self, file_id: int) -> str:
        return f"{self._prefix()}thumbs/{file_id}.webp"

    def legacy_thumbnail_key(self, file_id: int) -> str:
        return f"{self._prefix()}thumbs/{file_id}.png"

    def stl_cache_key(self, sha256: str) -> str:
        return f"{self._prefix()}stl-cache/{sha256}.stl"

    def collection_image_key(self, collection_id: int, name: str) -> str:
        return f"{self._prefix()}collection-images/{collection_id}/{name}"

    def document_file_key(self, document_id: int, name: str) -> str:
        return f"{self._prefix()}documents/{document_id}/{name}"

    def document_image_key(self, document_id: int, name: str) -> str:
        return f"{self._prefix()}document-images/{document_id}/{name}"

    def exists(self, key: str) -> bool:
        return self.object_info(key) is not None

    def object_info(self, key: str) -> StorageObjectInfo | None:
        import botocore.exceptions

        try:
            response = self._client.head_object(Bucket=self._bucket, Key=key)
        except botocore.exceptions.ClientError as exc:
            # Only a genuine "not there" is False. Credential, permission and
            # network errors must raise: callers need to distinguish an absent
            # object from a storage backend they cannot inspect.
            if exc.response.get("Error", {}).get("Code") in (
                "404",
                "NoSuchKey",
                "NotFound",
            ):
                return None
            raise
        etag = response.get("ETag")
        if etag and not str(etag).startswith('"'):
            etag = f'"{etag}"'
        return StorageObjectInfo(
            size=int(response.get("ContentLength", 0) or 0),
            etag=str(etag) if etag else None,
        )

    def write_stream(self, src: BinaryIO, key: str) -> int:
        return self.create_stream(src, key).size

    def write_bytes(self, data: bytes, key: str) -> int:
        return self.create_bytes(data, key).size

    def create_stream(self, src: BinaryIO, key: str) -> CreationReceipt:
        import botocore.exceptions

        token = uuid.uuid4().hex
        try:
            response = self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=src,
                IfNoneMatch="*",
                Metadata={"printstash-create-token": token},
            )
        except botocore.exceptions.ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if code in {"412", "PreconditionFailed", "ConditionalRequestConflict"} or status in {409, 412}:
                raise StorageCollisionError(key) from exc
            raise
        info = self.object_info(key)
        if info is None:
            raise RuntimeError(f"storage create could not verify destination: {key}")
        etag = response.get("ETag") or info.etag
        return CreationReceipt(
            key=key,
            size=info.size,
            token=token,
            backend="s3",
            namespace=f"{self._bucket}/{self._prefix()}",
            etag=str(etag) if etag else None,
        )

    def rollback_create(self, receipt: CreationReceipt) -> bool:
        if not self.creation_matches(receipt):
            return False
        kwargs = {"Bucket": self._bucket, "Key": receipt.key}
        if receipt.etag:
            kwargs["IfMatch"] = receipt.etag
        self._client.delete_object(**kwargs)
        return True

    def replace_stream(
        self, src: BinaryIO, receipt: CreationReceipt
    ) -> CreationReceipt:
        import botocore.exceptions

        if not receipt.etag or not self.creation_matches(receipt):
            raise StorageCollisionError(receipt.key)
        token = uuid.uuid4().hex
        try:
            response = self._client.put_object(
                Bucket=self._bucket,
                Key=receipt.key,
                Body=src,
                IfMatch=receipt.etag,
                Metadata={"printstash-create-token": token},
            )
        except botocore.exceptions.ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            status_code = exc.response.get("ResponseMetadata", {}).get(
                "HTTPStatusCode"
            )
            if code in {"412", "PreconditionFailed", "ConditionalRequestConflict"} or status_code in {
                409,
                412,
            }:
                raise StorageCollisionError(receipt.key) from exc
            raise
        info = self.object_info(receipt.key)
        if info is None:
            raise RuntimeError("storage_replace_verification_failed")
        etag = response.get("ETag") or info.etag
        return CreationReceipt(
            key=receipt.key,
            size=info.size,
            token=token,
            backend="s3",
            namespace=receipt.namespace,
            etag=str(etag) if etag else None,
        )

    def creation_matches(self, receipt: CreationReceipt) -> bool:
        if (
            receipt.backend != "s3"
            or receipt.namespace != f"{self._bucket}/{self._prefix()}"
        ):
            return False
        try:
            response = self._client.head_object(Bucket=self._bucket, Key=receipt.key)
        except Exception:
            raise
        metadata = response.get("Metadata", {})
        if metadata.get("printstash-create-token") != receipt.token:
            logger.warning(
                "storage rollback skipped: remote token no longer matches receipt",
                extra={"key": receipt.key},
            )
            return False
        if int(response.get("ContentLength", -1)) != receipt.size:
            return False
        if receipt.etag and str(response.get("ETag", "")) != receipt.etag:
            return False
        return True

    def verify_destructive_access(self, keys: list[str]) -> None:
        if not keys:
            return
        probe_key = f"{self._prefix()}.printstash-delete-probes/{uuid.uuid4().hex}"
        receipt = self.create_bytes(b"", probe_key)
        if not self.rollback_create(receipt):
            raise RuntimeError("storage_delete_probe_cleanup_unverified")

    def move(self, src_key: str, dest_key: str) -> None:
        del src_key, dest_key
        raise RuntimeError("unchecked_storage_move_disabled")

    def stat_size(self, key: str) -> int:
        resp = self._client.head_object(Bucket=self._bucket, Key=key)
        return resp.get("ContentLength", 0)

    def read_bytes(self, key: str) -> bytes:
        resp = self._client.get_object(Bucket=self._bucket, Key=key)
        return resp["Body"].read()

    def stream_chunks(self, key: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        resp = self._client.get_object(Bucket=self._bucket, Key=key)
        body = resp["Body"]
        while True:
            chunk = body.read(chunk_size)
            if not chunk:
                break
            yield chunk

    def download_to_path(self, key: str, dest: Path) -> Path:
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        return _copy_stream_create_only(response["Body"], dest)

    def upload_file(self, src: Path, key: str) -> None:
        with src.open("rb") as source:
            self.create_stream(source, key)

    def ensure_setup(self) -> None:
        self._ensure_bucket()
        if (
            int(settings.s3_lifecycle_expiration_days or 0) > 0
            or int(settings.s3_lifecycle_transition_days or 0) > 0
        ):
            # Bucket lifecycle configuration is bucket-wide and replacing it
            # can destroy operator-managed rules or expire objects that this
            # installation never proved it owns. Keep automatic mutation off.
            logger.warning(
                "automatic S3 lifecycle mutation is disabled for data safety"
            )

    def delete(self, key: str) -> None:
        del key
        raise RuntimeError("unchecked_storage_delete_disabled")

    def list_keys(self, prefix: str = "") -> list[str]:
        full_prefix = prefix or self._prefix()
        keys: list[str] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=full_prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        return keys

    def walk_keys(self, prefix: str = "") -> Iterator[str]:
        full_prefix = prefix or self._prefix()
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=full_prefix):
            for obj in page.get("Contents", []):
                yield obj["Key"]

    def usage(self, prefix: str = "") -> dict:
        full_prefix = prefix or self._prefix()
        total_size = 0
        object_count = 0
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=full_prefix):
            for obj in page.get("Contents", []):
                object_count += 1
                total_size += int(obj.get("Size", 0) or 0)
        return {
            "backend": "s3",
            "bucket": self._bucket,
            "prefix": full_prefix,
            "object_count": object_count,
            "total_size_bytes": total_size,
        }

    def presigned_download_url(self, key: str, filename: str) -> str | None:
        return self._client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self._bucket,
                "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{filename}"',
            },
            ExpiresIn=int(settings.s3_presigned_url_expire_seconds),
        )

    def health_probe(self) -> dict:
        try:
            self._client.head_bucket(Bucket=self._bucket)
            return {
                "backend": "s3",
                "ok": True,
                "bucket": self._bucket,
                "endpoint": settings.s3_endpoint_url,
            }
        except Exception as exc:
            return {
                "backend": "s3",
                "ok": False,
                "bucket": self._bucket,
                "endpoint": settings.s3_endpoint_url,
                "error": str(exc),
            }


# ---------------------------------------------------------------------------
# Module-level backend singleton
# ---------------------------------------------------------------------------

_backend: StorageBackend | None = None


def get_backend() -> StorageBackend:
    global _backend
    if _backend is None:
        if settings.storage_backend == "s3":
            logger.info(
                "initialising S3 storage backend (bucket=%s)", settings.s3_bucket
            )
            _backend = S3StorageBackend()
        else:
            logger.info("initialising local storage backend")
            _backend = LocalStorageBackend()
    return _backend


def init_backend() -> StorageBackend:
    global _backend
    _backend = get_backend()
    _backend.ensure_setup()
    return _backend
