"""Stateless MakerWorld login protocol.

MakerWorld authenticates downloads with a Bambu account JWT. The protocol can
complete immediately or require an emailed/authenticator code. This module owns
that provider-specific exchange without choosing an HTTP implementation or a
place to persist the short-lived pending login.

Applications must provide a :class:`PendingLoginStore`. A durable implementation
may back it with a database or shared cache as long as it honors the documented
expiry and deletion semantics; the core client never relies on process globals.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, cast
from uuid import UUID

LOGIN_URL = "https://api.bambulab.com/v1/user-service/user/login"
TFA_URL = "https://bambulab.com/api/sign-in/tfa"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 30.0
DEFAULT_PENDING_TTL = 600.0


class MakerWorldAuthError(Exception):
    """Login failed; ``code`` is a stable identifier for API/UI consumers."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


@dataclass
class LoginResult:
    """Outcome of a login step.

    ``status`` is ``ok``, ``need_email_code``, or ``need_tfa_code``. Completed
    logins carry ``token``; pending logins carry the opaque ``login_token``.
    """

    status: str
    token: str | None = None
    login_token: str | None = None


@dataclass(frozen=True)
class PendingLogin:
    """Minimum state needed to complete a login; passwords are never retained."""

    account: str
    tfa_key: str | None
    created_at: float


class PendingLoginStore(Protocol):
    """Persistence boundary for pending MakerWorld login challenges.

    Implementations may be in-memory or durable/shared. Methods must be safe for
    concurrent callers, ``get`` must not extend a record's lifetime, and
    ``delete``/``prune`` must be idempotent. ``prune`` removes records whose
    ``created_at`` is strictly less than ``created_before``. The client deletes a
    record only after successful verification (or expiry), so transient and
    invalid-code failures remain retryable until the original TTL elapses.
    """

    async def save(self, login_token: str, pending: PendingLogin) -> None: ...

    async def get(self, login_token: str) -> PendingLogin | None: ...

    async def delete(self, login_token: str) -> None: ...

    async def prune(self, *, created_before: float) -> None: ...


class InMemoryPendingLoginStore:
    """Process-local pending-login store for single-process applications."""

    def __init__(self) -> None:
        self._entries: dict[str, PendingLogin] = {}

    async def save(self, login_token: str, pending: PendingLogin) -> None:
        self._entries[login_token] = pending

    async def get(self, login_token: str) -> PendingLogin | None:
        return self._entries.get(login_token)

    async def delete(self, login_token: str) -> None:
        self._entries.pop(login_token, None)

    async def prune(self, *, created_before: float) -> None:
        expired = [
            token
            for token, pending in self._entries.items()
            if pending.created_at < created_before
        ]
        for token in expired:
            self._entries.pop(token, None)


class ResponseCookies(Protocol):
    def get(self, key: str, default: str | None = None) -> str | None: ...


class HttpResponse(Protocol):
    status_code: int
    cookies: ResponseCookies

    def json(self) -> object: ...


class HttpClient(Protocol):
    async def post(
        self,
        url: str,
        *,
        json: Mapping[str, object],
        headers: Mapping[str, str],
        timeout: float,
    ) -> HttpResponse: ...


class WarningLogger(Protocol):
    def warning(self, message: str, *args: object) -> None: ...


Clock = Callable[[], float]
UuidFactory = Callable[[], UUID]


