"""Which derivative kinds apply to an Artifact, and which producer makes each.

An Artifact belongs to one group by its type: a mesh gets geometry and a
rendered thumbnail, G-code gets slicer metadata and its embedded thumbnail,
and binary G-code additionally gets a converted toolpath. An external-library
sentinel row has no bytes of its own and gets nothing.
"""

from __future__ import annotations

import pytest

from app.db.models import SENTINEL_FILE_HASH, File, FileType
from app.modules.derivatives import kinds
from app.modules.derivatives.kinds import (
    GCODE_DEFINITION,
    MESH_DEFINITION,
    METADATA,
    THUMBNAIL,
    TOOLPATH,
    TOOLPATH_DEFINITION,
)
from tests.factories.library import detached_file


def _file(file_type: FileType, name: str = "part", sha: str = "a" * 64) -> File:
    return detached_file(original_filename=name, file_type=file_type, sha256=sha)


class TestRecipesFor:
    @pytest.mark.parametrize(
        "file_type", [FileType.STL, FileType.THREE_MF, FileType.OBJ, FileType.STEP]
    )
    def test_a_mesh_gets_every_mesh_kind(self, file_type: FileType) -> None:
        assert set(kinds.recipes_for(_file(file_type))) == {METADATA, THUMBNAIL}

    def test_plain_gcode_gets_what_its_header_holds(self) -> None:
        assert set(kinds.recipes_for(_file(FileType.GCODE, "plate.gcode"))) == {
            METADATA,
            THUMBNAIL,
        }

    @pytest.mark.parametrize("name", ["plate.bgcode", "PLATE.BGC"])
    def test_binary_gcode_also_gets_a_toolpath(self, name: str) -> None:
        assert set(kinds.recipes_for(_file(FileType.GCODE, name))) == {
            METADATA,
            THUMBNAIL,
            TOOLPATH,
        }

    def test_a_sentinel_row_gets_nothing(self) -> None:
        assert kinds.recipes_for(_file(FileType.STL, sha=SENTINEL_FILE_HASH)) == {}


class TestGroups:
    def test_names_the_producer_of_each_kind(self) -> None:
        assert kinds.definition_for_kind(TOOLPATH) == TOOLPATH_DEFINITION
        assert set(kinds.definitions_for_kind(THUMBNAIL)) == {
            MESH_DEFINITION,
            GCODE_DEFINITION,
        }

    def test_an_unknown_kind_is_refused(self) -> None:
        with pytest.raises(LookupError):
            kinds.definition_for_kind("hologram")

    def test_lists_a_definitions_kinds(self) -> None:
        assert kinds.kinds_for_definition(MESH_DEFINITION) == [METADATA, THUMBNAIL]
        assert kinds.kinds_for_definition("not.a.group") == []

    def test_an_unknown_group_is_refused(self) -> None:
        with pytest.raises(LookupError):
            kinds.group("not.a.group")

    def test_every_group_is_a_definition(self) -> None:
        assert set(kinds.all_definitions()) == {
            MESH_DEFINITION,
            GCODE_DEFINITION,
            TOOLPATH_DEFINITION,
        }
