"""Whether a database is at the schema this build ships.

A worker never migrates: the API owns the schema. It waits for this to hold,
so a worker that starts before the API has upgraded, or one left on an older
build after it has, never runs code against a schema it was not written for.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from app.db.migrate import _alembic_config, run_migrations, schema_is_current
from app.db.url import normalize_database_url


def _url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'vault.sqlite'}"


class TestSchemaIsCurrent:
    def test_a_migrated_database_is_current(self, tmp_path: Path) -> None:
        url = _url(tmp_path)
        run_migrations(url)

        assert schema_is_current(url) is True

    def test_an_empty_database_is_not(self, tmp_path: Path) -> None:
        assert schema_is_current(_url(tmp_path)) is False

    def test_a_database_one_revision_behind_is_not(self, tmp_path: Path) -> None:
        # The API of a newer build has not upgraded it yet.
        url = _url(tmp_path)
        run_migrations(url)
        command.downgrade(_alembic_config(normalize_database_url(url)), "-1")

        assert schema_is_current(url) is False
