"""Compute and publish derivatives for one Artifact.

A producer reads the Artifact's bytes once, derives only the kinds that are
still needed, and publishes each output with its row in one transaction per
kind. Outputs stay with their owners: geometry and slicer facts in
``metadata`` (plus material requirements and detected profiles), thumbnails as
immutable objects behind ``File.thumbnail_path``, toolpaths as a stored blob
referenced by the derivative row.
"""

from __future__ import annotations

import tempfile
import time
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from printstash_core.mesh.similarity.budgets import MAX_ANALYSIS_FACES
from sqlmodel import Session, delete, select

from app.core.config import settings
from app.core.errors import OperationError
from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import (
    ArtifactDerivative,
    ArtifactMaterialRequirement,
    File,
    Metadata,
)
from app.db.scopes import live
from app.db.session import get_session_factory
from app.modules.media import gcode_parser, thumbnail
from app.modules.media.thumbnail_engine import (
    ThumbnailEngine,
    ThumbnailFailureReason,
    ThumbnailRequest,
)
from app.modules.media.thumbnail_publication import (
    ThumbnailPublicationError,
    point_at,
    publish_thumbnail,
)
from app.modules.storage.artifact_content import ArtifactContentError, resolve
from app.modules.storage.capacity import CapacityManager, CapacityResource
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.storage.storage_ownership import publish_file

from . import records
from .kinds import (
    GCODE_DEFINITION,
    MESH_DEFINITION,
    METADATA,
    THUMBNAIL,
    TOOLPATH,
    TOOLPATH_DEFINITION,
    group,
)

logger = get_logger(__name__)

_GEOMETRY_FIELDS = (
    "bbox_x_mm",
    "bbox_y_mm",
    "bbox_z_mm",
    "volume_mm3",
    "triangle_count",
)
_DETERMINISTIC = {
    ThumbnailFailureReason.INVALID_SOURCE.value,
    ThumbnailFailureReason.UNSUPPORTED_FORMAT.value,
    ThumbnailFailureReason.NO_GEOMETRY.value,
    ThumbnailFailureReason.RESOURCE_LIMIT.value,
    ThumbnailFailureReason.RENDERER_NO_OUTPUT.value,
}


class ArtifactGone(Exception):
    """The Artifact was trashed or deleted; there is nothing to derive."""


@dataclass
class Outcome:
    """What one producer run did, per kind, for the Job's result."""

    kinds: dict[str, str]

    def as_result(self) -> dict[str, Any]:
        return {"derivatives": dict(sorted(self.kinds.items()))}


def _announce(file_row: File, outcome: Outcome) -> Outcome:
    """Tell viewers of the Model that its Artifact's derivatives changed."""
    from app.modules.work import events

    assert file_row.id is not None
    for kind, state in outcome.kinds.items():
        events.derivative_changed(
            model_id=file_row.model_id, file_id=file_row.id, kind=kind, state=state
        )
    return outcome


def _live_file(session: Session, file_id: int) -> File:
    file_row = session.exec(select(File).where(File.id == file_id, live(File))).first()
    if file_row is None:
        raise ArtifactGone(str(file_id))
    return file_row


def _begin(file_id: int, definition: str) -> tuple[File, set[str]] | None:
    now = utcnow()
    kinds = group(definition).kinds
    with get_session_factory().scoped_session() as session:
        try:
            file_row = _live_file(session, file_id)
        except ArtifactGone:
            return None
        needed = records.needed(session, file_row, kinds, now=now)
        for kind in needed:
            records.begin(session, file_row, kind, kinds[kind], now=now)
        session.commit()
        session.refresh(file_row)
        session.expunge(file_row)
    return file_row, needed


def _row(session: Session, file_id: int, kind: str, recipe: int) -> ArtifactDerivative:
    row = session.exec(
        select(ArtifactDerivative).where(
            ArtifactDerivative.file_id == file_id,
            ArtifactDerivative.kind == kind,
            ArtifactDerivative.recipe_version == recipe,
        )
    ).first()
    if row is None:
        raise RuntimeError("derivative_row_missing")
    return row


