"""Builders for who is asking: users, tokens, and the two RBAC grants.

The suite's default `auth_headers` is an admin superuser, which proves nothing
about the 403 half of any endpoint's contract. Every access-control row needs a
second identity, and hand-rolling one is how thirteen slightly different `_user`
helpers came to exist — with `superuser` defaulting to `True` in one file and
`False` in others, so the same call meant opposite things depending on which file
you were reading. Here it defaults to `False`: a plain user is the interesting
case, and a superuser is something a test asks for out loud.

Passwords are hashed through the production hasher rather than stubbed, because
several tests drive the real login endpoint. `PASSWORD` is deliberately obvious
filler — no test in this suite should contain anything resembling a real
credential.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from app.db.models import (
    Collection,
    CollectionPermission,
    CollectionRole,
    Printer,
    PrinterPermission,
    PrinterRole,
    User,
)
from app.services.auth import create_access_token, hash_password
from tests.factories._support import nth, reject_aliases, save

PASSWORD = "Password123"


def build_user(
    session: Session,
    username: str | None = None,
    *,
    superuser: bool = False,
    active: bool = True,
    password: str = PASSWORD,
    **overrides: Any,
) -> User:
    """A user who can log in. Not a superuser unless you say so."""
    reject_aliases(
        overrides,
        {
            "is_superuser": "superuser",
            "is_active": "active",
            "hashed_password": "password",
        },
    )
    return save(
        session,
        User(
            username=username or f"user-{nth('user')}",
            hashed_password=hash_password(password),
            is_active=active,
            is_superuser=superuser,
            **overrides,
        ),
    )


def bearer(user: User, *, scope: str = "write") -> dict[str, str]:
    """Authorization headers for *user* at *scope*.

    The scope is separate from the role on purpose: a token can be `read` even
    for a superuser, and several endpoints are gated on the scope rather than on
    who the caller is.
    """
    return {
        "Authorization": f"Bearer {create_access_token(user.id, user.username, scope=scope)}"
    }


def grant_collection_role(
    session: Session,
    user: User,
    collection: Collection,
    role: CollectionRole = CollectionRole.VIEW,
) -> CollectionPermission:
    """Share a collection with a user, the way an admin would.

    Roles are hierarchical (`view` < `edit` < `admin`) and resolve down the
    collection tree, so granting on a parent is how a test covers inheritance.
    """
    return save(
        session,
        CollectionPermission(user_id=user.id, collection_id=collection.id, role=role),
    )


def grant_printer_role(
    session: Session,
    user: User,
    printer: Printer,
    role: PrinterRole = PrinterRole.PRINT,
) -> PrinterPermission:
    """Give a user a role on one printer.

    Separate from collection access: someone who may print a model is not
    necessarily someone who may reconfigure the machine. Roles are ordered
    `view` < `print` < `control` < `admin`.
    """
    return save(
        session,
        PrinterPermission(user_id=user.id, printer_id=printer.id, role=role),
    )
