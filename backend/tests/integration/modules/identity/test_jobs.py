"""``identity.retention``: expired refresh tokens are pruned every hour.

A refresh token past its expiry can never be exchanged again, so keeping it
only grows the table every login writes to. The schedule runs whether or not
setup has finished (a token can exist before it), and the Job records how many
it pruned so the admin page shows what each occurrence did.
"""

from __future__ import annotations

import json
from datetime import timedelta

from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import Job, RefreshToken
from app.modules.identity import jobs as identity_jobs
from app.modules.work.submission import submit

(RETENTION,) = identity_jobs.definitions()


def _token(session: Session, user, *, expires_in: timedelta, name: str) -> None:
    session.add(
        RefreshToken(
            user_id=user.id,
            token_hash=name.ljust(64, "0"),
            expires_at=utcnow() + expires_in,
        )
    )
    session.commit()


class TestAuthRetention:
    def test_runs_every_hour_without_waiting_for_setup(
        self, db_session: Session
    ) -> None:
        assert RETENTION.source is not None
        assert RETENTION.source.cron(db_session) == "45 * * * *"  # type: ignore[attr-defined]

    def test_prunes_only_expired_tokens(
        self, db_session: Session, work_engine, make_user, make_job
    ) -> None:
        user = make_user()
        _token(db_session, user, expires_in=-timedelta(minutes=1), name="expired")
        _token(db_session, user, expires_in=timedelta(days=1), name="live")
        job = make_job(kind=RETENTION.name, subject=f"{RETENTION.name}@now")

        submit(job.id)
        work_engine.run_one()

        db_session.expire_all()
        remaining = db_session.exec(select(RefreshToken.token_hash)).all()
        assert [token.rstrip("0") for token in remaining] == ["live"]
        row = db_session.get(Job, job.id)
        assert row is not None
        assert json.loads(row.status_json)["result"] == {"outcome": 1}
