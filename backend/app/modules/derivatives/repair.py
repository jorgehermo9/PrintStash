"""Re-deriving outputs an audit found broken.

Two entry points, one rule: forget the kind at its current recipe so it is
pending again, then derive it.

- ``request`` does it asynchronously, for a request path (an administrator's
  "repair" click): invalidate, commit, nudge.
- ``now`` derives synchronously, for a caller already running inside a Job
  (an audit's automatic repair), which must verify the result before it
  records the finding as repaired.
"""

from __future__ import annotations

from sqlmodel import Session, col, select

from app.db.models import File, Model
from app.db.scopes import live
from app.db.session import get_session_factory

from . import producers, records
from .kinds import (
    GCODE_DEFINITION,
    MESH_DEFINITION,
    MESH_TYPES,
    TOOLPATH_DEFINITION,
    groups_for,
)

_PRODUCERS = {
    MESH_DEFINITION: producers.derive_mesh,
    GCODE_DEFINITION: producers.derive_gcode,
    TOOLPATH_DEFINITION: producers.derive_toolpath,
}


def representative(session: Session, model_id: int) -> File | None:
    """The Artifact whose thumbnail represents a Model (or should)."""
    model = session.get(Model, model_id)
    if model is None or model.deleted_at is not None:
        return None
    if model.thumbnail_file_id is not None:
        current = session.exec(
            select(File).where(File.id == model.thumbnail_file_id, live(File))
        ).first()
        if current is not None:
            return current
    return session.exec(
        select(File)
        .where(File.model_id == model_id, live(File))
        .order_by(col(File.file_type).in_(MESH_TYPES).desc(), col(File.id).desc())
    ).first()


def request(session: Session, file: File, kinds: list[str]) -> bool:
    """Make ``kinds`` pending for ``file`` and nudge its producers."""
    from app.modules.work import nudge

    records.invalidate(session, file, kinds)
    session.commit()
    groups = [group for group in groups_for(file) if set(kinds) & set(group.kinds)]
    for group in groups:
        nudge(group.definition)
    return bool(groups)


def now(file_id: int, kinds: list[str]) -> dict[str, str]:
    """Invalidate and derive ``kinds`` for one Artifact in this thread."""
    with get_session_factory().scoped_session() as session:
        file = session.exec(select(File).where(File.id == file_id, live(File))).first()
        if file is None:
            return {}
        records.invalidate(session, file, kinds)
        session.commit()
        groups = [
            group.definition
            for group in groups_for(file)
            if set(kinds) & set(group.kinds)
        ]
    outcome: dict[str, str] = {}
    for definition in groups:
        outcome.update(_PRODUCERS[definition](file_id).kinds)
    return outcome
