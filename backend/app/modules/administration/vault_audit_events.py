"""Durable scheduled-audit event publication, separate from metric projection."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlmodel import Session, select

from app.core.time import ensure_utc
from app.db.models import (
    NotificationEventType,
    VaultAuditEvent,
    VaultAuditPolicy,
)
from app.modules.notifications.notifications import enqueue_storage_event


def record_overdue(
    session: Session, policy: VaultAuditPolicy, *, now: datetime
) -> None:
    """Commit one durable overdue event without advancing the missed slot."""
    if policy.next_due_at is None or now <= ensure_utc(policy.next_due_at) + timedelta(
        minutes=policy.max_lateness_minutes
    ):
        return
    slot = ensure_utc(policy.next_due_at).isoformat()
    key = f"policy:{policy.mode}:{policy.revision}:{slot}:overdue"
    if session.exec(
        select(VaultAuditEvent).where(VaultAuditEvent.dedup_key == key)
    ).first():
        return
    event_type = NotificationEventType.STORAGE_AUDIT_OVERDUE
    session.add(
        VaultAuditEvent(
            dedup_key=key,
            event_type=event_type.value,
            summary_json=json.dumps({"mode": policy.mode, "scheduled_for": slot}),
        )
    )
    if policy.notification_threshold != "off":
        deliveries = enqueue_storage_event(
            session,
            event_type,
            run_id=None,
            mode=policy.mode,
            summary={},
            channel_ids=json.loads(policy.notification_channels_json),
        )
        if deliveries:
            due = (
                max(
                    now,
                    ensure_utc(policy.last_notified_at)
                    + timedelta(minutes=policy.notification_cooldown_minutes),
                )
                if policy.last_notified_at
                else now
            )
            for row in deliveries:
                row.next_retry_at = due
            policy.last_notified_at = due
            session.add(policy)
    session.commit()
