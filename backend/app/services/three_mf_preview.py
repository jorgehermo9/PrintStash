"""Read embedded G-code from a Bambu 3MF project without extracting it.

The service deliberately works on a temporary/local path supplied by the
storage seam. It never publishes an extracted copy and never trusts an archive
member's path as a filesystem destination.
"""

from __future__ import annotations

import re
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.core.config import settings
from app.services.storage_backend import StorageBackend

_PLATE_PATH = re.compile(r"^Metadata/plate_[0-9]+\.gcode$")


class EmbeddedGcodeError(ValueError):
    """Stable, safe-to-expose failure from an embedded toolpath lookup."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class EmbeddedGcode:
    """One bounded G-code member read from a 3MF archive."""

    filename: str
    content: bytes


def _safe_member_name(name: str) -> bool:
    """Accept only canonical relative POSIX names; unsafe entries are ignored."""

    if (
        not name
        or "\x00" in name
        or "\\" in name
        or name.startswith("/")
        or unicodedata.normalize("NFC", name) != name
    ):
        return False
    path = PurePosixPath(name)
    return (
        not path.is_absolute()
        and str(path) == name
        and all(part not in {"", ".", ".."} for part in path.parts)
    )


def _select_member(
    infos: list[zipfile.ZipInfo], plate_index: int | None
) -> zipfile.ZipInfo:
    safe_infos = [
        info
        for info in infos
        if not info.is_dir()
        and _safe_member_name(info.filename)
        and _PLATE_PATH.fullmatch(info.filename)
    ]
    if plate_index is not None:
        wanted = f"Metadata/plate_{plate_index}.gcode"
        matches = [info for info in safe_infos if info.filename == wanted]
    else:
        matches = safe_infos
    if not matches:
        raise EmbeddedGcodeError("embedded_gcode_not_found")
    if len(matches) != 1:
        raise EmbeddedGcodeError("embedded_gcode_ambiguous")
    return matches[0]


def extract_embedded_gcode(
    archive_path: Path,
    *,
    plate_index: int | None = None,
    max_uncompressed_bytes: int | None = None,
    max_compression_ratio: float | None = None,
) -> EmbeddedGcode:
    """Read one embedded toolpath from *archive_path* with bounded memory use.

    A requested plate must have the exact ``Metadata/plate_<N>.gcode`` member.
    Without a plate index, exactly one canonical plate member is required.
    ``read(cap + 1)`` detects a stream that exceeds the configured cap without
    ever materialising an unbounded archive member.
    """

    if plate_index is not None and plate_index < 0:
        raise EmbeddedGcodeError("embedded_gcode_not_found")
    cap = (
        max_uncompressed_bytes
        if max_uncompressed_bytes is not None
        else settings.three_mf_preview_max_uncompressed_mb * 1024 * 1024
    )
    ratio_limit = (
        max_compression_ratio
        if max_compression_ratio is not None
        else settings.three_mf_preview_max_ratio
    )
    if cap <= 0 or ratio_limit <= 0:
        raise ValueError("embedded_gcode_limits_invalid")

    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            member = _select_member(archive.infolist(), plate_index)
            if member.file_size > cap:
                raise EmbeddedGcodeError("embedded_gcode_too_large")
            if member.file_size and (
                member.compress_size == 0
                or member.file_size / member.compress_size > ratio_limit
            ):
                raise EmbeddedGcodeError("embedded_gcode_bomb")
            try:
                with archive.open(member, "r") as source:
                    content = source.read(cap + 1)
            except (EOFError, OSError, RuntimeError, zipfile.BadZipFile) as exc:
                raise EmbeddedGcodeError("embedded_gcode_malformed") from exc
            if len(content) > cap:
                raise EmbeddedGcodeError("embedded_gcode_too_large")
            return EmbeddedGcode(
                filename=member.filename.rsplit("/", 1)[-1], content=content
            )
    except EmbeddedGcodeError:
        raise
    except FileNotFoundError:
        # Preserve the storage seam's missing-blob signal for the API's 410.
        raise
    except (EOFError, OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise EmbeddedGcodeError("embedded_gcode_malformed") from exc


def read_embedded_gcode(
    backend: StorageBackend,
    storage_key: str,
    *,
    plate_index: int | None = None,
    max_uncompressed_bytes: int | None = None,
    max_compression_ratio: float | None = None,
) -> EmbeddedGcode:
    """Read a 3MF through ``StorageBackend.local_path`` for local or S3 data."""

    with backend.local_path(storage_key) as archive_path:
        return extract_embedded_gcode(
            archive_path,
            plate_index=plate_index,
            max_uncompressed_bytes=max_uncompressed_bytes,
            max_compression_ratio=max_compression_ratio,
        )
