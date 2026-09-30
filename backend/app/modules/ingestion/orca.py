"""Validate Orca native context and resolve exact source-Model lineage."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pydantic import ValidationError
from sqlmodel import Session, select

from app.db.models import CollectionRole, File, FileType, Model, User
from app.db.scopes import live
from app.modules.identity import rbac
from app.modules.library.model_views.access import accessible_live_model_ids_stmt
from app.schemas.orca import OrcaNativeContext

SOURCE_FILE_TYPES = (
    FileType.STL,
    FileType.THREE_MF,
    FileType.OBJ,
    FileType.STEP,
    FileType.DXF,
)


class OrcaContextError(ValueError):
    """A stable client-facing Orca intake error."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SourceResolution:
    status: str
    model: Model | None = None


def parse_native_context(value: str | None) -> OrcaNativeContext | None:
    if value is None or not value.strip():
        return None
    if len(value.encode("utf-8")) > 64 * 1024:
        raise OrcaContextError("orca_native_context_too_large")
    try:
        return OrcaNativeContext.model_validate_json(value)
    except ValidationError as exc:
        raise OrcaContextError("orca_native_context_invalid") from exc


def normalized_source_filename(value: str) -> str:
    normalized = value.strip().strip("\"'").replace("\\", "/")
    filename = PurePosixPath(normalized).name
    if not filename or filename in {".", ".."}:
        raise OrcaContextError("orca_source_filename_invalid")
    return filename


def resolve_source_model(
    session: Session, user: User, context: OrcaNativeContext
) -> SourceResolution:
    if context.classification != "single_object" or not context.source.filename:
        return SourceResolution("not_attachable")
    filename = normalized_source_filename(context.source.filename)
    accessible_ids = accessible_live_model_ids_stmt(session, user)
    rows = session.exec(
        select(Model)
        .join(File, File.model_id == Model.id)  # pyright: ignore[reportArgumentType]
        .where(
            Model.id.in_(accessible_ids),  # type: ignore[union-attr]
            File.original_filename == filename,
            File.file_type.in_(SOURCE_FILE_TYPES),  # type: ignore[union-attr]
            live(File),
            live(Model),
        )
        .distinct()
    ).all()
    editable = [
        model
        for model in rows
        if rbac.role_allows(
            rbac.effective_collection_role(session, user, model.collection_id),
            CollectionRole.EDIT,
        )
    ]
    if not editable:
        return SourceResolution("not_found")
    if len(editable) > 1:
        return SourceResolution("ambiguous")
    return SourceResolution("matched", editable[0])


def display_name(
    context: OrcaNativeContext | None,
    legacy_name: str | None,
    original_filename: str,
) -> str:
    if context is not None:
        candidates = (
            context.source.project_name,
            context.source.plate_name,
            context.source.first_object_name,
            context.source.basename,
        )
        for candidate in candidates:
            if candidate and candidate.strip():
                return candidate.strip()
    stem = Path(original_filename).stem
    return (legacy_name or stem).strip() or stem


def stored_context(context: OrcaNativeContext | None) -> dict[str, object] | None:
    return (
        context.model_dump(mode="json", exclude_unset=True)
        if context is not None
        else None
    )


def ingestion_key(user_id: int, submission_id: str, context: OrcaNativeContext) -> str:
    if re.fullmatch(r"[0-9a-fA-F]{64}", submission_id) is None:
        raise OrcaContextError("orca_submission_id_invalid")
    identity = json.dumps(
        context.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(
        f"orca-v1:{user_id}:{submission_id.lower()}:{identity}".encode()
    ).hexdigest()
