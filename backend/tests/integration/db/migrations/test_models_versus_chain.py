"""What a fresh install gets and what an upgraded one gets, compared row by row.

`run_migrations` has two paths to head and they do not produce the same schema:

* **fresh** — no tables at all, so `create_all` builds from the models and stamps
  head. This is what a new installation gets.
* **upgraded** — an existing `alembic_version`, so the chain runs from wherever the
  installation is. This is what a self-hoster who upgraded has.

The difference is eighteen foreign keys — 108 against 90 — and the cause is not an
oversight. SQLite has no `ALTER TABLE ADD CONSTRAINT`, so the migrations that added
the audit columns guarded their `op.create_foreign_key` calls with
`if not is_sqlite` (see `69b6a6d8a1d1_phase_4c_4d_lifecycle_audit.py`). The column
lands, the constraint does not. `batch_alter_table` — which Alembic implements on
SQLite by rebuilding the table — is the way to do it, and this repo already uses it
elsewhere; it was not used here.

Two shapes therefore exist in the wild, both SQLite:

* Installed before v0.7.2, when a fresh database still replayed the chain: the pure
  chain shape, missing all eighteen.
* Installed v0.7.2 or later, when `create_all` became the fresh path: every
  constraint the models declared *at install time*, minus any a later migration
  added without one.

PostgreSQL is not affected. The chain's baseline cannot bootstrap a Postgres
database at all — which is exactly why the fresh path became `create_all` — so every
Postgres installation is a `create_all` installation and has all 108.

This test does not assert the two agree, because they do not. It pins the gap in
both directions: a new divergence fails it, and *closing* the gap fails it too, with
the message saying to delete the entry. Converging them needs a batch migration and
is a release decision about every self-hoster's upgrade path.
"""

from __future__ import annotations

from collections import Counter
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


# `_orphan_schema_issues` reports 136 differences between a chain-built SQLite
# database and the models, in these categories. Pinning the strings would be 136
# lines of noise nobody reads; pinning the counts catches new drift and stays
# legible.
#
# The categories are not equally interesting:
#
# * `different foreign` / `add_fk` / `remove_fk` — the eighteen constraints
#   `TestForeignKeyParity` above names individually. These change behaviour.
# * `different unique` / `different nullable` / `missing index` — shape differences
#   that can change behaviour, mostly unique *constraints* where the models declare
#   unique *indexes*.
# * `different type` / `different default` — enum columns stored as plain text, and
#   Python-side defaults the migrations never wrote as server defaults. Almost
#   certainly harmless: SQLAlchemy applies the default on insert either way, and
#   nothing reads these columns without going through the models. Counted, not
#   ignored, because "almost certainly" is not a guarantee and a *new* one might not
#   be harmless.
# * `unexpected index` / `unexpected constraint` — schema the migrations created and
#   the models do not declare, so an upgraded installation carries it and a fresh one
#   does not.
#
# Two-sided: a category that grows is fresh drift; one that shrinks is progress that
# has to be recorded here.
STRUCTURAL_DIFFERENCE_COUNTS = {
    "different default": 53,
    "different type": 27,
    "unexpected index": 15,
    "different foreign": 8,
    "different index": 8,
    "different unique": 8,
    "missing index": 8,
    "unexpected constraint": 5,
    "different nullable": 2,
    "structural difference add_fk": 1,
    "structural difference remove_fk": 1,
}


def _category(issue: str) -> str:
    """The kind of difference, with the object it is about stripped off."""
    if issue.startswith("structural difference"):
        return issue
    return " ".join(issue.split()[:2])


class TestStructuralParity:
    """The whole schema, not just its foreign keys.

    Running the entire suite a second time against a chain-built database would
    cover this, slowly. `_orphan_schema_issues` covers it in three seconds and with
    more authority, because it is the comparison the app itself makes when deciding
    whether a database is current: dialect-normalised columns, types, nullability
    and server defaults from Alembic autogenerate, plus explicit checks on primary
    and foreign keys, unique and check constraints, and partial-index predicates.

    Its verdict on the schema a self-hoster upgraded into is that it is **not**
    current. That has a consequence worth knowing before relying on it in support:
    the orphan-rescue path in `run_migrations` can only ever adopt a database built
    by `create_all`, never one built by the chain.
    """

    def test_the_migrated_schema_differs_from_the_models_only_as_recorded(
        self, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        path = tmp_path_factory.mktemp("structural") / "chain.sqlite"
        url = f"sqlite:///{path}"
        command.upgrade(migrate_mod._alembic_config(url), "head")

        engine = create_engine(url)
        try:
            issues = migrate_mod._orphan_schema_issues(engine)  # noqa: SLF001
        finally:
            engine.dispose()

        counts = Counter(_category(issue) for issue in issues)

        grown = {
            category: (count, STRUCTURAL_DIFFERENCE_COUNTS.get(category, 0))
            for category, count in counts.items()
            if count > STRUCTURAL_DIFFERENCE_COUNTS.get(category, 0)
        }
        assert not grown, (
            "the migration chain drifted further from the models: "
            + ", ".join(
                f"{category} {now} (was {before})"
                for category, (now, before) in sorted(grown.items())
            )
            + ". A migration has to change the schema the same way `create_all` "
            "would, which on SQLite means `op.batch_alter_table` — never a DDL "
            "operation guarded by `if not is_sqlite`."
        )

        shrunk = {
            category: (counts.get(category, 0), expected)
            for category, expected in STRUCTURAL_DIFFERENCE_COUNTS.items()
            if counts.get(category, 0) < expected
        }
        assert not shrunk, (
            "the schemas converged, so these counts are stale: "
            + ", ".join(
                f"{category} {now} (recorded {before})"
                for category, (now, before) in sorted(shrunk.items())
            )
            + ". Lower them here."
        )
