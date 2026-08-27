"""Storage contracts the ORM layer must keep with the migrations that built it.

A SQLModel table declaration and an Alembic migration describe the same column
twice, in two places, and nothing checks that the two descriptions agree. Where
they disagree the mismatch is invisible until a real row is read back — and then
it is a 500 on a listing endpoint, not a validation error at write time.

Enum columns are the sharp case, because SQLAlchemy stores the enum *member name*
rather than its value. `ExternalLibraryWatchMode.AUTO` is written as `"AUTO"`, so
a migration whose `server_default` is the lowercase value writes rows the ORM
cannot read: every pre-existing library raises `LookupError` on load and the
libraries listing 500s for exactly the installations that upgraded. Asserting on
the raw stored string is the only way to see it, since reading through the ORM
round-trips the value and hides the disagreement.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlmodel import Session

from app.db.models import ExternalLibraryWatchMode
from tests.factories import build_external_library


class TestExternalLibrary:
    def test_stores_the_watch_mode_as_the_enum_member_name(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        nas = tmp_path / "nas"
        nas.mkdir()

        library = build_external_library(
            db_session, nas, name="nas", watch_mode=ExternalLibraryWatchMode.AUTO
        )

        raw = db_session.execute(
            text("SELECT watch_mode FROM external_libraries WHERE id = :id"),
            {"id": library.id},
        ).scalar_one()
        assert raw == "AUTO", "migration server_default must match the member name"
