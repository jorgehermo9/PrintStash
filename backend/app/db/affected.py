"""Row counts of bulk DML statements."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy.engine import CursorResult
from sqlalchemy.sql.dml import Delete, Update
from sqlmodel import Session


def affected(session: Session, statement: Update | Delete) -> int:
    """Execute a bulk ``UPDATE`` or ``DELETE`` and return how many rows it hit."""
    result = cast(CursorResult[Any], session.execute(statement))
    return int(result.rowcount or 0)
