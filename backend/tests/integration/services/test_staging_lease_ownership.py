"""Proving a staged file is still the file the lease was taken on.

A staging lease is a promise to delete something later, and later is when the danger is.
The recorded path is a string; by the time a lease expires, the file at that path may have
been replaced, may be a symlink to somebody's library, or may be a directory. Unlinking on
the strength of a path alone is how a cleanup pass deletes a user's models.

So every lease carries the device, inode and ctime of the file it was taken on, and the
matcher hands back a path **only** when all of them still agree. Everything else — a
missing file, a non-regular file, a different inode, a changed size — returns `None`, and
`None` means "leave it alone", never "delete it anyway".

Capture spools are the one exception, and a deliberate one: their bytes are written into
the recorded inode while the request is in flight, so size and ctime legitimately change.
Device and inode still hold, and on a filesystem that supports it an extended attribute
carries the slot id — so a *replacement* at the same deterministic path, even one that
reuses the inode number, is refused. On a filesystem without xattrs the device/inode proof
is the fallback, and the path is still never recursively scanned.
"""

from __future__ import annotations

import errno
from pathlib import Path

import pytest

from app.core.time import utcnow
from app.db.models import StagingLease
from app.services import staging_leases


def _lease_for(path: Path, **overrides) -> StagingLease:
    info = path.lstat()
    fields = {
        "id": "lease-1",
        "path": str(path),
        "size_bytes": info.st_size,
        "sha256": "a" * 64,
        "device": info.st_dev,
        "inode": info.st_ino,
        "ctime_ns": info.st_ctime_ns,
        "expires_at": utcnow(),
    }
    fields.update(overrides)
    return StagingLease(**fields)


class TestMatchingPath:
    def test_matches_the_file_the_lease_was_taken_on(self, tmp_path: Path) -> None:
        path = tmp_path / "staged.bin"
        path.write_bytes(b"payload")

        assert staging_leases._matching_path(_lease_for(path)) == path

    def test_refuses_a_lease_with_no_identity_recorded(self, tmp_path: Path) -> None:
        path = tmp_path / "staged.bin"
        path.write_bytes(b"payload")

        # A lease from before identity was recorded proves nothing about the
        # file at its path, so it is never a licence to unlink.
        assert staging_leases._matching_path(_lease_for(path, device=None)) is None

    def test_refuses_a_file_that_is_gone(self, tmp_path: Path) -> None:
        path = tmp_path / "staged.bin"
        path.write_bytes(b"payload")
        lease = _lease_for(path)
        path.unlink()

        assert staging_leases._matching_path(lease) is None

    def test_refuses_a_path_that_is_now_a_directory(self, tmp_path: Path) -> None:
        path = tmp_path / "staged.bin"
        path.write_bytes(b"payload")
        lease = _lease_for(path)
        path.unlink()
        path.mkdir()

        assert staging_leases._matching_path(lease) is None

    def test_refuses_a_path_that_is_now_a_symlink(self, tmp_path: Path) -> None:
        target = tmp_path / "somebodys-library.stl"
        target.write_bytes(b"not ours")
        path = tmp_path / "staged.bin"
        path.write_bytes(b"payload")
        lease = _lease_for(path)
        path.unlink()
        path.symlink_to(target)

        # This is the whole reason `lstat` is used rather than `stat`: following
        # the link would delete the user's file.
        assert staging_leases._matching_path(lease) is None
        assert target.exists()

    def test_refuses_a_replacement_at_the_same_path(self, tmp_path: Path) -> None:
        path = tmp_path / "staged.bin"
        path.write_bytes(b"payload")
        lease = _lease_for(path)
        path.unlink()
        path.write_bytes(b"payload")

        assert staging_leases._matching_path(lease) is None

    def test_refuses_a_file_whose_size_changed(self, tmp_path: Path) -> None:
        path = tmp_path / "staged.bin"
        path.write_bytes(b"payload")
        lease = _lease_for(path, size_bytes=999)

        # An ordinary lease is taken on a finished file; a size change means it
        # is not the file the lease describes.
        assert staging_leases._matching_path(lease) is None


