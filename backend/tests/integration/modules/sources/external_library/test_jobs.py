"""``sources.scan``: one Job per library owed a scan.

A library is owed a scan when someone asked for one (interactive), its
schedule boundary passed (backfill), or a dead process left it marked running.
Nothing is owed while the feature is off. Each library is its own Job, so one
unreachable NAS never stops the others being scanned. Cancelling withdraws the
request; a failed Job settles the library as failed rather than leaving it
"running"; a retry asks for the scan again.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from sqlmodel import Session, select

import app.modules.work as work
from app.core.time import ensure_utc, utcnow
from app.db.models import (
    ExternalLibrary,
    ExternalLibraryScanStatus,
    File,
    JobKind,
    WorkPriority,
)
from app.modules.sources import external_library
from app.modules.sources.external_library import ScanSource
from tests.factories import build_external_library
from tests.integration.api.v1._ingest_assertions import drain_work
from tests.integration.modules.sources.external_library._helpers import (
    drop_gcode,
    enable_feature,
)

SOURCE = ScanSource()
(DEFINITION,) = external_library.definitions()


def _reread(session: Session, library_id: int) -> ExternalLibrary:
    session.expire_all()
    library = session.get(ExternalLibrary, library_id)
    assert library is not None
    return library


def _subject(library: ExternalLibrary) -> str:
    return f"library/{library.id}"


class TestScanSource:
    def test_nothing_is_owed_while_libraries_are_off(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        build_external_library(db_session, tmp_path / "nas", name="nas", enabled=True)

        assert SOURCE.pending(db_session, now=utcnow(), limit=10) == []

    def test_a_requested_scan_is_interactive(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        enable_feature(db_session)
        library = build_external_library(
            db_session,
            tmp_path / "nas",
            name="nas",
            enabled=True,
            scan_schedule="",
            last_scanned_at=utcnow(),
            scan_requested_at=utcnow(),
        )

        (item,) = SOURCE.pending(db_session, now=utcnow(), limit=10)

        assert (item.subject_key, item.priority) == (
            _subject(library),
            WorkPriority.INTERACTIVE,
        )

    def test_a_scheduled_scan_is_backfill(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        enable_feature(db_session)
        build_external_library(db_session, tmp_path / "nas", name="nas", enabled=True)

        (item,) = SOURCE.pending(db_session, now=utcnow(), limit=10)

        assert item.priority is WorkPriority.BACKFILL

    def test_never_offers_more_than_asked(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        enable_feature(db_session)
        for name in ("a", "b", "c"):
            build_external_library(db_session, tmp_path / name, name=name, enabled=True)

        assert len(SOURCE.pending(db_session, now=utcnow(), limit=2)) == 2

    def test_is_due_at_the_next_schedule_boundary(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        enable_feature(db_session)
        now = utcnow()
        build_external_library(
            db_session,
            tmp_path / "nas",
            name="nas",
            enabled=True,
            scan_schedule="0 * * * *",
            last_scanned_at=now,
        )

        due = SOURCE.next_due(db_session, now=now)

        assert due is not None
        assert now < ensure_utc(due) <= now + timedelta(hours=1, seconds=1)


class TestScanJob:
    def test_a_requested_scan_indexes_the_library(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        enable_feature(db_session)
        root = tmp_path / "nas"
        root.mkdir()
        drop_gcode(root, "plate.gcode", marker="scan-job")
        library = build_external_library(
            db_session, root, name="nas", enabled=True, scan_requested_at=utcnow()
        )

        work.nudge(JobKind.SOURCES_SCAN)
        drain_work()

        scanned = _reread(db_session, library.id)
        assert scanned.scan_requested_at is None
        assert scanned.last_scan_status == ExternalLibraryScanStatus.OK
        names = db_session.exec(select(File.original_filename)).all()
        assert "plate.gcode" in names

    def test_one_unreachable_library_never_stops_the_others(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        enable_feature(db_session)
        reachable = tmp_path / "reachable"
        reachable.mkdir()
        drop_gcode(reachable, "kept.gcode", marker="kept")
        gone = build_external_library(
            db_session, tmp_path / "gone", name="gone", enabled=True
        )
        build_external_library(db_session, reachable, name="reachable", enabled=True)

        work.nudge(JobKind.SOURCES_SCAN)
        drain_work()

        assert "kept.gcode" in db_session.exec(select(File.original_filename)).all()
        assert _reread(db_session, gone.id).last_scan_status != (
            ExternalLibraryScanStatus.RUNNING
        )


class TestHooks:
    def test_cancelling_withdraws_the_request(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        library = build_external_library(
            db_session,
            tmp_path / "nas",
            name="nas",
            scan_requested_at=utcnow(),
            scan_requested_path="sub/dir",
        )

        DEFINITION.cancel(db_session, _subject(library))
        db_session.commit()

        withdrawn = _reread(db_session, library.id)
        assert (withdrawn.scan_requested_at, withdrawn.scan_requested_path) == (
            None,
            None,
        )

    def test_a_failed_job_settles_the_library_as_failed(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        # Left "running", the library would look busy until a stale window.
        library = build_external_library(
            db_session,
            tmp_path / "nas",
            name="nas",
            scanning=True,
            last_scan_status=ExternalLibraryScanStatus.RUNNING,
        )

        DEFINITION.on_failure(db_session, _subject(library), "boom")
        db_session.commit()

        assert _reread(db_session, library.id).last_scan_status != (
            ExternalLibraryScanStatus.RUNNING
        )

    def test_a_retry_asks_for_the_scan_again(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        library = build_external_library(db_session, tmp_path / "nas", name="nas")

        assert DEFINITION.retry(db_session, _subject(library)) is True
        db_session.commit()

        assert _reread(db_session, library.id).scan_requested_at is not None

    def test_a_library_that_is_gone_cannot_be_retried(
        self, db_session: Session
    ) -> None:
        assert DEFINITION.retry(db_session, "library/999999") is False
