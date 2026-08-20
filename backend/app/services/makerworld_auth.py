"""OSS composition root for the framework-neutral MakerWorld login client.

The provider protocol lives in :mod:`printstash_core.imports.makerworld`. This
facade preserves the original service API while selecting the OSS defaults: the
shared application HTTP pool, wall-clock time, random UUIDs, application logging,
and a process-local pending-login store. A deployment that needs cross-process
or restart-safe verification can supply a durable ``PendingLoginStore`` at this
same composition boundary without changing the protocol client.
"""

from __future__ import annotations

import time
import uuid
from typing import cast

from printstash_core.imports.makerworld import (
    BROWSER_USER_AGENT,
    DEFAULT_PENDING_TTL,
    DEFAULT_TIMEOUT,
    LOGIN_URL,
    TFA_URL,
    Clock,
    HttpClient,
    InMemoryPendingLoginStore,
    LoginResult,
    MakerWorldAuthClient,
    MakerWorldAuthError,
    PendingLoginStore,
    UuidFactory,
    WarningLogger,
)

from app.core.http_client import get_http_client
from app.core.logging import get_logger

logger = get_logger(__name__)

# Compatibility aliases for existing diagnostics/tests that referenced the
# original module constants. The supported service API remains the two async
# functions plus LoginResult and MakerWorldAuthError.
_LOGIN_URL = LOGIN_URL
_TFA_URL = TFA_URL
_BROWSER_UA = BROWSER_USER_AGENT
_TIMEOUT = DEFAULT_TIMEOUT
_PENDING_TTL = DEFAULT_PENDING_TTL

_pending_store: PendingLoginStore = InMemoryPendingLoginStore()
_clock: Clock = time.time
_uuid_factory: UuidFactory = uuid.uuid4


def _client() -> MakerWorldAuthClient:
    return MakerWorldAuthClient(
        http=cast(HttpClient, get_http_client()),
        pending_store=_pending_store,
        clock=_clock,
        uuid_factory=_uuid_factory,
        logger=cast(WarningLogger, logger),
        pending_ttl=_PENDING_TTL,
        timeout=_TIMEOUT,
    )


async def begin_login(account: str, password: str) -> LoginResult:
    """Start a MakerWorld login using the OSS application's dependencies."""
    return await _client().begin_login(account, password)


async def submit_code(login_token: str, code: str) -> LoginResult:
    """Complete a pending MakerWorld login using the OSS pending-state store."""
    return await _client().submit_code(login_token, code)


__all__ = [
    "LoginResult",
    "MakerWorldAuthError",
    "begin_login",
    "submit_code",
]
