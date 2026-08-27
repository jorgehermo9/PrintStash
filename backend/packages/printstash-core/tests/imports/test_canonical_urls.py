"""Binding a captured page URL to the provider that claims to have produced it.

A capture arrives from a browser extension with a `provider` and a `canonical_url`, and
that pair becomes the model's durable identity — what a re-capture deduplicates against
and what the UI links back to. So the URL is not merely tidied here, it is **bound**: for
every provider PrintStash has an adapter for, the host and the page route must be the ones
that provider actually uses, and the item segment must match the id the capture carries.
A capture claiming `printables` with a URL on someone else's domain is a capture that
would link a user's library to a page the provider never served.

Everything that could make one URL mean two things is refused rather than normalised:
userinfo, an explicit port, a percent-encoded path (a browser, a proxy and the provider's
own server may each decode it differently), a backslash, a dot segment, and an empty
segment. Query strings and fragments are **dropped**, because a provider download link
routinely carries a signed credential there and this value is stored and displayed.

Case is normalised only where it is route-insignificant — scheme, host, locale and route
tokens — and never on a provider's own slug, which is theirs to define.
"""

from __future__ import annotations

import pytest

from printstash_core.imports.contracts import (
    CaptureContractError,
    canonicalize_provider_url,
    sanitize_canonical_url,
)

MAX_URL_LENGTH = 2048


class TestSanitizeCanonicalUrl:
    """The permissive path, for providers PrintStash has no adapter for."""

    def test_keeps_an_ordinary_page_url(self) -> None:
        assert (
            sanitize_canonical_url("https://example.test/models/42")
            == "https://example.test/models/42"
        )

    def test_drops_everything_after_the_path(self) -> None:
        # A provider download link routinely carries a signed credential here,
        # and this value is stored and displayed.
        assert (
            sanitize_canonical_url("https://example.test/a?token=secret#frag")
            == "https://example.test/a"
        )

    def test_lowercases_the_authority(self) -> None:
        assert (
            sanitize_canonical_url("HTTPS://EXAMPLE.TEST/a") == "https://example.test/a"
        )

    def test_keeps_a_port_for_a_provider_it_does_not_know(self) -> None:
        assert (
            sanitize_canonical_url("https://example.test:8443/a")
            == "https://example.test:8443/a"
        )

    def test_normalises_an_international_host_to_punycode(self) -> None:
        # Two spellings of one host would otherwise be two identities.
        assert sanitize_canonical_url("https://exämple.test/a").startswith(
            "https://xn--"
        )

    def test_brackets_an_ipv6_host(self) -> None:
        assert (
            sanitize_canonical_url("https://[2001:db8::1]/a")
            == "https://[2001:db8::1]/a"
        )

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("ftp://example.test/a", id="scheme"),
            pytest.param("/models/42", id="relative"),
            pytest.param("https:///a", id="no-host"),
            pytest.param("https://alice:pw@example.test/a", id="userinfo"),
            pytest.param("https://alice@example.test/a", id="username-only"),
            pytest.param("https://example.test:notaport/a", id="bad-port"),
            pytest.param("https://" + "a" * 300 + ".test/a", id="unencodable-host"),
        ],
    )
    def test_refuses_a_value_it_cannot_vouch_for(self, value: str) -> None:
        with pytest.raises(CaptureContractError):
            sanitize_canonical_url(value)

    def test_refuses_a_url_longer_than_the_cap(self) -> None:
        with pytest.raises(CaptureContractError):
            sanitize_canonical_url("https://example.test/" + "a" * MAX_URL_LENGTH)

    def test_refuses_something_that_is_not_a_string(self) -> None:
        with pytest.raises(CaptureContractError):
            sanitize_canonical_url(42)


class TestCanonicalizeProviderUrlUnknownProvider:
    def test_falls_back_to_the_permissive_rules(self) -> None:
        # An adapter PrintStash does not have yet must still be capturable.
        assert (
            canonicalize_provider_url("some-new-site", "https://example.test/x?a=1")
            == "https://example.test/x"
        )


