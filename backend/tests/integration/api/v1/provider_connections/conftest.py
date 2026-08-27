"""Shared setup for the `/provider-connections` and `/browser-pairings` endpoints.

Both routers live in `app/api/v1/provider_connections.py` and share two pieces of state
that outlive a single test: the claim endpoint's process-wide rate limiter, and the
MyMiniFactory deployment credentials in the config overlay. Leaving either set would make
the next test's result depend on this one's, so both are reset here rather than in every
test that touches them.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.v1.provider_connections import _claim_limit
from app.core.config import _overlay

MMF_CLIENT_ID = "test-client-id"
MMF_CLIENT_SECRET = "test-client-secret"


@pytest.fixture(autouse=True)
def reset_claim_rate_limit() -> Iterator[None]:
    """The claim limiter is a process singleton; a used budget must not leak."""
    _claim_limit.limiter.reset()
    yield
    _claim_limit.limiter.reset()


@pytest.fixture
def mmf_configured() -> Iterator[None]:
    """A deployment that has MyMiniFactory OAuth credentials configured."""
    _overlay["mmf_client_id"] = MMF_CLIENT_ID
    _overlay["mmf_client_secret"] = MMF_CLIENT_SECRET
    yield
    _overlay.pop("mmf_client_id", None)
    _overlay.pop("mmf_client_secret", None)


@pytest.fixture
def pair(client: TestClient):
    """Run one full pairing: create a code as `headers`, claim it as a browser would."""

    def run(headers: dict[str, str], name: str) -> tuple[str, dict[str, object]]:
        code = client.post("/api/v1/browser-pairings", headers=headers).json()["code"]
        claimed = client.post(
            "/api/v1/browser-pairings/claim", json={"code": code, "name": name}
        )
        assert claimed.status_code == 200, claimed.text
        body = claimed.json()
        return body["credential"], body["device"]

    return run