def _metadata_row(session: Session, file_id: int) -> Metadata:
    row = session.exec(select(Metadata).where(Metadata.file_id == file_id)).first()
    if row is None:
        row = Metadata(file_id=file_id)
        session.add(row)
    return row


def _fail(
    file_id: int, kind: str, recipe: int, reason: str, *, deterministic: bool
) -> None:
    with get_session_factory().scoped_session() as session:
        row = _row(session, file_id, kind, recipe)
        records.mark_failed(
            session, row, reason, now=utcnow(), deterministic=deterministic
        )
        session.commit()


def _publish_thumbnail(
    file_row: File,
    image: bytes,
    *,
    kind_recipe: int,
    normalize: bool,
    strategy: str,
    complete: bool,
    duration_ms: int | None = None,
    peak_rss_bytes: int | None = None,
) -> str:
    assert file_row.id is not None
    try:
        encoded = thumbnail.to_webp(image, normalize=normalize)
    except ValueError:
        _fail(file_row.id, THUMBNAIL, kind_recipe, "invalid_source", deterministic=True)
        return "failed"
    backend = get_backend()
    with get_session_factory().scoped_session() as session:
        fresh = _live_file(session, file_row.id)
        try:
            published = publish_thumbnail(
                session,
                backend,
                fresh,
                encoded,
                recipe_tag=f"{THUMBNAIL}:{kind_recipe}:w{settings.model_thumbnail_width}",
            )
        except (ThumbnailPublicationError, OperationError, OSError):
            session.rollback()
            logger.exception(
                "thumbnail publication failed", extra={"file_id": fresh.id}
            )
            _fail(fresh.id, THUMBNAIL, kind_recipe, "storage", deterministic=False)  # type: ignore[arg-type]
            return "failed"
        point_at(session, fresh, published.key)
        records.mark_ready(
            session,
            _row(session, fresh.id, THUMBNAIL, kind_recipe),  # type: ignore[arg-type]
            now=utcnow(),
            storage_key=published.key,
            output={
                "sha256": published.sha256,
                "size": published.size,
                "etag": published.etag,
                "width": int(settings.model_thumbnail_width),
                "height": round(int(settings.model_thumbnail_width) * 3 / 4),
                "strategy": strategy,
                "complete": complete,
            },
            duration_ms=duration_ms,
            peak_rss_bytes=peak_rss_bytes,
        )
        session.commit()
    return "ready"


def _thumbnail_failure(file_id: int, recipe: int, reason: str | None) -> str:
    value = reason or ThumbnailFailureReason.RENDERER_NO_OUTPUT.value
    _fail(file_id, THUMBNAIL, recipe, value, deterministic=value in _DETERMINISTIC)
    return "failed"