class TestCanonicalizeProviderUrlPrintables:
    def test_keeps_a_model_page(self) -> None:
        assert (
            canonicalize_provider_url(
                "printables", "https://www.printables.com/model/42-cube"
            )
            == "https://www.printables.com/model/42-cube"
        )

    def test_keeps_the_files_view(self) -> None:
        # `/files` is a real model-page view the browser extension captures from.
        assert canonicalize_provider_url(
            "printables", "https://www.printables.com/model/42-cube/files"
        ).endswith("/files")

    def test_matches_an_id_against_a_slugged_segment(self) -> None:
        assert canonicalize_provider_url(
            "printables", "https://www.printables.com/model/42-cube", "42"
        ).endswith("42-cube")

    def test_matches_an_id_given_in_full(self) -> None:
        # Older portable captures carry the whole slug as the id.
        assert canonicalize_provider_url(
            "printables", "https://www.printables.com/model/42-cube", "42-cube"
        ).endswith("42-cube")

    def test_normalises_the_route_token_but_not_the_slug(self) -> None:
        assert canonicalize_provider_url(
            "printables", "https://www.printables.com/MODEL/42-Cube"
        ).endswith("/model/42-Cube")

    def test_drops_a_trailing_slash(self) -> None:
        assert (
            canonicalize_provider_url(
                "printables", "https://www.printables.com/model/42-cube/"
            )
            == "https://www.printables.com/model/42-cube"
        )

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("https://printables.example.test/model/42", id="wrong-host"),
            pytest.param("https://www.printables.com:8443/model/42", id="port"),
            pytest.param("https://www.printables.com/models/42", id="wrong-route"),
            pytest.param("https://www.printables.com/model", id="too-short"),
            pytest.param(
                "https://www.printables.com/model/42/other", id="wrong-subview"
            ),
            pytest.param("https://www.printables.com/model/42/files/x", id="too-long"),
        ],
    )
    def test_refuses_a_url_the_provider_would_not_serve(self, value: str) -> None:
        with pytest.raises(CaptureContractError):
            canonicalize_provider_url("printables", value)

    def test_refuses_an_item_the_url_does_not_name(self) -> None:
        # Otherwise a capture could bind one provider page to another's id.
        with pytest.raises(CaptureContractError):
            canonicalize_provider_url(
                "printables", "https://www.printables.com/model/42-cube", "99"
            )


class TestCanonicalizeProviderUrlMakerworld:
    def test_keeps_a_model_page(self) -> None:
        assert (
            canonicalize_provider_url("makerworld", "https://makerworld.com/models/42")
            == "https://makerworld.com/models/42"
        )

    def test_keeps_a_localised_model_page(self) -> None:
        assert canonicalize_provider_url(
            "makerworld", "https://makerworld.com/en/models/42-cube"
        ).endswith("/en/models/42-cube")

    def test_accepts_a_regional_subdomain(self) -> None:
        assert canonicalize_provider_url(
            "makerworld", "https://eu.makerworld.com/models/42"
        ).startswith("https://eu.makerworld.com/")

    def test_lowercases_the_path_segments(self) -> None:
        assert canonicalize_provider_url(
            "makerworld", "https://makerworld.com/EN/MODELS/42-Cube"
        ).endswith("/en/models/42-Cube")

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("https://makerworld.example.test/models/42", id="wrong-host"),
            pytest.param("https://makerworld.com/42", id="too-short"),
            pytest.param("https://makerworld.com/en/things/42", id="wrong-route"),
            pytest.param("https://makerworld.com/english/models/42", id="bad-locale"),
            pytest.param("https://makerworld.com/en/models/42/extra", id="too-long"),
        ],
    )
    def test_refuses_a_url_the_provider_would_not_serve(self, value: str) -> None:
        with pytest.raises(CaptureContractError):
            canonicalize_provider_url("makerworld", value)

    def test_refuses_an_item_the_url_does_not_name(self) -> None:
        with pytest.raises(CaptureContractError):
            canonicalize_provider_url(
                "makerworld", "https://makerworld.com/models/42", "99"
            )


class TestCanonicalizeProviderUrlThingiverse:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            pytest.param(
                "https://www.thingiverse.com/thing:42",
                "https://www.thingiverse.com/thing:42",
                id="thing-colon",
            ),
            pytest.param(
                "https://www.thingiverse.com/things/42",
                "https://www.thingiverse.com/things/42",
                id="things-path",
            ),
            pytest.param(
                "https://www.thingiverse.com/things/42/files",
                "https://www.thingiverse.com/things/42/files",
                id="things-files",
            ),
            pytest.param(
                "https://www.thingiverse.com/thing:42/files",
                "https://www.thingiverse.com/thing:42/files",
                id="thing-colon-files",
            ),
        ],
    )
    def test_keeps_each_of_the_four_page_shapes(
        self, value: str, expected: str
    ) -> None:
        # Thingiverse serves the same model under several routes; all four are
        # real, and all four must reduce to a stable identity.
        assert canonicalize_provider_url("thingiverse", value) == expected

    def test_normalises_the_route_token(self) -> None:
        assert canonicalize_provider_url(
            "thingiverse", "https://www.thingiverse.com/THING:42"
        ).endswith("/thing:42")

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("https://thingiverse.example.test/thing:42", id="wrong-host"),
            pytest.param("https://www.thingiverse.com/thing", id="no-id"),
            pytest.param("https://www.thingiverse.com/things/abc", id="non-numeric-id"),
            pytest.param(
                "https://www.thingiverse.com/things/42/other", id="wrong-subview"
            ),
        ],
    )
    def test_refuses_a_url_the_provider_would_not_serve(self, value: str) -> None:
        with pytest.raises(CaptureContractError):
            canonicalize_provider_url("thingiverse", value)

    def test_refuses_an_item_the_url_does_not_name(self) -> None:
        with pytest.raises(CaptureContractError):
            canonicalize_provider_url(
                "thingiverse", "https://www.thingiverse.com/thing:42", "99"
            )


