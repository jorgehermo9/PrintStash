"""Identity upkeep: expired refresh tokens are pruned hourly."""

from __future__ import annotations

from app.db.models import JobKind
from app.modules.work.sources import fixed, scheduled

from .auth import prune_expired_refresh_tokens


def definitions():
    return [
        scheduled(
            JobKind.IDENTITY_RETENTION,
            cron=fixed("45 * * * *"),
            run=prune_expired_refresh_tokens,
            label="Session token retention",
        )
    ]
