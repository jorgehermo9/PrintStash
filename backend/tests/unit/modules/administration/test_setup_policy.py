"""The first-run settings resolve to exactly one policy, and nonsense resolves to closed.

``VAULT_SETUP_MODE`` and ``VAULT_SETUP_ADMIN_*`` are separate environment variables,
so a deployment can describe combinations that mean nothing: credentials a mode would
silently ignore, or ``environment`` with nothing to create an owner from. Each of those
used to fall back to whatever the mode allowed, which could be first-come browser
registration behind a store's proxy. They now resolve to ``Misconfigured``, which
keeps first ownership shut and names the variables to change, never their values.
"""

from __future__ import annotations

from typing import get_args

import pytest

from app.modules.administration import setup_policy
from app.schemas.setup import UnavailableReason

USERNAME = "store-owner"
PASSWORD = "StoreFormPassword123"


class TestEnvironment:
    def test_keeps_the_password_out_of_its_repr(self) -> None:
        policy = setup_policy.resolve("environment", USERNAME, PASSWORD, "")

        assert PASSWORD not in repr(policy)


class TestMisconfigured:
    @pytest.mark.parametrize(
        ("mode", "username", "password", "description"),
        [
            pytest.param(
                "environment",
                USERNAME,
                "",
                "VAULT_SETUP_MODE=environment needs VAULT_SETUP_ADMIN_PASSWORD; "
                "no administrator was created",
                id="missing",
            ),
            pytest.param(
                "environment",
                USERNAME,
                "short",
                "VAULT_SETUP_ADMIN_PASSWORD does not meet the first-run requirements "
                "(username 3 to 128 characters, password 8 to 256, email at most "
                "255); no administrator was created",
                id="invalid",
            ),
            pytest.param(
                "trusted_network",
                USERNAME,
                PASSWORD,
                "VAULT_SETUP_ADMIN_USERNAME, VAULT_SETUP_ADMIN_PASSWORD is set but "
                "VAULT_SETUP_MODE is not environment, so it is not used; set "
                "VAULT_SETUP_MODE=environment or remove it",
                id="wrong-mode",
            ),
        ],
    )
    def test_describes_the_problem_by_variable_name(
        self, mode: str, username: str, password: str, description: str
    ) -> None:
        policy = setup_policy.resolve(mode, username, password, "")

        assert isinstance(policy, setup_policy.Misconfigured)
        assert policy.describe() == description

    @pytest.mark.parametrize(
        ("mode", "password"),
        [
            pytest.param("environment", "short", id="invalid"),
            pytest.param("trusted_network", PASSWORD, id="wrong-mode"),
        ],
    )
    def test_never_describes_the_password(self, mode: str, password: str) -> None:
        policy = setup_policy.resolve(mode, USERNAME, password, "")

        assert isinstance(policy, setup_policy.Misconfigured)
        assert password not in policy.describe()

    def test_every_code_is_a_reason_the_setup_page_explains(self) -> None:
        # The status endpoint forwards the code; the page must know each one.
        codes = set(get_args(setup_policy.MisconfigurationCode))

        assert codes <= set(get_args(UnavailableReason))