class MakerWorldAuthClient:
    """MakerWorld login exchange with all effects supplied by the application."""

    def __init__(
        self,
        *,
        http: HttpClient,
        pending_store: PendingLoginStore,
        clock: Clock,
        uuid_factory: UuidFactory,
        logger: WarningLogger,
        pending_ttl: float = DEFAULT_PENDING_TTL,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._http = http
        self._pending_store = pending_store
        self._clock = clock
        self._uuid_factory = uuid_factory
        self._logger = logger
        self._pending_ttl = pending_ttl
        self._timeout = timeout

    async def begin_login(self, account: str, password: str) -> LoginResult:
        """Start a login, returning a JWT or an opaque pending-login token."""
        account = (account or "").strip()
        if not account or not password:
            raise MakerWorldAuthError("missing_credentials")

        try:
            response = await self._http.post(
                LOGIN_URL,
                json={"account": account, "password": password, "apiError": ""},
                headers=_headers(),
                timeout=self._timeout,
            )
        except Exception as exc:  # noqa: BLE001 -- provider network boundary
            self._logger.warning("bambu login request failed: %s", exc)
            raise MakerWorldAuthError("network_error") from exc

        if response.status_code in (401, 403):
            raise MakerWorldAuthError("invalid_credentials")
        if response.status_code != 200:
            raise MakerWorldAuthError(
                "login_failed", f"HTTP {response.status_code}"
            )

        data = _response_mapping(
            response, error_code="login_failed", error_message="non-JSON response"
        )
        token = _token_from_payload(data)
        if token:
            return LoginResult(status="ok", token=token)

        login_type = str(data.get("loginType") or "").lower()
        raw_tfa_key = data.get("tfaKey") or None
        tfa_key = str(raw_tfa_key) if raw_tfa_key is not None else None
        if login_type == "tfa" or tfa_key:
            login_token = await self._stash(account=account, tfa_key=tfa_key)
            return LoginResult(status="need_tfa_code", login_token=login_token)
        if login_type in ("verifycode", "verify_code", "email"):
            login_token = await self._stash(account=account, tfa_key=None)
            return LoginResult(status="need_email_code", login_token=login_token)

        self._logger.warning(
            "bambu login returned no token and no known loginType: %r", login_type
        )
        login_token = await self._stash(account=account, tfa_key=None)
        return LoginResult(status="need_email_code", login_token=login_token)

    async def submit_code(self, login_token: str, code: str) -> LoginResult:
        """Complete a pending email/authenticator challenge."""
        code = (code or "").strip()
        if not code:
            raise MakerWorldAuthError("missing_code")

        await self._prune()
        pending = await self._pending_store.get(login_token)
        if pending is None:
            raise MakerWorldAuthError("login_expired")

        token = (
            await self._submit_tfa(pending, code)
            if pending.tfa_key
            else await self._submit_email_code(pending, code)
        )
        await self._pending_store.delete(login_token)
        return LoginResult(status="ok", token=token)

    async def _stash(self, *, account: str, tfa_key: str | None) -> str:
        now = self._clock()
        await self._pending_store.prune(
            created_before=now - self._pending_ttl
        )
        login_token = self._uuid_factory().hex
        await self._pending_store.save(
            login_token,
            PendingLogin(account=account, tfa_key=tfa_key, created_at=now),
        )
        return login_token

    async def _prune(self) -> None:
        await self._pending_store.prune(
            created_before=self._clock() - self._pending_ttl
        )

    async def _submit_email_code(self, pending: PendingLogin, code: str) -> str:
        try:
            response = await self._http.post(
                LOGIN_URL,
                json={"account": pending.account, "code": code},
                headers=_headers(),
                timeout=self._timeout,
            )
        except Exception as exc:  # noqa: BLE001 -- provider network boundary
            self._logger.warning("bambu code submit failed: %s", exc)
            raise MakerWorldAuthError("network_error") from exc

        if response.status_code != 200:
            raise MakerWorldAuthError(
                "invalid_code", f"HTTP {response.status_code}"
            )
        data = _response_mapping(
            response, error_code="invalid_code", error_message="non-JSON response"
        )
        token = _token_from_payload(data)
        if not token:
            raise MakerWorldAuthError("invalid_code")
        return token

    async def _submit_tfa(self, pending: PendingLogin, code: str) -> str:
        try:
            response = await self._http.post(
                TFA_URL,
                json={"tfaCode": code, "tfaKey": pending.tfa_key},
                headers=_headers(),
                timeout=self._timeout,
            )
        except Exception as exc:  # noqa: BLE001 -- provider network boundary
            self._logger.warning("bambu tfa submit failed: %s", exc)
            raise MakerWorldAuthError("network_error") from exc

        if response.status_code != 200:
            raise MakerWorldAuthError(
                "invalid_code", f"HTTP {response.status_code}"
            )
        token = response.cookies.get("token")
        if not token:
            try:
                token = _token_from_payload(
                    _response_mapping(
                        response,
                        error_code="invalid_code",
                        error_message="non-JSON response",
                    )
                )
            except MakerWorldAuthError:
                token = None
        if not token:
            raise MakerWorldAuthError("invalid_code")
        return token


def _headers() -> dict[str, str]:
    return {
        "User-Agent": BROWSER_USER_AGENT,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _response_mapping(
    response: HttpResponse, *, error_code: str, error_message: str
) -> Mapping[str, object]:
    try:
        data = response.json()
    except ValueError as exc:
        raise MakerWorldAuthError(error_code, error_message) from exc
    if not isinstance(data, Mapping):
        raise MakerWorldAuthError(error_code, error_message)
    return cast(Mapping[str, object], data)


def _token_from_payload(data: Mapping[str, object]) -> str | None:
    for key in ("accessToken", "access_token", "token"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


__all__ = [
    "BROWSER_USER_AGENT",
    "Clock",
    "DEFAULT_PENDING_TTL",
    "DEFAULT_TIMEOUT",
    "HttpClient",
    "HttpResponse",
    "InMemoryPendingLoginStore",
    "LOGIN_URL",
    "LoginResult",
    "MakerWorldAuthClient",
    "MakerWorldAuthError",
    "PendingLogin",
    "PendingLoginStore",
    "ResponseCookies",
    "TFA_URL",
    "UuidFactory",
    "WarningLogger",
]
