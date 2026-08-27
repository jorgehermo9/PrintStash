"""``tests/integration/`` — the real app in this process, no egress.

The default tier. SQLite with the production pragmas, real routers, real services, real
storage backend, real RBAC, real fixture files. The only things stood in for are the
outbound boundaries, and only by injection or by patching ``get_http_client`` where it is
used. The socket guard below makes that structural: a real connection fails the test, so a
test that needs one is a contract test and belongs in ``tests/contract/``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Protocol

import pytest
from sqlmodel import Session

from tests._guards import block_real_network  # noqa: F401 — autouse

if TYPE_CHECKING:
    from app.db.models import User


class MakeUser(Protocol):
    """Signature of the ``make_user`` factory."""

    def __call__(
        self,
        username: str,
        *,
        is_superuser: bool = False,
        password: str = "Password123",
    ) -> User: ...


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
def make_user(db_session: Session) -> MakeUser:
    """Create and return a user row. Create-or-fail: a repeat name is a collision."""
    from app.db.models import User
    from app.services.auth import hash_password

    def make(
        username: str,
        *,
        is_superuser: bool = False,
        password: str = "Password123",
    ) -> User:
        user = User(
            username=username,
            hashed_password=hash_password(password),
            is_active=True,
            is_superuser=is_superuser,
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        return user

    return make


@pytest.fixture
def headers_for() -> Callable[..., dict[str, str]]:
    """Bearer headers for an existing user, at the scope you name."""
    from app.services.auth import create_access_token

    def build(user: User, *, scope: str = "write") -> dict[str, str]:
        token = create_access_token(user.id, user.username, scope=scope)
        return {"Authorization": f"Bearer {token}"}

    return build


@pytest.fixture
def user_headers(make_user: MakeUser, headers_for) -> UserHeaders:
    """Bearer headers for a user who is *not* the suite's superuser.

    ``auth_headers`` is an admin-scoped superuser, which proves nothing about the
    403 half of an endpoint's contract. Every RBAC and scope row needs the other
    side, and hand-rolling one per file drifted: this returns a fresh user each
    call, so two identities in one test are two calls. When the test also needs the
    row (to grant it a collection role, say), take ``make_user`` and ``headers_for``
    instead.
    """

    def make(
        username: str,
        *,
        is_superuser: bool = False,
        scope: str = "write",
        password: str = "Password123",
    ) -> dict[str, str]:
        user = make_user(username, is_superuser=is_superuser, password=password)
        return headers_for(user, scope=scope)

    return make


@pytest.fixture
def backup_env(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """A file-based vault the backup service can read and rewrite as files.

    Shared by `services/test_backup.py` and `api/v1/test_backup.py`; the harness itself
    is `tests/integration/_backup_harness.py`.
    """
    from tests.integration._backup_harness import build_backup_env

    yield from build_backup_env(tmp_path, monkeypatch)


__all__ = [
    "MakeUser",
    "UserHeaders",
    "backup_env",
    "block_real_network",
    "headers_for",
    "make_user",
    "user_headers",
]
