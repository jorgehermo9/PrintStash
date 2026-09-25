"""Peak estimates used by existing heavy workflows, on their actual roots."""

from __future__ import annotations

import tempfile
from pathlib import Path

from app.core.config import settings
from app.modules.storage.capacity import CapacityResource
from app.modules.storage.storage_backend.runtime import get_backend


def vault_allocation(size_bytes: int, *, role: str = "vault") -> CapacityResource:
    backend = get_backend()
    direct = backend.direct_path(backend.thumbnail_key(0))
    if direct is not None:
        return CapacityResource.for_path(settings.data_dir, size_bytes, role=role)
    target = backend.storage_target
    return CapacityResource.for_quota(
        target.target_ref if target else backend.backend_name,
        size_bytes,
        None,
        role=role,
    )


def archive_import(size_bytes: int) -> list[CapacityResource]:
    return [
        CapacityResource.for_path(
            settings.staging_dir, size_bytes, role="archive extraction"
        ),
        vault_allocation(size_bytes),
    ]


def archive_export(size_bytes: int) -> list[CapacityResource]:
    # Deflate can expand incompressible bytes; metadata and the largest
    # materialized source overlap the final archive on the same temp volume.
    return [
        CapacityResource.for_path(
            Path(tempfile.gettempdir()),
            size_bytes * 2 + 16 * 1024**2,
            role="archive export",
        )
    ]


def backup_create(size_bytes: int, destination: Path) -> list[CapacityResource]:
    peak = size_bytes * 2 + 16 * 1024**2
    resources = [
        CapacityResource.for_path(settings.backup_dir, peak, role="backup build"),
        CapacityResource.for_path(destination, peak, role="backup publication"),
    ]
    backend = get_backend()
    if backend.direct_path(backend.thumbnail_key(0)) is None:
        # Snapshot streaming materializes remote originals through local_path;
        # that adapter uses the process temporary directory, not backup_dir.
        resources.append(
            CapacityResource.for_path(
                Path(tempfile.gettempdir()), size_bytes, role="backup remote source"
            )
        )
    return resources


def backup_restore(size_bytes: int) -> list[CapacityResource]:
    return [
        CapacityResource.for_path(
            settings.backup_dir, size_bytes * 2, role="restore extraction and rollback"
        ),
        vault_allocation(size_bytes, role="restore publication"),
    ]