class TestCanonicalizeProviderUrlCults:
    def test_keeps_a_model_page(self) -> None:
        assert (
            canonicalize_provider_url(
                "cults", "https://cults3d.com/3d-model/tool/widget"
            )
            == "https://cults3d.com/3d-model/tool/widget"
        )

    def test_keeps_a_localised_model_page(self) -> None:
        assert canonicalize_provider_url(
            "cults", "https://cults3d.com/en/3d-model/tool/widget"
        ).endswith("/en/3d-model/tool/widget")

    def test_does_not_bind_the_slug_to_the_item_id(self) -> None:
        # Cults' API id is opaque and differs from the page slug, so the slug
        # comparison belongs to its adapter rather than here.
        assert canonicalize_provider_url(
            "cults", "https://cults3d.com/3d-model/tool/widget", "opaque-id"
        ).endswith("/widget")

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("https://cults.example.test/3d-model/a/b", id="wrong-host"),
            pytest.param("https://cults3d.com/3d-model/a", id="too-short"),
            pytest.param("https://cults3d.com/model/a/b", id="wrong-route"),
            pytest.param("https://cults3d.com/english/3d-model/a/b", id="bad-locale"),
            pytest.param("https://cults3d.com/en/3d-model/a/b/c", id="too-long"),
        ],
    )
    def test_refuses_a_url_the_provider_would_not_serve(self, value: str) -> None:
        with pytest.raises(CaptureContractError):
            canonicalize_provider_url("cults", value)


class TestCanonicalizeProviderUrlMyMiniFactory:
    def test_keeps_an_object_page(self) -> None:
        assert (
            canonicalize_provider_url(
                "myminifactory", "https://www.myminifactory.com/object/3d-print-42"
            )
            == "https://www.myminifactory.com/object/3d-print-42"
        )

    def test_normalises_the_route_token(self) -> None:
        assert canonicalize_provider_url(
            "myminifactory", "https://www.myminifactory.com/OBJECT/3d-print-42"
        ).endswith("/object/3d-print-42")

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("https://mmf.example.test/object/42", id="wrong-host"),
            pytest.param("https://www.myminifactory.com/objects/42", id="wrong-route"),
            pytest.param("https://www.myminifactory.com/object", id="too-short"),
            pytest.param(
                "https://www.myminifactory.com/object/42/files", id="too-long"
            ),
        ],
    )
    def test_refuses_a_url_the_provider_would_not_serve(self, value: str) -> None:
        with pytest.raises(CaptureContractError):
            canonicalize_provider_url("myminifactory", value)

    def test_refuses_an_item_the_url_does_not_name(self) -> None:
        with pytest.raises(CaptureContractError):
            canonicalize_provider_url(
                "myminifactory", "https://www.myminifactory.com/object/42-cube", "99"
            )


class TestCanonicalizeProviderUrlPathSafety:
    """Anything that could make one URL mean two things is refused, not normalised."""

    @pytest.mark.parametrize(
        "path",
        [
            pytest.param("/model/%2e%2e/42", id="encoded-dot-segment"),
            pytest.param("/model/42%2Ffiles", id="encoded-slash"),
            pytest.param("/model/../42", id="dot-segment"),
            pytest.param("/model//42", id="empty-segment"),
            pytest.param("/model/42%20cube", id="encoded-space"),
            pytest.param("/model/42%23x", id="encoded-hash"),
        ],
    )
    def test_refuses_a_path_a_proxy_might_read_differently(self, path: str) -> None:
        # A browser, a proxy and the provider's own server may each decode this
        # differently, which is three identities for one capture.
        with pytest.raises(CaptureContractError):
            canonicalize_provider_url("printables", f"https://www.printables.com{path}")

    def test_refuses_a_backslash_in_the_path(self) -> None:
        with pytest.raises(CaptureContractError):
            canonicalize_provider_url(
                "printables", "https://www.printables.com/model\\42"
            )

    def test_refuses_a_bare_host_with_no_path(self) -> None:
        with pytest.raises(CaptureContractError):
            canonicalize_provider_url("printables", "https://www.printables.com/")
