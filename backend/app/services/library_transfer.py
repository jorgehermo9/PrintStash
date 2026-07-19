"""Portable, versioned library archive import/export."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import (
    Collection,
    File,
    FileRevisionStatus,
    FileType,
    Metadata,
    Model,
    ModelStar,
    ModelTagLink,
    PrintJob,
    SavedView,
    Tag,
    User,
)
from app.db.scopes import live
from app.services import ingestion, model_views, taxonomy
from app.services.storage_backend import get_backend

FORMAT = "printstash-library-v1"
MAX_ENTRIES = 20_000
MAX_UNCOMPRESSED = 100 * 1024 * 1024 * 1024


def _json_value(value: object) -> object:
    if hasattr(value, "value"):
        return value.value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def create_archive(session: Session, user: User) -> Path:
    visible_ids = {
        row["id"] for row in model_views.export_payload(session, user)["models"]
    }
    models = session.exec(
        select(Model).where(Model.id.in_(visible_ids), live(Model))
    ).all()
    collection_ids = {
        row.collection_id for row in models if row.collection_id is not None
    }
    collection_paths = (
        {
            row.id: row.path
            for row in session.exec(
                select(Collection).where(Collection.id.in_(collection_ids))
            ).all()
        }
        if collection_ids
        else {}
    )
    files = session.exec(
        select(File).where(File.model_id.in_(visible_ids), live(File))
    ).all()
    file_ids = [row.id for row in files]
    metadata = {
        row.file_id: row
        for row in session.exec(
            select(Metadata).where(Metadata.file_id.in_(file_ids))
        ).all()
    }
    tags = {row.id: row for row in session.exec(select(Tag).where(live(Tag))).all()}
    links = session.exec(
        select(ModelTagLink).where(ModelTagLink.model_id.in_(visible_ids))
    ).all()
    tags_by_model: dict[int, list[str]] = {}
    for link in links:
        tag = tags.get(link.tag_id)
        if tag and link.model_id is not None:
            tags_by_model.setdefault(link.model_id, []).append(tag.name)
    jobs = session.exec(
        select(PrintJob).where(PrintJob.model_id.in_(visible_ids), live(PrintJob))
    ).all()
    stars = set(
        session.exec(
            select(ModelStar.model_id).where(ModelStar.user_id == user.id)
        ).all()
    )
    saved = session.exec(select(SavedView).where(SavedView.user_id == user.id)).all()

    manifest: dict[str, object] = {
        "format": FORMAT,
        "exported_at": utcnow().isoformat(),
        "models": [],
        "print_jobs": [],
        "saved_views": [
            {"name": row.name, "filters": json.loads(row.filters_json)} for row in saved
        ],
    }
    files_by_model: dict[int, list[File]] = {}
    for row in files:
        files_by_model.setdefault(row.model_id, []).append(row)

    fd, filename = tempfile.mkstemp(suffix=".printstash.zip")
    Path(filename).unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(
            filename, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
        ) as archive:
            for model in models:
                artifacts = []
                for artifact in sorted(
                    files_by_model.get(model.id, []), key=lambda item: item.version
                ):
                    entry = f"blobs/{model.hash}/{artifact.version}-{artifact.id}-{Path(artifact.original_filename).name}"
                    backend = get_backend()
                    with backend.local_path(artifact.path) as source:
                        archive.write(source, entry)
                    md = metadata.get(artifact.id)
                    artifacts.append(
                        {
                            "source_id": artifact.id,
                            "entry": entry,
                            "original_filename": artifact.original_filename,
                            "file_type": artifact.file_type.value,
                            "version": artifact.version,
                            "size_bytes": artifact.size_bytes,
                            "sha256": artifact.sha256,
                            "revision_label": artifact.revision_label,
                            "revision_status": _json_value(artifact.revision_status),
                            "revision_notes": artifact.revision_notes,
                            "is_recommended": artifact.is_recommended,
                            "metadata": {
                                key: _json_value(value)
                                for key, value in md.model_dump(
                                    # created_at is set fresh by Metadata's
                                    # default_factory on import — carrying the
                                    # source instance's ISO string through
                                    # crashes ingestion.persist_artifact's
                                    # write (SQLite datetime columns require a
                                    # real datetime, not a string).
                                    exclude={"id", "file_id", "created_at"}
                                ).items()
                            }
                            if md
                            else {},
                        }
                    )
                manifest["models"].append(
                    {  # type: ignore[union-attr]
                        "source_id": model.id,
                        "name": model.name,
                        "slug": model.slug,
                        "hash": model.hash,
                        "description": model.description,
                        "source_url": model.source_url,
                        "collection": collection_paths.get(model.collection_id),
                        "tags": sorted(tags_by_model.get(model.id, [])),
                        "starred": model.id in stars,
                        "artifacts": artifacts,
                    }
                )
            for job in jobs:
                manifest["print_jobs"].append(
                    {  # type: ignore[union-attr]
                        "source_id": job.id,
                        "model_source_id": job.model_id,
                        "file_source_id": job.file_id,
                        "remote_filename": job.remote_filename,
                        "printer_name": job.printer_name,
                        "state": job.state.value,
                        "source": job.source,
                        "filament_used_g": job.filament_used_g,
                        "actual_duration_s": job.actual_duration_s,
                        "cost": job.cost,
                        "filament_g_effective": job.filament_g_effective,
                        "started_at": _json_value(job.started_at),
                        "finished_at": _json_value(job.finished_at),
                    }
                )
            archive.writestr("manifest.json", json.dumps(manifest, indent=2))
        return Path(filename)
    except Exception:
        Path(filename).unlink(missing_ok=True)
        raise
    finally:
        try:
            import os

            os.close(fd)
        except OSError:
            pass


def _safe_entry(name: str) -> bool:
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts and "\\" not in name


def import_archive(session: Session, archive_path: Path, user: User) -> dict[str, int]:
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        if (
            len(infos) > MAX_ENTRIES
            or sum(item.file_size for item in infos) > MAX_UNCOMPRESSED
        ):
            raise ValueError("archive_too_large")
        if any(not _safe_entry(item.filename) for item in infos):
            raise ValueError("unsafe_archive_path")
        try:
            manifest = json.loads(archive.read("manifest.json"))
        except (KeyError, json.JSONDecodeError) as exc:
            raise ValueError("invalid_manifest") from exc
        if manifest.get("format") != FORMAT or not isinstance(
            manifest.get("models"), list
        ):
            raise ValueError("unsupported_archive_format")

        # Validate every blob before first database/storage write.
        for model_data in manifest["models"]:
            for artifact in model_data.get("artifacts", []):
                try:
                    payload = archive.read(artifact["entry"])
                except KeyError as exc:
                    raise ValueError("archive_blob_missing") from exc
                if (
                    len(payload) != artifact["size_bytes"]
                    or hashlib.sha256(payload).hexdigest() != artifact["sha256"]
                ):
                    raise ValueError("archive_blob_hash_mismatch")

        created_models = created_files = skipped_files = 0
        source_models: dict[int, Model] = {}
        source_files: dict[int, File] = {}
        with tempfile.TemporaryDirectory(prefix="printstash-import-") as tempdir:
            for model_data in manifest["models"]:
                model = session.exec(
                    select(Model).where(Model.hash == model_data["hash"], live(Model))
                ).first()
                if model is None:
                    collection = (
                        taxonomy.resolve_or_create_collection(
                            session, model_data.get("collection") or ""
                        )
                        if model_data.get("collection")
                        else None
                    )
                    model = Model(
                        name=model_data["name"],
                        slug=model_data["slug"],
                        hash=model_data["hash"],
                        description=model_data.get("description"),
                        source_url=model_data.get("source_url"),
                        collection_id=collection.id if collection else None,
                        created_by=user.id,
                    )
                    session.add(model)
                    session.commit()
                    session.refresh(model)
                    created_models += 1
                source_models[model_data["source_id"]] = model
                resolved_tags = taxonomy.resolve_or_create_tags(
                    session, model_data.get("tags", [])
                )
                existing_tag_ids = set(
                    session.exec(
                        select(ModelTagLink.tag_id).where(
                            ModelTagLink.model_id == model.id
                        )
                    ).all()
                )
                for tag in resolved_tags:
                    if tag.id not in existing_tag_ids:
                        session.add(ModelTagLink(model_id=model.id, tag_id=tag.id))
                session.commit()
                for artifact_data in sorted(
                    model_data.get("artifacts", []), key=lambda row: row["version"]
                ):
                    existing = session.exec(
                        select(File).where(
                            File.model_id == model.id,
                            File.sha256 == artifact_data["sha256"],
                            live(File),
                        )
                    ).first()
                    if existing:
                        source_files[
                            artifact_data.get("source_id", artifact_data["version"])
                        ] = existing
                        skipped_files += 1
                        continue
                    staged = (
                        Path(tempdir)
                        / f"{model.id}-{artifact_data['version']}-{Path(artifact_data['original_filename']).name}"
                    )
                    with (
                        archive.open(artifact_data["entry"]) as src,
                        staged.open("wb") as dst,
                    ):
                        shutil.copyfileobj(src, dst)
                    file_row = ingestion.persist_artifact(
                        session,
                        model=model,
                        staged_path=staged,
                        original_filename=artifact_data["original_filename"],
                        file_type=FileType(artifact_data["file_type"]),
                        blob_hash=artifact_data["sha256"],
                        meta=artifact_data.get("metadata", {}),
                        thumb_bytes=None,
                        overwrite_thumbnail=False,
                        revision_label=artifact_data.get("revision_label"),
                        revision_status=FileRevisionStatus(
                            artifact_data["revision_status"]
                        )
                        if artifact_data.get("revision_status")
                        else None,
                        revision_notes=artifact_data.get("revision_notes"),
                        is_recommended=artifact_data.get("is_recommended", False),
                    )
                    source_files[
                        artifact_data.get("source_id", artifact_data["version"])
                    ] = file_row
                    created_files += 1
                if model_data.get("starred"):
                    exists = session.exec(
                        select(ModelStar).where(
                            ModelStar.user_id == user.id,
                            ModelStar.model_id == model.id,
                        )
                    ).first()
                    if exists is None:
                        session.add(ModelStar(user_id=user.id, model_id=model.id))
                        session.commit()

        imported_jobs = 0
        for job_data in manifest.get("print_jobs", []):
            model = source_models.get(job_data.get("model_source_id"))
            file_row = source_files.get(job_data.get("file_source_id"))
            if model is None or file_row is None:
                continue
            started_at = (
                datetime.fromisoformat(job_data["started_at"])
                if job_data.get("started_at")
                else None
            )
            duplicate = session.exec(
                select(PrintJob).where(
                    PrintJob.model_id == model.id,
                    PrintJob.file_id == file_row.id,
                    PrintJob.remote_filename == job_data["remote_filename"],
                    PrintJob.started_at == started_at,
                )
            ).first()
            if duplicate:
                continue
            session.add(
                PrintJob(
                    model_id=model.id,
                    file_id=file_row.id,
                    remote_filename=job_data["remote_filename"],
                    printer_name=job_data.get("printer_name"),
                    state=job_data["state"],
                    source=job_data.get("source") or "archive",
                    filament_used_g=job_data.get("filament_used_g"),
                    actual_duration_s=job_data.get("actual_duration_s"),
                    cost=job_data.get("cost"),
                    filament_g_effective=job_data.get("filament_g_effective"),
                    started_at=started_at,
                    finished_at=(
                        datetime.fromisoformat(job_data["finished_at"])
                        if job_data.get("finished_at")
                        else None
                    ),
                )
            )
            imported_jobs += 1
        for saved_data in manifest.get("saved_views", []):
            existing = session.exec(
                select(SavedView).where(
                    SavedView.user_id == user.id, SavedView.name == saved_data["name"]
                )
            ).first()
            if existing is None:
                session.add(
                    SavedView(
                        user_id=user.id,
                        name=saved_data["name"],
                        filters_json=json.dumps(saved_data.get("filters", {})),
                    )
                )
        session.commit()
        return {
            "created_models": created_models,
            "created_files": created_files,
            "skipped_files": skipped_files,
            "imported_jobs": imported_jobs,
        }
