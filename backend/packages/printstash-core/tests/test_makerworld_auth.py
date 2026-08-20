from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import pytest

from printstash_core.imports.makerworld import (
    InMemoryPendingLoginStore,
    MakerWorldAuthClient,
    MakerWorldAuthError,
    PendingLogin,
)


@dataclass
class FakeResponse:
    status_code: int = 200
    body: object = field(default_factory=dict)
    cookies: Mapping[str, str] = field(default_factory=dict)

    def json(self) -> object:
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


class FakeHttpClient:
    def __init__(self, *responses: FakeResponse | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def post(
        self,
        url: str,
        *,
        json: Mapping[str, object],
        headers: Mapping[str, str],
        timeout: float,
    ) -> FakeResponse:
        self.calls.append(
            {"url": url, "json": json, "headers": headers, "timeout": timeout}
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeLogger:
    def __init__(self) -> None:
        self.warnings: list[tuple[str, tuple[object, ...]]] = []

    def warning(self, message: str, *args: object) -> None:
        self.warnings.append((message, args))


class Clock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def make_client(
    http: FakeHttpClient,
    *,
    store: InMemoryPendingLoginStore | None = None,
    clock: Clock | None = None,
    logger: FakeLogger | None = None,
) -> tuple[
    MakerWorldAuthClient,
    InMemoryPendingLoginStore,
    Clock,
    FakeLogger,
]:
    pending_store = store or InMemoryPendingLoginStore()
    fake_clock = clock or Clock()
    fake_logger = logger or FakeLogger()
    client = MakerWorldAuthClient(
        http=http,
        pending_store=pending_store,
        clock=fake_clock,
        uuid_factory=lambda: UUID("12345678-1234-5678-1234-567812345678"),
        logger=fake_logger,
    )
    return client, pending_store, fake_clock, fake_logger


@pytest.mark.asyncio
async def test_begin_login_returns_token_without_pending_state() -> None:
    http = FakeHttpClient(FakeResponse(body={"accessToken": "JWT123"}))
    client, store, _, _ = make_client(http)

    result = await client.begin_login("  a@b.com  ", "pw")

    assert result.status == "ok"
    assert result.token == "JWT123"
    assert result.login_token is None
    assert await store.get("12345678123456781234567812345678") is None
    assert http.calls[0]["json"] == {
        "account": "a@b.com",
        "password": "pw",
        "apiError": "",
    }


@pytest.mark.asyncio
async def test_email_code_pending_state_uses_injected_store_clock_and_uuid() -> None:
    http = FakeHttpClient(
        FakeResponse(body={"accessToken": "", "loginType": "verifyCode"}),
        FakeResponse(body={"access_token": "JWT-AFTER-CODE"}),
    )
    client, store, _, _ = make_client(http)

    begun = await client.begin_login("a@b.com", "secret-password")

    assert begun.status == "need_email_code"
    assert begun.login_token == "12345678123456781234567812345678"
    assert await store.get(begun.login_token) == PendingLogin(
        account="a@b.com", tfa_key=None, created_at=1_000.0
    )
    assert "password" not in PendingLogin.__dataclass_fields__

    done = await client.submit_code(begun.login_token, " 123456 ")

    assert done.status == "ok"
    assert done.token == "JWT-AFTER-CODE"
    assert await store.get(begun.login_token) is None
    assert http.calls[1]["json"] == {"account": "a@b.com", "code": "123456"}


@pytest.mark.asyncio
async def test_expired_pending_login_is_pruned_before_lookup() -> None:
    http = FakeHttpClient(
        FakeResponse(body={"loginType": "verifyCode"}),
    )
    client, store, clock, _ = make_client(http)
    begun = await client.begin_login("a@b.com", "pw")
    clock.now += 601.0

    with pytest.raises(MakerWorldAuthError, match="login_expired") as exc:
        await client.submit_code(begun.login_token, "123456")

    assert exc.value.code == "login_expired"
    assert await store.get(begun.login_token) is None
    assert len(http.calls) == 1


@pytest.mark.asyncio
async def test_tfa_code_uses_tfa_endpoint_and_cookie_token() -> None:
    http = FakeHttpClient(
        FakeResponse(body={"loginType": "tfa", "tfaKey": "KEY"}),
        FakeResponse(cookies={"token": "JWT-TFA"}),
    )
    client, store, _, _ = make_client(http)
    begun = await client.begin_login("a@b.com", "pw")

    assert begun.status == "need_tfa_code"
    assert (await store.get(begun.login_token)).tfa_key == "KEY"

    done = await client.submit_code(begun.login_token, "000111")

    assert done.token == "JWT-TFA"
    assert http.calls[1]["url"] == "https://bambulab.com/api/sign-in/tfa"
    assert http.calls[1]["json"] == {"tfaCode": "000111", "tfaKey": "KEY"}


@pytest.mark.asyncio
async def test_failed_code_does_not_consume_pending_login() -> None:
    http = FakeHttpClient(
        FakeResponse(body={"loginType": "verifyCode"}),
        FakeResponse(status_code=400),
    )
    client, store, _, _ = make_client(http)
    begun = await client.begin_login("a@b.com", "pw")

    with pytest.raises(MakerWorldAuthError, match="HTTP 400") as exc:
        await client.submit_code(begun.login_token, "bad")

    assert exc.value.code == "invalid_code"
    assert await store.get(begun.login_token) is not None


@pytest.mark.asyncio
async def test_network_boundary_uses_injected_logger() -> None:
    error = RuntimeError("connection reset")
    http = FakeHttpClient(error)
    client, _, _, logger = make_client(http)

    with pytest.raises(MakerWorldAuthError) as exc:
        await client.begin_login("a@b.com", "pw")

    assert exc.value.code == "network_error"
    assert logger.warnings == [("bambu login request failed: %s", (error,))]