class TestResolve:
    def test_trusted_network_without_credentials_registers_in_the_browser(self) -> None:
        policy = setup_policy.resolve("trusted_network", "", "", "")

        assert isinstance(policy, setup_policy.TrustedNetwork)

    def test_disabled_without_credentials_offers_no_first_run_path(self) -> None:
        policy = setup_policy.resolve("disabled", "", "", "")

        assert isinstance(policy, setup_policy.Disabled)

    def test_environment_with_credentials_provisions_them(self) -> None:
        policy = setup_policy.resolve("environment", USERNAME, PASSWORD, "")

        assert isinstance(policy, setup_policy.Environment)
        assert (policy.request.username, policy.request.password) == (
            USERNAME,
            PASSWORD,
        )

    def test_environment_trims_the_username(self) -> None:
        policy = setup_policy.resolve("environment", f"  {USERNAME}  ", PASSWORD, "")

        assert isinstance(policy, setup_policy.Environment)
        assert policy.request.username == USERNAME

    def test_environment_keeps_spaces_inside_the_password(self) -> None:
        policy = setup_policy.resolve("environment", USERNAME, "pass word 123", "")

        assert isinstance(policy, setup_policy.Environment)
        assert policy.request.password == "pass word 123"

    def test_environment_treats_a_blank_email_as_none(self) -> None:
        policy = setup_policy.resolve("environment", USERNAME, PASSWORD, "   ")

        assert isinstance(policy, setup_policy.Environment)
        assert policy.request.email is None

    @pytest.mark.parametrize(
        ("username", "password", "variables"),
        [
            pytest.param(
                USERNAME, "", ("VAULT_SETUP_ADMIN_PASSWORD",), id="password-missing"
            ),
            pytest.param(
                USERNAME, "   ", ("VAULT_SETUP_ADMIN_PASSWORD",), id="password-blank"
            ),
            pytest.param("", PASSWORD, ("VAULT_SETUP_ADMIN_USERNAME",), id="username"),
            pytest.param(
                "",
                "",
                ("VAULT_SETUP_ADMIN_USERNAME", "VAULT_SETUP_ADMIN_PASSWORD"),
                id="both",
            ),
        ],
    )
    def test_environment_without_credentials_is_misconfigured(
        self, username: str, password: str, variables: tuple[str, ...]
    ) -> None:
        policy = setup_policy.resolve("environment", username, password, "")

        assert policy == setup_policy.Misconfigured(
            "admin_credentials_missing", variables
        )

    @pytest.mark.parametrize(
        ("username", "password", "variable"),
        [
            pytest.param("ab", PASSWORD, "VAULT_SETUP_ADMIN_USERNAME", id="username"),
            pytest.param(
                USERNAME, "short", "VAULT_SETUP_ADMIN_PASSWORD", id="password"
            ),
        ],
    )
    def test_environment_below_the_wizard_minimum_is_misconfigured(
        self, username: str, password: str, variable: str
    ) -> None:
        policy = setup_policy.resolve("environment", username, password, "")

        assert policy == setup_policy.Misconfigured(
            "admin_credentials_invalid", (variable,)
        )

    @pytest.mark.parametrize("mode", ["trusted_network", "disabled"], ids=str)
    @pytest.mark.parametrize(
        ("username", "password", "email", "variables"),
        [
            pytest.param(
                USERNAME,
                PASSWORD,
                "",
                ("VAULT_SETUP_ADMIN_USERNAME", "VAULT_SETUP_ADMIN_PASSWORD"),
                id="credentials",
            ),
            pytest.param(
                "", "", "owner@example.test", ("VAULT_SETUP_ADMIN_EMAIL",), id="email"
            ),
        ],
    )
    def test_credentials_outside_environment_mode_are_misconfigured(
        self,
        mode: str,
        username: str,
        password: str,
        email: str,
        variables: tuple[str, ...],
    ) -> None:
        # Silently ignoring them would leave the operator believing an owner exists.
        policy = setup_policy.resolve(mode, username, password, email)

        assert policy == setup_policy.Misconfigured(
            "admin_credentials_without_environment_mode", variables
        )


class TestLabel:
    @pytest.mark.parametrize(
        ("mode", "username", "password", "label"),
        [
            pytest.param("trusted_network", "", "", "trusted_network", id="trusted"),
            pytest.param("environment", USERNAME, PASSWORD, "environment", id="env"),
            pytest.param("disabled", "", "", "disabled", id="disabled"),
            pytest.param(
                "environment",
                USERNAME,
                "",
                "misconfigured (admin_credentials_missing)",
                id="misconfigured",
            ),
        ],
    )
    def test_names_the_resolved_policy(
        self, mode: str, username: str, password: str, label: str
    ) -> None:
        # The startup log reports this, not VAULT_SETUP_MODE: a misconfigured
        # mode keeps every door shut whatever it says.
        policy = setup_policy.resolve(mode, username, password, "")

        assert setup_policy.label(policy) == label

    def test_never_names_the_credentials(self) -> None:
        policy = setup_policy.resolve("environment", USERNAME, PASSWORD, "")

        assert USERNAME not in setup_policy.label(policy)
        assert PASSWORD not in setup_policy.label(policy)