class TestMatchingCaptureStagingPath:
    @pytest.fixture
    def spool(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from app.core.config import _overlay

        monkeypatch.setitem(_overlay, "staging_dir", tmp_path)

        def build(slot_id: str = "slot-1", data: bytes = b"partial") -> Path:
            path = staging_leases.capture_slot_staging_path(slot_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return path

        return build

    def _lease(self, path: Path, slot_id: str = "slot-1", **overrides) -> StagingLease:
        return _lease_for(path, capture_upload_slot_id=slot_id, **overrides)

    def test_matches_a_spool_still_being_written(self, spool) -> None:
        path = spool()
        lease = self._lease(path)
        path.write_bytes(b"more bytes than before")

        # Size and ctime legitimately change while the upload is in flight.
        assert staging_leases._matching_capture_staging_path(lease) == path

    def test_refuses_a_lease_naming_no_slot(self, spool) -> None:
        path = spool()

        assert (
            staging_leases._matching_capture_staging_path(
                _lease_for(path, capture_upload_slot_id=None)
            )
            is None
        )

    def test_refuses_a_lease_with_no_inode_recorded(self, spool) -> None:
        path = spool()

        assert (
            staging_leases._matching_capture_staging_path(self._lease(path, inode=None))
            is None
        )

    def test_refuses_a_path_that_is_not_the_slots_own(
        self, spool, tmp_path: Path
    ) -> None:
        path = spool()
        elsewhere = tmp_path / "elsewhere.bin"
        elsewhere.write_bytes(b"partial")

        # The path is derived from the slot id, so a lease pointing anywhere
        # else was not written by this slot.
        assert (
            staging_leases._matching_capture_staging_path(
                _lease_for(elsewhere, capture_upload_slot_id="slot-1")
            )
            is None
        )

    def test_refuses_a_spool_that_is_gone(self, spool) -> None:
        path = spool()
        lease = self._lease(path)
        path.unlink()

        assert staging_leases._matching_capture_staging_path(lease) is None

    def test_refuses_a_path_that_is_now_a_directory(self, spool) -> None:
        path = spool()
        lease = self._lease(path)
        path.unlink()
        path.mkdir()

        assert staging_leases._matching_capture_staging_path(lease) is None

    def test_refuses_a_replacement_at_the_slots_path(self, spool) -> None:
        path = spool()
        lease = self._lease(path)
        path.unlink()
        path.write_bytes(b"somebody else's bytes")

        assert staging_leases._matching_capture_staging_path(lease) is None

    def test_refuses_a_spool_whose_marker_names_another_slot(
        self, spool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = spool()
        lease = self._lease(path)
        monkeypatch.setattr(
            staging_leases.os,
            "getxattr",
            lambda _p, _n: b"a-different-slot",
            raising=False,
        )

        # The marker survives writes to the owned inode but not a replacement,
        # so a mismatch means this file is not ours even if the inode matches.
        assert staging_leases._matching_capture_staging_path(lease) is None

    def test_accepts_a_spool_whose_marker_names_it(
        self, spool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = spool()
        lease = self._lease(path)
        monkeypatch.setattr(
            staging_leases.os, "getxattr", lambda _p, _n: b"slot-1", raising=False
        )

        assert staging_leases._matching_capture_staging_path(lease) == path

    @pytest.mark.parametrize(
        "code",
        [
            pytest.param(getattr(errno, "ENOTSUP", 95), id="not-supported"),
            pytest.param(getattr(errno, "ENOSYS", 38), id="not-implemented"),
        ],
    )
    def test_falls_back_to_inode_proof_on_a_filesystem_without_xattrs(
        self, spool, monkeypatch: pytest.MonkeyPatch, code: int
    ) -> None:
        path = spool()
        lease = self._lease(path)

        def unsupported(_path: object, _name: object) -> bytes:
            raise OSError(code, "not supported")

        monkeypatch.setattr(staging_leases.os, "getxattr", unsupported, raising=False)

        # No xattrs is not a reason to stop cleaning up; device and inode still
        # prove ownership.
        assert staging_leases._matching_capture_staging_path(lease) == path

    def test_refuses_when_a_supported_marker_is_missing(
        self, spool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = spool()
        lease = self._lease(path)

        def missing(_path: object, _name: object) -> bytes:
            raise OSError(errno.ENODATA, "no such attribute")

        monkeypatch.setattr(staging_leases.os, "getxattr", missing, raising=False)

        # Fail closed: a filesystem that supports xattrs and has no marker means
        # this is a replacement, not an owned partial.
        assert staging_leases._matching_capture_staging_path(lease) is None
