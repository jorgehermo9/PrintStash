"""What a fresh install gets and what an upgraded install gets, compared row by row.

`run_migrations` has two paths that both end at "head", and they do not produce the
same schema:

* **fresh** — no tables at all, so `create_all` builds from the models and stamps
  head. This is what a new installation gets, on SQLite or PostgreSQL.
* **upgraded** — an existing `alembic_version`, so the chain runs. This is what
  every self-hoster who upgraded from an earlier release has.

They agree on everything except foreign keys, and the difference is not a rounding
error: the models declare **eighteen** that no migration ever created — 108 against
90. A fresh install enforces them; an upgraded install does not. With
`foreign_keys=ON` that is different delete behaviour on two supported installations
of the same version, and one of the eighteen is reachable from the HTTP API today
(see `files.external_library_id` below).

This test does not assert they agree, because today they do not. It pins the exact
gap, in both directions. A new divergence fails it, and *closing* the gap fails it
too — with the message saying to delete the entry. The gap is a production decision
(add a migration, or drop the constraints from the models), and until it is taken
the honest thing is a guarded, documented list rather than a comment nobody reads.

The origin is visible in `tests/repo/test_schema_ddl.py`: the model graph has a
foreign-key cycle, and a `create_all` against an ALTER-capable dialect suppresses
the cycle-breaking constraints on the shared metadata. Autogenerating against a
metadata already in that state is how the chain came to be missing exactly these.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlmodel import SQLModel

import app.db.models  # noqa: F401 - registers every table on SQLModel.metadata
from alembic import command
from app.db import migrate as migrate_mod

# Foreign keys the models declare and the migration chain never creates, as
# (table, column). Two-sided: a new entry means fresh and upgraded installs drifted
# further apart, and a removed one means the gap was closed and this list should
# shrink with it.
KNOWN_MISSING_IN_CHAIN = {
    # The audit columns. Seventeen of the eighteen, across six tables — every
    # migration that added a `*_by` column declared it as a plain integer.
    ("collections", "created_by"),
    ("collections", "deleted_by"),
    ("collections", "updated_by"),
    ("files", "deleted_by"),
    ("models", "created_by"),
    ("models", "deleted_by"),
    ("models", "updated_by"),
    ("print_jobs", "created_by"),
    ("print_jobs", "deleted_by"),
    ("print_jobs", "updated_by"),
    ("printers", "created_by"),
    ("printers", "deleted_by"),
    ("printers", "updated_by"),
    ("tags", "created_by"),
    ("tags", "deleted_by"),
    ("tags", "updated_by"),
    ("users", "deleted_by"),
    # The one that is not an audit column, and the only one of the eighteen that
    # is reachable today: `DELETE /api/v1/libraries/{id}` trashes the indexed files
    # but leaves this column pointing at the library it then deletes. On a fresh
    # install that is a 500; on an upgraded one it succeeds and dangles.
    ("files", "external_library_id"),
}


def _foreign_keys(url: str) -> set[tuple[str, str]]:
    """Every (table, column) foreign key in the database.

    Every table, not a chosen few: the first version of this compared `files` and
    `models` alone, because that is where the flake surfaced, and it therefore
    reported five of the eighteen. A divergence list that only looks where you
    already know to look is not a divergence list.
    """
    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        return {
            (table, column)
            for table in inspector.get_table_names()
            for key in inspector.get_foreign_keys(table)
            for column in key["constrained_columns"]
        }
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def fresh_install_keys(
    tmp_path_factory: pytest.TempPathFactory,
) -> set[tuple[str, str]]:
    """The foreign keys `create_all` builds — what a new installation gets."""
    path: Path = tmp_path_factory.mktemp("fresh") / "fresh.sqlite"
    url = f"sqlite:///{path}"
    engine = create_engine(url)
    try:
        SQLModel.metadata.create_all(engine)
    finally:
        engine.dispose()
    return _foreign_keys(url)


@pytest.fixture(scope="module")
def upgraded_install_keys(
    tmp_path_factory: pytest.TempPathFactory,
) -> set[tuple[str, str]]:
    """The foreign keys the chain builds — what an upgraded installation has."""
    path: Path = tmp_path_factory.mktemp("upgraded") / "upgraded.sqlite"
    url = f"sqlite:///{path}"
    command.upgrade(migrate_mod._alembic_config(url), "head")  # noqa: SLF001
    return _foreign_keys(url)


class TestForeignKeyParity:
    def test_the_chain_is_missing_exactly_the_known_set(
        self,
        fresh_install_keys: set[tuple[str, str]],
        upgraded_install_keys: set[tuple[str, str]],
    ) -> None:
        missing = fresh_install_keys - upgraded_install_keys

        undocumented = sorted(missing - KNOWN_MISSING_IN_CHAIN)
        assert not undocumented, (
            "these foreign keys are new divergences between a fresh install and an "
            "upgraded one — the models declare them and no migration creates them: "
            f"{undocumented}. Add the migration rather than adding them here."
        )

        closed = sorted(KNOWN_MISSING_IN_CHAIN - missing)
        assert not closed, (
            "the chain now creates these, so the gap closed: "
            f"{closed}. Delete them from KNOWN_MISSING_IN_CHAIN."
        )

    def test_the_chain_creates_nothing_the_models_do_not_declare(
        self,
        fresh_install_keys: set[tuple[str, str]],
        upgraded_install_keys: set[tuple[str, str]],
    ) -> None:
        # The other direction, which has no known exceptions: a constraint the chain
        # creates but the models do not declare would be enforced only for upgraded
        # installs, and would never appear in a fresh one.
        extra = sorted(upgraded_install_keys - fresh_install_keys)

        assert not extra, (
            "the migration chain creates foreign keys the models do not declare, so "
            f"only upgraded installs enforce them: {extra}"
        )
