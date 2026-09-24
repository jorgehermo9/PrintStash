"""The HTTP shape of a toolpath, shared by the owner and share-link routes."""

from __future__ import annotations

from fastapi import HTTPException, Response
from fastapi.responses import JSONResponse
from sqlmodel import Session

from app.core.config import settings
from app.db.models import File, FileType
from app.modules.derivatives import records
from app.modules.derivatives.kinds import TOOLPATH
from app.modules.media import toolpath
from app.modules.storage.storage_backend.runtime import get_backend

_HEADERS = {"Cache-Control": "private, no-store"}


def toolpath_response(session: Session, file: File) -> Response:
    """ASCII G-code from the Artifact; binary G-code from its derivative.

    Nothing here converts: a binary toolpath still being derived answers 202
    with its derivative state, and a failed conversion answers 422 with the
    reason the derivative recorded.
    """
    if file.file_type != FileType.GCODE:
        raise HTTPException(status_code=404, detail="toolpath_not_gcode")
    if not toolpath.is_binary_gcode(file):
        return Response(
            content=toolpath.read_ascii(file), media_type="text/plain", headers=_HEADERS
        )
    row = records.rows_for(session, file).get(TOOLPATH)
    if row is not None and row.state == "failed":
        raise HTTPException(
            status_code=422, detail=row.failure_reason or "toolpath_failed"
        )
    if row is None or row.state != "ready" or not row.storage_key:
        state = "pending" if row is None else str(row.state)
        return JSONResponse(status_code=202, content={"state": state}, headers=_HEADERS)
    content = get_backend().read_bytes(row.storage_key)
    if len(content) > settings.toolpath_output_max_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail="toolpath_output_too_large")
    return Response(content=content, media_type="text/plain", headers=_HEADERS)
