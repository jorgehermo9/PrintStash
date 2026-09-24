"""Which derivatives exist, which Artifacts they apply to, and their recipes.

A derivative is a pure function of one Artifact's bytes plus a recipe. Kinds
are grouped by the producer that computes them together: one mesh load yields
geometry and a rendered thumbnail, one G-code header read yields slicer
metadata and the embedded thumbnail. Each group is one job definition; each
kind in it has its own recipe version.

**Bumping a recipe.** A recipe version is the code's statement that the output
of a kind would now differ for the same bytes. Increase it, by hand, in the
same change that alters what the producer emits for a kind (a new renderer, a
parser that reads a field it used to miss, a different encoding). Nothing else
is needed: the derivative source finds every Artifact without a row at the new
version with an anti-join and re-derives it at backfill priority, and each old
output stays visible until its replacement is ready. Do not bump for a refactor
that cannot change any output. See ``docs/derivatives.md``.

An Artifact belongs to exactly one group (it is a mesh or it is G-code), so a
kind's recipe version is read relative to the Artifact's group.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, func, or_
from sqlmodel import col

from app.db.models import SENTINEL_FILE_HASH, File, FileType

METADATA = "metadata"
THUMBNAIL = "thumbnail"
TOOLPATH = "toolpath"

MESH_DEFINITION = "derivatives.mesh"
GCODE_DEFINITION = "derivatives.gcode"
TOOLPATH_DEFINITION = "derivatives.toolpath"

# Recipe versions. Bump rule: see the module docstring.
MESH_GEOMETRY_RECIPE = 1
MESH_THUMBNAIL_RECIPE = 1
GCODE_METADATA_RECIPE = 1
GCODE_THUMBNAIL_RECIPE = 1
TOOLPATH_RECIPE = 1

MESH_TYPES = (FileType.STL, FileType.THREE_MF, FileType.OBJ, FileType.STEP)
BINARY_GCODE_SUFFIXES = (".bgcode", ".bgc")


@dataclass(frozen=True)
class DerivativeGroup:
    """One producer: a job definition and the kinds it derives together."""

    definition: str
    kinds: dict[str, int]
    label: str

    def applies(self) -> Any:
        """SQL predicate over ``files``: Artifacts this group derives for."""
        real = col(File.sha256) != SENTINEL_FILE_HASH
        if self.definition == MESH_DEFINITION:
            return and_(real, col(File.file_type).in_(MESH_TYPES))
        if self.definition == GCODE_DEFINITION:
            return and_(real, col(File.file_type) == FileType.GCODE)
        lowered = func.lower(File.original_filename)
        return and_(
            real,
            col(File.file_type) == FileType.GCODE,
            or_(*(lowered.like(f"%{suffix}") for suffix in BINARY_GCODE_SUFFIXES)),
        )

    def applies_to(self, file: File) -> bool:
        if file.sha256 == SENTINEL_FILE_HASH:
            return False
        if self.definition == MESH_DEFINITION:
            return file.file_type in MESH_TYPES
        if self.definition == GCODE_DEFINITION:
            return file.file_type == FileType.GCODE
        return (
            file.file_type == FileType.GCODE
            and file.original_filename.lower().endswith(BINARY_GCODE_SUFFIXES)
        )


GROUPS: tuple[DerivativeGroup, ...] = (
    DerivativeGroup(
        MESH_DEFINITION,
        {METADATA: MESH_GEOMETRY_RECIPE, THUMBNAIL: MESH_THUMBNAIL_RECIPE},
        "Mesh geometry and thumbnails",
    ),
    DerivativeGroup(
        GCODE_DEFINITION,
        {METADATA: GCODE_METADATA_RECIPE, THUMBNAIL: GCODE_THUMBNAIL_RECIPE},
        "G-code metadata and thumbnails",
    ),
    DerivativeGroup(
        TOOLPATH_DEFINITION, {TOOLPATH: TOOLPATH_RECIPE}, "Toolpath previews"
    ),
)
KINDS = (METADATA, THUMBNAIL, TOOLPATH)


def group(definition: str) -> DerivativeGroup:
    for candidate in GROUPS:
        if candidate.definition == definition:
            return candidate
    raise LookupError(f"unknown_derivative_group:{definition}")


def groups_for(file: File) -> list[DerivativeGroup]:
    return [candidate for candidate in GROUPS if candidate.applies_to(file)]


def recipes_for(file: File) -> dict[str, int]:
    """Every kind that applies to ``file``, at its current recipe version."""
    recipes: dict[str, int] = {}
    for candidate in groups_for(file):
        recipes.update(candidate.kinds)
    return recipes


def definition_for_kind(kind: str) -> str:
    """The first group deriving ``kind`` (used to nudge after a regenerate)."""
    if kind not in KINDS:
        raise LookupError(f"unknown_derivative_kind:{kind}")
    for candidate in GROUPS:
        if kind in candidate.kinds:
            return candidate.definition
    raise LookupError(f"unknown_derivative_kind:{kind}")


def definitions_for_kind(kind: str) -> list[str]:
    return [candidate.definition for candidate in GROUPS if kind in candidate.kinds]


def kinds_for_definition(definition: str) -> list[str]:
    for candidate in GROUPS:
        if candidate.definition == definition:
            return list(candidate.kinds)
    return []


def all_definitions() -> Iterable[str]:
    return (candidate.definition for candidate in GROUPS)
