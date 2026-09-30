"""The automatic backup claim is durable across scheduler ticks and failures.

The claim is what lets the ``backups.automatic`` schedule be resubmitted after a
crash without archiving twice in one day; the Job itself is defended in
``test_jobs.py``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Session

from app.db.models import SystemConfig
from app.modules.backups import backup_schedule


class TestClaimDueBackup:
    def test_claims_a_due_day_once(
        self, db_session: Session, make_system_config
    ) -> None:
        make_system_config(
            automatic_backups_enabled=True,
            automatic_backup_time_utc="02:00",
        )
        now = datetime(2026, 9, 3, 10, 0, tzinfo=timezone.utc)

        first = backup_schedule.claim_due_backup(db_session, now=now)
        second = backup_schedule.claim_due_backup(db_session, now=now)

        assert first is True
        assert second is False
        stored = db_session.get(SystemConfig, 1)
        assert stored is not None
        assert stored.automatic_backup_last_attempt_at == now.replace(tzinfo=None)
