"""Identity upkeep: expired refresh tokens are pruned hourly."""

from __future__ import annotations

from app.modules.work.sources import fixed, scheduled

from .auth import prune_expired_refresh_tokens


def definitions():
    return [
        scheduled(
            "identity.retention",
            cron=fixed("45 * * * *"),
            run=prune_expired_refresh_tokens,
            label="Session token retention",
        )
    ]
