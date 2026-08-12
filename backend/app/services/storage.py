"""Pure filesystem-layout helpers for the vault.

Slug generation and upload staging only — anything touching stored blobs
goes through ``get_backend()`` from ``app.services.storage_backend``
(``blob_key``, ``thumbnail_key``, ``move_in``, ``local_path``...).
"""

from __future__ import annotations

import os
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import BinaryIO

_SLUG_RE = re.compile(r"[^a-z0-9]+")


class UploadTooLarge(Exception):
    pass


def slugify(name: str) -> str:
    """Produce a filesystem-safe, kebab-case slug."""
    normalized = (
        unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    )
    slug = _SLUG_RE.sub("-", normalized.lower()).strip("-")
    return slug or "model"


def ensure_unique_slug(base: str, exists: callable) -> str:
    """Append -2, -3, ... until exists(slug) returns False."""
    candidate = base
    n = 2
    while exists(candidate):
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def stream_to_path(
    src: BinaryIO, dest: Path, *, max_bytes: int | None = None
) -> int:
    """Stream a binary source to a local path, returning bytes written."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    bytes_written = 0
    fd, temp_name = tempfile.mkstemp(prefix=".printstash-stage-", dir=dest.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if max_bytes is not None and bytes_written > max_bytes:
                    raise UploadTooLarge
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        # Publish only a complete staging object and never replace a collision.
        os.link(temp, dest, follow_symlinks=False)
        return bytes_written
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            # An uncertain private temp is safer than turning a successfully
            # published staging file into a reported failure.
            pass
