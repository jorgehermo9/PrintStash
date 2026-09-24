"""Explicitly advance durable projections in tests that require indexed content."""

import asyncio

from app.modules.search.projection import process_pending


def drain_search(session):
    session.commit()
    for _ in range(2048):
        worked = process_pending(session)
        session.commit()
        if not worked:
            session.expire_all()
            return
    raise AssertionError("search projection did not become idle")


def _search_round() -> bool:
    """One unit of what the ``search.project`` and ``search.index`` Jobs run."""
    from app.db.session import get_session_factory
    from app.modules.search.jobs import _index_unit

    with get_session_factory().scoped_session() as session:
        projected = process_pending(session)
        session.commit()
    return bool(projected) | _index_unit()


async def run_search_units() -> None:
    """Keep search work moving while a test talks HTTP to the same vault.

    For tests about search behaviour under concurrent reads, not about
    scheduling: it runs the Jobs' units back to back, the way a busy search
    lane would, until cancelled.
    """
    while True:
        worked = await asyncio.to_thread(_search_round)
        await asyncio.sleep(0 if worked else 0.05)