def _derive_mesh(file_id: int) -> Outcome:
    """Geometry and a rendered thumbnail from one mesh load."""
    begun = _begin(file_id, MESH_DEFINITION)
    if begun is None:
        return Outcome({})
    file_row, needed = begun
    kinds = group(MESH_DEFINITION).kinds
    outcome: dict[str, str] = {}
    if not needed:
        return Outcome(outcome)
    from app.modules.ingestion.extensions import after_commit, extraction_options

    options = extraction_options(get_session_factory()) if METADATA in needed else {}
    started = time.monotonic()
    try:
        with ExitStack() as stack:
            claim = CapacityManager(get_session_factory()).reserve(
                f"derive:mesh:{file_id}:{time.monotonic_ns()}",
                [
                    CapacityResource.for_path(
                        Path(tempfile.gettempdir()),
                        file_row.size_bytes * 3 + 16 * 1024**2,
                        role="thumbnail rendering",
                    )
                ],
            )
            stack.callback(claim.release)
            source = stack.enter_context(
                resolve(file_row).materialize(capacity_claimed=True)
            )
            result = ThumbnailEngine().generate(
                ThumbnailRequest(
                    path=source,
                    file_type=file_row.file_type.value,
                    include_geometry=METADATA in needed,
                    include_thumbnail=THUMBNAIL in needed,
                    reason="derivative",
                    output_format="WEBP",
                    include_fingerprint=bool(options.get("include_fingerprint")),
                    triangle_cap=int(options.get("triangle_cap") or MAX_ANALYSIS_FACES),
                )
            )
    except ArtifactContentError:
        for kind in needed:
            _fail(file_id, kind, kinds[kind], "invalid_source", deterministic=False)
            outcome[kind] = "failed"
        return Outcome(outcome)
    duration_ms = int((time.monotonic() - started) * 1000)

    if METADATA in needed:
        with get_session_factory().scoped_session() as session:
            meta = _metadata_row(session, file_id)
            for name in _GEOMETRY_FIELDS:
                setattr(meta, name, result.geometry.get(name))
            session.add(meta)
            records.mark_ready(
                session,
                _row(session, file_id, METADATA, kinds[METADATA]),
                now=utcnow(),
                output={"triangle_count": result.geometry.get("triangle_count")},
                duration_ms=duration_ms,
                peak_rss_bytes=result.peak_rss_bytes,
            )
            session.commit()
        outcome[METADATA] = "ready"
        if result.fingerprint_result is not None:
            try:
                after_commit(
                    get_session_factory(), file_id, None, result.fingerprint_result
                )
            except Exception:  # noqa: BLE001 - similarity evidence is re-derivable
                logger.warning(
                    "fingerprint publication failed", extra={"file_id": file_id}
                )

    if THUMBNAIL in needed:
        if result.image is None:
            outcome[THUMBNAIL] = _thumbnail_failure(
                file_id,
                kinds[THUMBNAIL],
                result.failure_reason.value if result.failure_reason else None,
            )
        else:
            outcome[THUMBNAIL] = _publish_thumbnail(
                file_row,
                result.image,
                kind_recipe=kinds[THUMBNAIL],
                normalize=True,
                strategy=result.strategy.value,
                complete=result.complete,
                duration_ms=duration_ms,
                peak_rss_bytes=result.peak_rss_bytes,
            )
    return Outcome(outcome)


def _replace_material_requirements(
    session: Session, file_id: int, requirements: Any
) -> None:
    session.execute(
        delete(ArtifactMaterialRequirement).where(
            ArtifactMaterialRequirement.file_id == file_id  # type: ignore[arg-type]
        )
    )
    if not isinstance(requirements, list):
        return
    for requirement in requirements:
        if not isinstance(requirement, dict):
            continue
        material_type = requirement.get("material_type")
        if not isinstance(material_type, str) or not material_type.strip():
            continue
        session.add(
            ArtifactMaterialRequirement(
                file_id=file_id,
                tool_index=int(requirement.get("tool_index") or 0),
                material_type=material_type.strip(),
                color_hex=requirement.get("color_hex"),
            )
        )


