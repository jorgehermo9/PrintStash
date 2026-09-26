"""How a fresh installation gets its first owner, as one validated value.

``VAULT_SETUP_MODE`` and ``VAULT_SETUP_ADMIN_*`` arrive as independent environment
variables, so on their own they can describe combinations that mean nothing, or
something dangerous: credentials that a mode silently ignores, or an ``environment``
mode with no credentials, which would leave a fresh install waiting for whoever
arrives first. :func:`resolve` turns them into exactly one :data:`SetupPolicy`, and
callers act on that instead of on the raw settings.

An invalid combination is not a crash. It resolves to :class:`Misconfigured`,
which keeps first ownership closed from every door and names the problem, so the
setup page can say what to change instead of the container restarting in a loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import ValidationError

from app.core.config import settings
from app.schemas.setup import SetupRequest

MisconfigurationCode = Literal[
    "admin_credentials_missing",
    "admin_credentials_invalid",
    "admin_credentials_without_environment_mode",
]

_VARIABLES = {
    "username": "VAULT_SETUP_ADMIN_USERNAME",
    "password": "VAULT_SETUP_ADMIN_PASSWORD",
    "email": "VAULT_SETUP_ADMIN_EMAIL",
}


@dataclass(frozen=True)
class TrustedNetwork:
    """A browser on the local network registers the first owner."""


@dataclass(frozen=True)
class Environment:
    """The first owner comes from ``VAULT_SETUP_ADMIN_*``; the browser never claims."""

    # Carries the password: never part of a repr that could reach a log.
    request: SetupRequest = field(repr=False)


@dataclass(frozen=True)
class Disabled:
    """No first-run path: an established or deliberately locked installation."""


@dataclass(frozen=True)
class Misconfigured:
    """The settings contradict each other; first ownership stays closed."""

    code: MisconfigurationCode
    variables: tuple[str, ...]

    def describe(self) -> str:
        return _DESCRIPTIONS[self.code].format(names=", ".join(self.variables))


# Names only, never values: this text reaches the startup log.
_DESCRIPTIONS: dict[MisconfigurationCode, str] = {
    "admin_credentials_missing": (
        "VAULT_SETUP_MODE=environment needs {names}; no administrator was created"
    ),
    "admin_credentials_invalid": (
        "{names} does not meet the first-run requirements (username 3 to 128 "
        "characters, password 8 to 256, email at most 255); no administrator was "
        "created"
    ),
    "admin_credentials_without_environment_mode": (
        "{names} is set but VAULT_SETUP_MODE is not environment, so it is not "
        "used; set VAULT_SETUP_MODE=environment or remove it"
    ),
}


SetupPolicy = TrustedNetwork | Environment | Disabled | Misconfigured


def resolve(mode: str, username: str, password: str, email: str) -> SetupPolicy:
    """Resolve the first-run settings into one policy.

    Blank values are unset: install forms emit empty variables for untouched
    fields. Spaces inside a real password are kept.
    """
    supplied = {
        "username": username.strip(),
        "password": password if password.strip() else "",
        "email": email.strip(),
    }
    present = tuple(_VARIABLES[key] for key, value in supplied.items() if value)
    if mode != "environment":
        if present:
            return Misconfigured("admin_credentials_without_environment_mode", present)
        return TrustedNetwork() if mode == "trusted_network" else Disabled()

    missing = tuple(
        _VARIABLES[key] for key in ("username", "password") if not supplied[key]
    )
    if missing:
        return Misconfigured("admin_credentials_missing", missing)
    try:
        request = SetupRequest(
            username=supplied["username"],
            password=supplied["password"],
            email=supplied["email"] or None,
        )
    except ValidationError as exc:
        # Name the variables only: a validation error echoes its input, and one
        # of these inputs is the password.
        rejected = {str(error["loc"][0]) for error in exc.errors()}
        return Misconfigured(
            "admin_credentials_invalid",
            tuple(_VARIABLES[key] for key in _VARIABLES if key in rejected),
        )
    return Environment(request)


def label(policy: SetupPolicy) -> str:
    """A log-safe name for ``policy``: the mode, or the misconfiguration code."""
    match policy:
        case TrustedNetwork():
            return "trusted_network"
        case Environment():
            return "environment"
        case Disabled():
            return "disabled"
        case Misconfigured(code=code):
            return f"misconfigured ({code})"


def current() -> SetupPolicy:
    """The policy the running configuration describes."""
    return resolve(
        str(settings.setup_mode),
        settings.setup_admin_username,
        settings.setup_admin_password.get_secret_value(),
        settings.setup_admin_email,
    )
