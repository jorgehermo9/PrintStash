"""Canonical path-boundary validation for destructive storage roles."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

from sqlalchemy.engine.url import make_url


class StoragePathOverlapError(ValueError):
    def __init__(self, first: str, second: str) -> None:
        super().__init__(f"storage paths overlap: {first}, {second}")
        self.first = first
        self.second = second


def canonical_path(value: str | Path) -> Path:
    """Resolve aliases and symlinks even when the leaf does not exist yet."""
    return Path(value).expanduser().resolve(strict=False)


def sqlite_database_path(db_url: str) -> Path | None:
    url = make_url(db_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return None
    return canonical_path(unquote(url.database))


def paths_overlap(first: Path, second: Path) -> bool:
    return first == second or first.is_relative_to(second) or second.is_relative_to(first)


def validate_disjoint_directories(paths: dict[str, str | Path]) -> dict[str, Path]:
    """Return canonical roots, rejecting equality, nesting, and symlink aliases."""
    resolved = {label: canonical_path(path) for label, path in paths.items()}
    items = list(resolved.items())
    for index, (first_label, first) in enumerate(items):
        for second_label, second in items[index + 1 :]:
            if paths_overlap(first, second):
                raise StoragePathOverlapError(first_label, second_label)
    return resolved


def validate_path_outside_roots(
    candidate: str | Path, roots: dict[str, str | Path]
) -> Path:
    resolved = canonical_path(candidate)
    for label, root in roots.items():
        if paths_overlap(resolved, canonical_path(root)):
            raise StoragePathOverlapError("candidate", label)
    return resolved


def unlink_managed_file(path: str | Path, root: str | Path) -> bool:
    """Unlink only a leaf proven to remain beneath its managed scratch root."""
    candidate = Path(path).expanduser().absolute()
    boundary = canonical_path(root)
    resolved_parent = candidate.parent.resolve(strict=False)
    if resolved_parent != boundary and not resolved_parent.is_relative_to(boundary):
        raise StoragePathOverlapError("managed_file", "outside_root")
    try:
        if candidate.is_symlink():
            # Following a leaf symlink here would unlink the target rather than
            # the operation's scratch entry. Preserve both for investigation.
            raise StoragePathOverlapError("managed_file", "symlink")
        candidate.unlink()
    except FileNotFoundError:
        return False
    return True


def validate_runtime_storage_paths() -> dict[str, Path]:
    """Validate all configured local roles before any worker can mutate them."""
    from app.core.config import settings

    paths: dict[str, str | Path] = {
        "data_dir": settings.data_dir,
        "thumb_dir": settings.thumb_dir,
        "staging_dir": settings.staging_dir,
        "backup_dir": settings.backup_dir,
    }
    resolved = validate_disjoint_directories(paths)
    database_path = sqlite_database_path(str(settings.db_url))
    if database_path is not None:
        validate_file_outside_roots(database_path, resolved)
    validate_file_outside_roots(settings.secrets_key_file, resolved)
    return resolved


def validate_file_outside_roots(
    candidate: str | Path, roots: dict[str, str | Path]
) -> Path:
    """Reject a managed file located inside any independently mutable root."""
    resolved = canonical_path(candidate)
    for label, root in roots.items():
        boundary = canonical_path(root)
        if resolved == boundary or resolved.is_relative_to(boundary):
            raise StoragePathOverlapError("managed_file", label)
    return resolved
