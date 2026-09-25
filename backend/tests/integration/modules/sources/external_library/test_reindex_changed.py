"""`_reindex_changed` refusing to record a signature it did not finish writing.

The scan skips unchanged files by comparing the stored size and mtime, so the
stored signature is a *claim* that what was derived from the row matches what is
on disk. Confirming the new signature without invalidating the derivatives of
the old bytes inverts that: the file's metadata and thumbnail describe content
that is gone, and every future scan skips it because the signature says it is up
to date. Stale derivatives that no amount of rescanning can fix are worse than a
failed scan.

So the new hash and the invalidation land in one commit, and a failure in
between leaves the old signature intact so the next scan tries again.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.db.models import DerivativeKind, DerivativeState, File
from app.modules.derivatives import records
from app.modules.sources import external_library
from app.runtime.engine.inline import InlineJobEngine
from tests._env import use_local_storage
from tests.factories import build_external_library
from tests.integration.modules.sources.external_library._helpers import (
    drop_gcode,
)


def _indexed(tmp_path: Path, session: Session) -> tuple[Path, File]:
    use_local_storage(tmp_path)
    nas = tmp_path / "nas"
    path = drop_gcode(nas, "atomic.gcode")
    lib = build_external_library(session, nas, name="nas")
    external_library.scan_library(lib.id)
    file_row = session.exec(
        select(File).where(File.original_filename == "atomic.gcode")
    ).one()
    return path, file_row


def _edit(path: Path) -> None:
    with path.open("ab") as handle:
        handle.write(b"\n; changed for atomicity test\n")


class TestReindexChanged:
    def test_forgets_the_derivatives_of_the_old_bytes(
        self,
        tmp_path: Path,
        db_session: Session,
        work_engine: InlineJobEngine,
        make_derivative,
    ) -> None:
        path, file_row = _indexed(tmp_path, db_session)
        make_derivative(file_row, DerivativeKind.METADATA, state=DerivativeState.READY)
        _edit(path)
        stat = path.stat()

        external_library._reindex_changed(
            db_session, file_row, path, stat.st_size, stat.st_mtime
        )

        assert DerivativeKind.METADATA not in records.rows_for(db_session, file_row)

    def test_rederives_the_changed_bytes(
        self, tmp_path: Path, db_session: Session, work_engine: InlineJobEngine
    ) -> None:
        path, file_row = _indexed(tmp_path, db_session)
        work_engine.drain()
        _edit(path)
        stat = path.stat()

        external_library._reindex_changed(
            db_session, file_row, path, stat.st_size, stat.st_mtime
        )
        work_engine.drain()

        db_session.expire_all()
        row = records.rows_for(db_session, db_session.get(File, file_row.id))[
            DerivativeKind.METADATA
        ]
        assert row.state == DerivativeState.READY

    def test_keeps_the_old_signature_when_invalidation_fails(
        self,
        tmp_path: Path,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        path, file_row = _indexed(tmp_path, db_session)
        old_hash = file_row.sha256
        old_size = file_row.size_bytes
        _edit(path)
        stat = path.stat()

        def fail_invalidate(*_args, **_kwargs) -> int:
            raise RuntimeError("invalidation_failed")

        monkeypatch.setattr(records, "invalidate", fail_invalidate)
        with pytest.raises(RuntimeError, match="invalidation_failed"):
            external_library._reindex_changed(
                db_session, file_row, path, stat.st_size, stat.st_mtime
            )
        db_session.rollback()
        db_session.refresh(file_row)

        assert (file_row.sha256, file_row.size_bytes) == (old_hash, old_size)

    def test_only_records_the_signature_when_just_the_mtime_moved(
        self, tmp_path: Path, db_session: Session, make_derivative
    ) -> None:
        path, file_row = _indexed(tmp_path, db_session)
        make_derivative(file_row, DerivativeKind.METADATA, state=DerivativeState.READY)
        stat = path.stat()

        changed = external_library._reindex_changed(
            db_session, file_row, path, stat.st_size, stat.st_mtime + 60
        )

        assert changed is False
        assert DerivativeKind.METADATA in records.rows_for(db_session, file_row)
