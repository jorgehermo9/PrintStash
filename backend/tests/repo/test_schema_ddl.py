"""The models render every foreign key they declare, on the dialect we ship by default.

`app/db/models.py` has a foreign-key cycle — `files.model_id -> models.id` and
`models.thumbnail_file_id -> files.id` — and SQLAlchemy resolves it per dialect.
When `create_all` targets a dialect that can `ALTER TABLE ... ADD CONSTRAINT` it
lifts the cycle-breaking constraints out of `CREATE TABLE` and sets
`ForeignKeyConstraint._create_rule` so they are not rendered inline. That attribute
lives on the shared `MetaData`, so the decision is process-wide and permanent: a
later `create_all` against SQLite, which cannot ALTER, silently omits them.

The default installation is SQLite and its schema is built by `create_all`
(`app/db/migrate.run_migrations`, fresh path). A run that lost those constraints
would produce a database with no referential integrity between models and files —
and `foreign_keys=ON` is a production pragma, so the app relies on them.

This is what caught it, on a suite that had been green: see
`tests/integration/postgres/conftest.py` for the leak, the failure it produced two
runs in five, and the fixture that undoes it. This test is the tripwire, so a future
leak fails here with the reason attached rather than as an `OrphanSchemaError` in an
unrelated backup test.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.schema import CreateTable
from sqlmodel import SQLModel

import app.db.models  # noqa: F401 - registers every table on SQLModel.metadata

# The two tables in the cycle, and therefore the only two whose constraints
# SQLAlchemy ever lifts out. Naming them rather than sweeping every table keeps the
# failure message pointed at the actual mechanism.
CYCLE_TABLES = ("files", "models")


@pytest.fixture(scope="module")
def sqlite_dialect():
    """A throwaway SQLite engine, used only to compile DDL — nothing is executed."""
    engine = create_engine("sqlite://")
    try:
        yield engine
    finally:
        engine.dispose()


class TestCreateTable:
    @pytest.mark.parametrize("table_name", CYCLE_TABLES)
    def test_renders_every_declared_foreign_key_inline_for_sqlite(
        self, sqlite_dialect, table_name: str
    ) -> None:
        table = SQLModel.metadata.tables[table_name]
        declared = len(table.foreign_key_constraints)

        ddl = str(CreateTable(table).compile(sqlite_dialect))

        assert ddl.count("FOREIGN KEY") == declared, (
            f"`{table_name}` declares {declared} foreign keys but its SQLite "
            f"CREATE TABLE renders {ddl.count('FOREIGN KEY')}. Something in this "
            "process ran `create_all` against an ALTER-capable dialect and left "
            "`_create_rule` set on the shared metadata — see this module's "
            "docstring and tests/integration/postgres/conftest.py."
        )

    @pytest.mark.parametrize("table_name", CYCLE_TABLES)
    def test_no_constraint_is_marked_for_alter_only_emission(
        self, table_name: str
    ) -> None:
        # The same invariant one level down, so the failure names the attribute
        # rather than a count. `_create_rule` is what suppresses inline rendering.
        table = SQLModel.metadata.tables[table_name]

        suppressed = [
            constraint.name or tuple(constraint.column_keys)
            for constraint in table.foreign_key_constraints
            if constraint._create_rule is not None  # noqa: SLF001
        ]

        assert not suppressed, (
            f"these `{table_name}` foreign keys are marked for ALTER-only emission "
            f"and will not appear in a SQLite CREATE TABLE: {suppressed}"
        )