def _derive_gcode(file_id: int) -> Outcome:
    """Slicer metadata and the embedded thumbnail from one header read."""
    begun = _begin(file_id, GCODE_DEFINITION)
    if begun is None:
        return Outcome({})
    file_row, needed = begun
    kinds = group(GCODE_DEFINITION).kinds
    outcome: dict[str, str] = {}
    if not needed:
        return Outcome(outcome)
    started = time.monotonic()
    meta: dict[str, Any] = {}
    image: bytes | None = None
    try:
        with resolve(file_row).materialize() as source:
            if METADATA in needed:
                meta = gcode_parser.parse(source)
            if THUMBNAIL in needed:
                image = thumbnail.extract(source)
    except ArtifactContentError:
        for kind in needed:
            _fail(file_id, kind, kinds[kind], "invalid_source", deterministic=False)
            outcome[kind] = "failed"
        return Outcome(outcome)
    duration_ms = int((time.monotonic() - started) * 1000)

    if METADATA in needed:
        from app.modules.printing.profile_detection import upsert_detected_profiles

        with get_session_factory().scoped_session() as session:
            row = _metadata_row(session, file_id)
            for name, value in meta.items():
                if name in Metadata.model_fields and name not in {
                    "id",
                    "file_id",
                    "created_at",
                }:
                    setattr(row, name, value)
            session.add(row)
            _replace_material_requirements(
                session, file_id, meta.get("material_requirements")
            )
            records.mark_ready(
                session,
                _row(session, file_id, METADATA, kinds[METADATA]),
                now=utcnow(),
                output={"slicer": meta.get("slicer_name")},
                duration_ms=duration_ms,
            )
            session.commit()
            try:
                upsert_detected_profiles(session, meta)
            except Exception:  # noqa: BLE001 - a detected profile is optional
                session.rollback()
                logger.exception("profile detection failed", extra={"file_id": file_id})
        outcome[METADATA] = "ready"

    if THUMBNAIL in needed:
        if image is None:
            with get_session_factory().scoped_session() as session:
                records.mark_skipped(
                    session,
                    _row(session, file_id, THUMBNAIL, kinds[THUMBNAIL]),
                    "no_embedded_thumbnail",
                    now=utcnow(),
                )
                session.commit()
            outcome[THUMBNAIL] = "skipped"
        else:
            outcome[THUMBNAIL] = _publish_thumbnail(
                file_row,
                image,
                kind_recipe=kinds[THUMBNAIL],
                normalize=False,
                strategy="embedded",
                complete=True,
                duration_ms=duration_ms,
            )
    return Outcome(outcome)


def _derive_toolpath(file_id: int) -> Outcome:
    """A converted ASCII toolpath for a binary G-code Artifact."""
    from app.modules.media import toolpath

    begun = _begin(file_id, TOOLPATH_DEFINITION)
    if begun is None:
        return Outcome({})
    file_row, needed = begun
    recipe = group(TOOLPATH_DEFINITION).kinds[TOOLPATH]
    if TOOLPATH not in needed:
        return Outcome({})
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="printstash-toolpath-") as directory:
        try:
            output = toolpath.convert(file_row, Path(directory))
        except OperationError as exc:
            deterministic = exc.kind.value in {
                "unprocessable",
                "too_large",
                "not_found",
            }
            _fail(file_id, TOOLPATH, recipe, exc.code, deterministic=deterministic)
            return Outcome({TOOLPATH: "failed"})
        except ArtifactContentError:
            _fail(file_id, TOOLPATH, recipe, "invalid_source", deterministic=False)
            return Outcome({TOOLPATH: "failed"})
        backend = get_backend()
        key = backend.blob_key(
            "_derivatives", 0, f"{file_row.sha256}-toolpath-r{recipe}.gcode"
        )
        with get_session_factory().scoped_session() as session:
            receipt = publish_file(
                session,
                backend,
                key,
                output,
                object_kind="toolpath",
                sha256=None,
                move=True,
            )
            records.mark_ready(
                session,
                _row(session, file_id, TOOLPATH, recipe),
                now=utcnow(),
                storage_key=receipt.key,
                output={"size": receipt.size},
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            session.commit()
    return Outcome({TOOLPATH: "ready"})


def _announced(produce):
    def run(file_id: int) -> Outcome:
        outcome = produce(file_id)
        if outcome.kinds:
            with get_session_factory().scoped_session() as session:
                file_row = session.get(File, file_id)
                if file_row is not None:
                    session.expunge(file_row)
            if file_row is not None:
                _announce(file_row, outcome)
        return outcome

    run.__doc__ = produce.__doc__
    return run


derive_mesh = _announced(_derive_mesh)
derive_gcode = _announced(_derive_gcode)
derive_toolpath = _announced(_derive_toolpath)
