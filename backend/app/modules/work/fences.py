"""Database fences: leases that coordinate work across every process.

A fence is one ``work_fences`` row. An exclusive fence (``restore``,
``backup``) has a fixed name and one holder. A shared fence (a storage
retention, a destructive storage operation) gets a unique name under a prefix,
so any number can be held at once and a checker asks whether any live one
exists. Every fence expires on its own: its holder heartbeats, and a crashed
holder's fence stops counting at ``expires_at``. Nothing here overrides a
filesystem recovery journal; that remains the authority for an interrupted
restore (see ``app.modules.storage.migration_journal``).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.time import ensure_utc, utcnow
from app.db.affected import affected
from app.db.models import WorkFence
from app.db.session import get_session_factory

RESTORE = "restore"
BACKUP = "backup"
RETENTION_PREFIX = "retain:"
DESTRUCTIVE_PREFIX = "destroy:"


class FenceHeld(Exception):
    """An exclusive fence is already held by another live holder."""

    def __init__(self, name: str, holder: str) -> None:
        super().__init__(f"{name} held by {holder}")
        self.name = name
        self.holder = holder


def _ttl() -> timedelta:
    return timedelta(seconds=settings.fence_ttl_seconds)


def _sweep(session: Session, now: datetime) -> None:
    session.execute(delete(WorkFence).where(col(WorkFence.expires_at) <= now))


def acquire(
    name: str, *, holder: str, reason: str, now: datetime | None = None
) -> WorkFence:
    """Take an exclusive fence, replacing an expired one; raise if live."""
    now = now or utcnow()
    with get_session_factory().scoped_session() as session:
        _sweep(session, now)
        existing = session.get(WorkFence, name)
        if existing is not None:
            if existing.holder == holder:
                existing.heartbeat_at = now
                existing.expires_at = now + _ttl()
                session.add(existing)
                session.commit()
                session.refresh(existing)
                return existing
            session.rollback()
            raise FenceHeld(name, existing.holder)
        fence = WorkFence(
            name=name,
            holder=holder,
            reason=reason[:64],
            acquired_at=now,
            heartbeat_at=now,
            expires_at=now + _ttl(),
        )
        session.add(fence)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            winner = session.get(WorkFence, name)
            raise FenceHeld(name, winner.holder if winner else "unknown") from exc
        session.refresh(fence)
        return fence


def acquire_shared(prefix: str, *, holder: str, reason: str) -> str:
    """Take one of any number of fences under ``prefix``; returns its name."""
    name = f"{prefix}{uuid.uuid4().hex}"
    now = utcnow()
    with get_session_factory().scoped_session() as session:
        session.add(
            WorkFence(
                name=name,
                holder=holder,
                reason=reason[:64],
                acquired_at=now,
                heartbeat_at=now,
                expires_at=now + _ttl(),
            )
        )
        session.commit()
    return name


def release(name: str, *, holder: str) -> bool:
    with get_session_factory().scoped_session() as session:
        removed = affected(
            session,
            delete(WorkFence).where(
                col(WorkFence.name) == name, col(WorkFence.holder) == holder
            ),
        )
        session.commit()
    return bool(removed)


def heartbeat(holder: str, *, now: datetime | None = None) -> int:
    """Extend every fence ``holder`` owns; returns how many it renewed."""
    now = now or utcnow()
    with get_session_factory().scoped_session() as session:
        renewed = affected(
            session,
            update(WorkFence)
            .where(col(WorkFence.holder) == holder, col(WorkFence.expires_at) > now)
            .values(heartbeat_at=now, expires_at=now + _ttl()),
        )
        session.commit()
    return renewed


def get(name: str, *, now: datetime | None = None) -> WorkFence | None:
    now = now or utcnow()
    with get_session_factory().scoped_session() as session:
        fence = session.get(WorkFence, name)
        if fence is None or ensure_utc(fence.expires_at) <= now:
            return None
        session.expunge(fence)
        return fence


def is_held(name: str, *, now: datetime | None = None) -> bool:
    return get(name, now=now) is not None


def any_held(
    prefix: str, *, except_holder: str | None = None, now: datetime | None = None
) -> bool:
    now = now or utcnow()
    with get_session_factory().scoped_session() as session:
        statement = select(WorkFence.name).where(
            col(WorkFence.name).startswith(prefix), col(WorkFence.expires_at) > now
        )
        if except_holder is not None:
            statement = statement.where(WorkFence.holder != except_holder)
        return session.exec(statement.limit(1)).first() is not None


def held_by(holder: str) -> list[str]:
    with get_session_factory().scoped_session() as session:
        return list(
            session.exec(select(WorkFence.name).where(WorkFence.holder == holder)).all()
        )
