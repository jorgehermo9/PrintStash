"""Follow background work through the public API, the way a client does.

Every route that accepts background work answers 202 with a ``job_id``; the
client then follows ``GET /api/v1/jobs/{job_id}``. These helpers run the queued
work to convergence first (the E2E engine is the inline one), so a flow asserts
the settled outcome instead of polling with sleeps.
"""

from __future__ import annotations

from typing import Any


def settle() -> int:
    """Run every queued Job, and every Job those nudged, to completion."""
    from app.modules.work.catalog import get_engine

    return get_engine().drain()  # type: ignore[attr-defined]


async def finished_job(api, response, headers: dict[str, str]) -> dict[str, Any]:
    """Settle the Job an accepted request queued and return its final status."""
    assert response.status_code == 202, response.text
    settle()
    job = await api.get(f"/api/v1/jobs/{response.json()['job_id']}", headers=headers)
    assert job.status_code == 200, job.text
    return job.json()


async def completed_job(api, response, headers: dict[str, str]) -> dict[str, Any]:
    """``finished_job``, asserting the Job completed."""
    payload = await finished_job(api, response, headers)
    assert payload["state"] == "completed", payload
    return payload


async def create_backup(api, headers: dict[str, str]) -> dict[str, Any]:
    """Take a manual backup through the API and return the new backup's metadata."""
    job = await completed_job(
        api, await api.post("/api/v1/backups", headers=headers), headers
    )
    return job["result"]
