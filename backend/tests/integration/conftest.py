"""``tests/integration/`` — the real app in this process, no egress.

The default tier. SQLite with the production pragmas, real routers, real services, real
storage backend, real RBAC, real fixture files. The only things stood in for are the
outbound boundaries, and only by injection or by patching ``get_http_client`` where it is
used. The socket guard below makes that structural: a real connection fails the test, so a
test that needs one is a contract test and belongs in ``tests/contract/``.
"""

from __future__ import annotations

from typing import Protocol

import pytest
from sqlmodel import Session

from tests._guards import block_real_network  # noqa: F401 — autouse


class UserHeaders(Protocol):
    """Signature of the ``user_headers`` factory."""

    def __call__(
        self,
        username: str,
        *,
        is_superuser: bool = False,
        scope: str = "write",
        password: str = "Password123",
    ) -> dict[str, str]: ...


@pytest.fixture
def user_headers(db_session: Session) -> UserHeaders:
    """Bearer headers for a user who is *not* the suite's superuser.

    ``auth_headers`` is an admin-scoped superuser, which proves nothing about the
    403 half of an endpoint's contract. Every RBAC and scope row needs the other
    side, and hand-rolling one per file drifted: this returns a fresh user each
    call, so two identities in one test are two calls.
    """
    from app.db.models import User
    from app.services.auth import create_access_token, hash_password

    def make(
        username: str,
        *,
        is_superuser: bool = False,
        scope: str = "write",
        password: str = "Password123",
    ) -> dict[str, str]:
        user = User(
            username=username,
            hashed_password=hash_password(password),
            is_active=True,
            is_superuser=is_superuser,
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        token = create_access_token(user.id, user.username, scope=scope)
        return {"Authorization": f"Bearer {token}"}

    return make


__all__ = ["UserHeaders", "block_real_network", "user_headers"]
