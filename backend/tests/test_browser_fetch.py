"""Unit tests for app.services.browser_fetch (15% coverage, no dedicated file).

The real Chromium/Patchright launch is faked at the ``_get_browser`` seam for
``fetch_rendered_html``/``api_get`` (there's no plain-HTTP transport here to
point at a local httpd — everything goes through a headless browser context),
and at the lazily-imported ``patchright.async_api.async_playwright`` seam for
``_get_browser`` itself.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.core.config import _overlay
from app.services import browser_fetch


@pytest.fixture(autouse=True)
def _reset_browser_globals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser_fetch, "_browser", None)
    monkeypatch.setattr(browser_fetch, "_playwright", None)
    _overlay["makerworld_browser_enabled"] = True
    _overlay["browser_fetch_timeout_seconds"] = 5
    yield
    _overlay.pop("makerworld_browser_enabled", None)
    _overlay.pop("browser_fetch_timeout_seconds", None)


# ---------------------------------------------------------------------------
# _cookies_for
# ---------------------------------------------------------------------------


def test_cookies_for_parses_multiple_pairs_and_scopes_to_registrable_domain() -> None:
    cookies = browser_fetch._cookies_for(  # noqa: SLF001
        "https://www.makerworld.com/en/models/123", "a=1; b=2; malformed"
    )
    assert cookies == [
        {"name": "a", "value": "1", "domain": ".makerworld.com", "path": "/"},
        {"name": "b", "value": "2", "domain": ".makerworld.com", "path": "/"},
    ]


def test_cookies_for_empty_host_returns_empty_list() -> None:
    assert browser_fetch._cookies_for("not-a-url", "a=1") == []  # noqa: SLF001


def test_cookies_for_single_label_host_uses_host_as_domain() -> None:
    cookies = browser_fetch._cookies_for("http://localhost:8080/x", "a=1")  # noqa: SLF001
    assert cookies == [{"name": "a", "value": "1", "domain": "localhost", "path": "/"}]


# ---------------------------------------------------------------------------
# feature flag short-circuit
# ---------------------------------------------------------------------------


def test_fetch_rendered_html_returns_none_when_feature_disabled() -> None:
    _overlay["makerworld_browser_enabled"] = False
    assert asyncio.run(browser_fetch.fetch_rendered_html("https://x.test")) is None


def test_api_get_returns_none_when_feature_disabled() -> None:
    _overlay["makerworld_browser_enabled"] = False
    assert asyncio.run(browser_fetch.api_get("https://x.test/api")) is None


# ---------------------------------------------------------------------------
# fetch_rendered_html / api_get against a faked browser
# ---------------------------------------------------------------------------


def _fake_context(page: Any) -> Any:
    context = AsyncMock()
    context.new_page.return_value = page
    context.add_cookies = AsyncMock()
    return context


def test_fetch_rendered_html_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    page = AsyncMock()
    page.content.return_value = "<html>rendered</html>"
    context = _fake_context(page)
    browser = AsyncMock()
    browser.new_context.return_value = context

    monkeypatch.setattr(browser_fetch, "_get_browser", AsyncMock(return_value=browser))

    html = asyncio.run(
        browser_fetch.fetch_rendered_html(
            "https://www.makerworld.com/x", wait_selector="script#__NEXT_DATA__", extra_cookie="a=1"
        )
    )

    assert html == "<html>rendered</html>"
    context.add_cookies.assert_awaited_once()
    page.wait_for_selector.assert_awaited_once()
    context.close.assert_awaited_once()


def test_fetch_rendered_html_returns_none_when_browser_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(browser_fetch, "_get_browser", AsyncMock(return_value=None))
    assert asyncio.run(browser_fetch.fetch_rendered_html("https://x.test")) is None


def test_fetch_rendered_html_swallows_wait_selector_timeout_and_returns_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = AsyncMock()
    page.content.return_value = "<html>partial</html>"
    page.wait_for_selector.side_effect = TimeoutError("selector never appeared")
    context = _fake_context(page)
    browser = AsyncMock()
    browser.new_context.return_value = context
    monkeypatch.setattr(browser_fetch, "_get_browser", AsyncMock(return_value=browser))

    html = asyncio.run(
        browser_fetch.fetch_rendered_html("https://x.test", wait_selector="script#missing")
    )

    assert html == "<html>partial</html>"  # returns whatever rendered, not None
    context.close.assert_awaited_once()


def test_fetch_rendered_html_returns_none_on_navigation_failure_and_still_closes_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = AsyncMock()
    page.goto.side_effect = RuntimeError("net::ERR_CONNECTION_RESET")
    context = _fake_context(page)
    browser = AsyncMock()
    browser.new_context.return_value = context
    monkeypatch.setattr(browser_fetch, "_get_browser", AsyncMock(return_value=browser))

    html = asyncio.run(browser_fetch.fetch_rendered_html("https://x.test"))

    assert html is None
    context.close.assert_awaited_once()


def test_fetch_rendered_html_cookie_injection_failure_is_non_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = AsyncMock()
    page.content.return_value = "<html>ok</html>"
    context = _fake_context(page)
    context.add_cookies.side_effect = RuntimeError("bad cookie")
    browser = AsyncMock()
    browser.new_context.return_value = context
    monkeypatch.setattr(browser_fetch, "_get_browser", AsyncMock(return_value=browser))

    html = asyncio.run(
        browser_fetch.fetch_rendered_html("https://x.test", extra_cookie="bad=;;;")
    )

    assert html == "<html>ok</html>"


def test_api_get_happy_path_warms_origin_then_requests_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = AsyncMock()
    context = _fake_context(page)
    resp = AsyncMock()
    resp.status = 200
    resp.text.return_value = '{"ok": true}'
    context.request.get = AsyncMock(return_value=resp)
    browser = AsyncMock()
    browser.new_context.return_value = context
    monkeypatch.setattr(browser_fetch, "_get_browser", AsyncMock(return_value=browser))

    result = asyncio.run(
        browser_fetch.api_get("https://api.makerworld.com/v1/x", cookie="session=abc")
    )

    assert result == (200, '{"ok": true}')
    page.goto.assert_awaited_once()
    goto_args = page.goto.await_args
    assert goto_args.args[0] == "https://api.makerworld.com/"
    context.request.get.assert_awaited_once()


def test_api_get_returns_none_when_browser_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(browser_fetch, "_get_browser", AsyncMock(return_value=None))
    assert asyncio.run(browser_fetch.api_get("https://x.test/api")) is None


def test_api_get_returns_none_on_request_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    page = AsyncMock()
    context = _fake_context(page)
    context.request.get = AsyncMock(side_effect=RuntimeError("timeout"))
    browser = AsyncMock()
    browser.new_context.return_value = context
    monkeypatch.setattr(browser_fetch, "_get_browser", AsyncMock(return_value=browser))

    assert asyncio.run(browser_fetch.api_get("https://x.test/api")) is None
    context.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# _get_browser / _shutdown / close_browser
# ---------------------------------------------------------------------------


def test_get_browser_reuses_already_connected_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = AsyncMock()
    existing.is_connected = lambda: True
    monkeypatch.setattr(browser_fetch, "_browser", existing)

    result = asyncio.run(browser_fetch._get_browser())  # noqa: SLF001

    assert result is existing


def test_get_browser_returns_none_when_patchright_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import builtins

    real_import = builtins.__import__

    def _fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "patchright.async_api":
            raise ImportError("no patchright")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    assert asyncio.run(browser_fetch._get_browser()) is None  # noqa: SLF001


def test_get_browser_launch_failure_shuts_down_and_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_playwright_driver = AsyncMock()
    fake_playwright_driver.chromium.launch = AsyncMock(side_effect=RuntimeError("no chromium binary"))

    fake_async_playwright_cm = AsyncMock()
    fake_async_playwright_cm.start = AsyncMock(return_value=fake_playwright_driver)

    monkeypatch.setattr(
        "patchright.async_api.async_playwright", lambda: fake_async_playwright_cm
    )

    result = asyncio.run(browser_fetch._get_browser())  # noqa: SLF001

    assert result is None
    assert browser_fetch._browser is None
    assert browser_fetch._playwright is None


def test_get_browser_launches_successfully(monkeypatch: pytest.MonkeyPatch) -> None:
    launched_browser = AsyncMock()
    fake_playwright_driver = AsyncMock()
    fake_playwright_driver.chromium.launch = AsyncMock(return_value=launched_browser)

    fake_async_playwright_cm = AsyncMock()
    fake_async_playwright_cm.start = AsyncMock(return_value=fake_playwright_driver)

    monkeypatch.setattr(
        "patchright.async_api.async_playwright", lambda: fake_async_playwright_cm
    )

    result = asyncio.run(browser_fetch._get_browser())  # noqa: SLF001

    assert result is launched_browser
    assert browser_fetch._browser is launched_browser


def test_shutdown_is_safe_when_nothing_launched() -> None:
    asyncio.run(browser_fetch._shutdown())  # noqa: SLF001
    assert browser_fetch._browser is None
    assert browser_fetch._playwright is None


def test_close_browser_tears_down_existing_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    browser = AsyncMock()
    playwright_driver = AsyncMock()
    monkeypatch.setattr(browser_fetch, "_browser", browser)
    monkeypatch.setattr(browser_fetch, "_playwright", playwright_driver)

    asyncio.run(browser_fetch.close_browser())

    browser.close.assert_awaited_once()
    playwright_driver.stop.assert_awaited_once()
    assert browser_fetch._browser is None
    assert browser_fetch._playwright is None


def test_shutdown_swallows_close_and_stop_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    browser = AsyncMock()
    browser.close.side_effect = RuntimeError("already dead")
    playwright_driver = AsyncMock()
    playwright_driver.stop.side_effect = RuntimeError("already stopped")
    monkeypatch.setattr(browser_fetch, "_browser", browser)
    monkeypatch.setattr(browser_fetch, "_playwright", playwright_driver)

    asyncio.run(browser_fetch._shutdown())  # noqa: SLF001

    assert browser_fetch._browser is None
    assert browser_fetch._playwright is None
