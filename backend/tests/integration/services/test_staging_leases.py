from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, delete, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from alembic import command
from app.core.time import utcnow
from app.db.models import BackgroundJob, InboxItem, StagingLease, User
from app.services import staging_leases
from tests.factories import build_user
from tests.paths import ALEMBIC_DIR, ALEMBIC_INI


def _inbox(session: Session, user: User) -> InboxItem:
    row = InboxItem(owner_user_id=user.id)
    session.add(row)
    session.flush()
    return row


def _job(session: Session, user: User) -> BackgroundJob:
    row = BackgroundJob(id="lease-job", owner_user_id=user.id)
    session.add(row)
    session.flush()
    return row


def test_review_lease_rejects_replaced_path_without_unlink(
    db_session: Session, tmp_path: Path
) -> None:
    user = build_user(db_session, "lease-user")
    inbox = _inbox(db_session, user)
    staged = tmp_path / "capture.3mf"
    staged.write_bytes(b"original")
    lease = staging_leases.create_review_lease(
        db_session,
        inbox_item_id=inbox.id,
        owner_user_id=user.id,
        path=staged,
        size_bytes=8,
        sha256="a" * 64,
    )
    db_session.commit()
    staged.unlink()
    staged.write_bytes(b"replacement")
    assert (
        staging_leases.dismiss_review_lease(db_session, inbox_item_id=inbox.id) is False
    )
    assert staged.read_bytes() == b"replacement"
    # The receipt is stale, so it no longer owns the replacement and releases
    # only its DB accounting; critically, the replacement remains untouched.
    assert db_session.get(StagingLease, lease.id) is None


def test_fc15_upgrade_and_downgrade_preserve_job_lease_data(tmp_path: Path) -> None:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    url = f"sqlite:///{tmp_path / 'staging-lease.sqlite'}"
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "fb14d5e8a7c3")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO background_jobs "
                "(id, visible, kind, state, status_json, replay_safe, attempts, created_at, updated_at) "
                "VALUES ('old-job', 1, 'ingest', 'pending', '{}', 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO staging_leases "
                "(id, path, background_job_id, size_bytes, sha256, expires_at, created_at) "
                "VALUES ('old-lease', '/tmp/old', 'old-job', 1, :sha, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"sha": "f" * 64},
        )
    command.upgrade(config, "fc15a6e9b8d4")
    inspector = inspect(engine)
    columns = {
        column["name"]: column for column in inspector.get_columns("staging_leases")
    }
    assert columns["background_job_id"]["nullable"] is True
    assert "inbox_item_id" in columns
    assert "ix_staging_leases_inbox_item_id" in {
        index["name"] for index in inspector.get_indexes("staging_leases")
    }
    with engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT background_job_id FROM staging_leases WHERE id = 'old-lease'"
                )
            ).scalar_one()
            == "old-job"
        )
    command.downgrade(config, "fb14d5e8a7c3")
    with engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT background_job_id FROM staging_leases WHERE id = 'old-lease'"
                )
            ).scalar_one()
            == "old-job"
        )
    assert "inbox_item_id" not in {
        column["name"] for column in inspect(engine).get_columns("staging_leases")
    }
    engine.dispose()


def test_transfer_is_atomic_and_preserves_exactly_one_owner(
    db_session: Session, tmp_path: Path
) -> None:
    user = build_user(db_session, "lease-user")
    inbox = _inbox(db_session, user)
    staged = tmp_path / "capture.stl"
    staged.write_bytes(b"staged")
    staging_leases.create_review_lease(
        db_session,
        inbox_item_id=inbox.id,
        owner_user_id=user.id,
        path=staged,
        size_bytes=6,
        sha256="c" * 64,
    )
    with pytest.raises(staging_leases.StagingLeaseError):
        staging_leases.transfer_inbox_to_job(
            db_session, inbox_item_id=inbox.id, job_id="missing"
        )
    lease = db_session.exec(
        select(StagingLease).where(StagingLease.inbox_item_id == inbox.id)
    ).one()
    assert lease.background_job_id is None
    job = _job(db_session, user)
    transferred = staging_leases.transfer_inbox_to_job(
        db_session, inbox_item_id=inbox.id, job_id=job.id
    )
    assert transferred.inbox_item_id is None
    assert transferred.background_job_id == job.id
    db_session.commit()
    with pytest.raises(IntegrityError):
        db_session.add(
            StagingLease(
                id="invalid-owner",
                path="/tmp/invalid",
                size_bytes=1,
                sha256="d" * 64,
                expires_at=utcnow(),
            )
        )
        db_session.commit()
    db_session.rollback()


def test_inbox_delete_cascades_review_lease(
    db_session: Session, tmp_path: Path
) -> None:
    user = build_user(db_session, "lease-user")
    inbox = _inbox(db_session, user)
    staged = tmp_path / "capture.obj"
    staged.write_bytes(b"staged")
    lease = staging_leases.create_review_lease(
        db_session,
        inbox_item_id=inbox.id,
        owner_user_id=user.id,
        path=staged,
        size_bytes=6,
        sha256="e" * 64,
    )
    lease_id = lease.id
    db_session.commit()
    db_session.connection().exec_driver_sql("PRAGMA foreign_keys=ON")
    db_session.exec(delete(InboxItem).where(InboxItem.id == inbox.id))
    db_session.commit()
    assert db_session.get(StagingLease, lease_id) is None


class TestPruneExpired:
    def test_prune_expired_unlinks_exact_file(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        user = build_user(db_session, "lease-user")
        inbox = _inbox(db_session, user)
        staged = tmp_path / "capture.gcode"
        staged.write_bytes(b"staged")
        lease = staging_leases.create_review_lease(
            db_session,
            inbox_item_id=inbox.id,
            owner_user_id=user.id,
            path=staged,
            size_bytes=6,
            sha256="b" * 64,
        )
        lease.expires_at = utcnow() - timedelta(seconds=1)
        db_session.commit()
        assert staging_leases.prune_expired(db_session) == (1, 1)
        db_session.commit()
        assert not staged.exists()
        assert db_session.get(StagingLease, lease.id) is None
