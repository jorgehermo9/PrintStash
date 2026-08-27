from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from printstash_core.files import (
    ArchiveLimits,
    ArchivePolicyError,
    extract_selected,
    inspect_archive,
    safe_entry_name,
    safe_subdir,
)

FILE_TYPES = {".stl": "stl", ".3mf": "3mf", ".gcode": "gcode"}
IMAGES = {".png", ".jpg"}


def _limits(**overrides: int) -> ArchiveLimits:
    values = {
        "max_entries": 100,
        "max_entry_bytes": 1024,
        "max_total_bytes": 4096,
        "max_central_directory_bytes": 4096,
        "max_path_bytes": 256,
        "max_depth": 32,
    }
    values.update(overrides)
    return ArchiveLimits(**values)


def _archive(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


def test_inspection_returns_only_supported_files_and_images(tmp_path: Path) -> None:
    path = _archive(
        tmp_path / "bundle.zip",
        {"parts/a.stl": b"a", "preview.png": b"p", "notes.txt": b"n"},
    )

    entries = inspect_archive(
        path, limits=_limits(), file_types=FILE_TYPES, image_suffixes=IMAGES
    )

    assert [(entry.name, entry.file_type, entry.is_image) for entry in entries] == [
        ("parts/a.stl", "stl", False),
        ("preview.png", None, True),
    ]
    assert all(entry.entry_id.count(":") == 2 for entry in entries)


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("../escape.stl", "archive_unsafe_entry"),
        ("/absolute.stl", "archive_unsafe_entry"),
        ("C:\\escape.stl", "archive_unsafe_entry"),
    ],
)
def test_inspection_rejects_unsafe_paths(tmp_path: Path, name: str, code: str) -> None:
    path = _archive(tmp_path / "unsafe.zip", {name: b"x"})

    with pytest.raises(ArchivePolicyError, match=code):
        inspect_archive(
            path, limits=_limits(), file_types=FILE_TYPES, image_suffixes=IMAGES
        )


def test_inspection_rejects_unicode_normalized_duplicates(tmp_path: Path) -> None:
    path = tmp_path / "duplicates.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Caf\N{LATIN SMALL LETTER E WITH ACUTE}.stl", b"one")
        archive.writestr("Cafe\N{COMBINING ACUTE ACCENT}.STL", b"two")

    with pytest.raises(ArchivePolicyError, match="archive_duplicate_entry"):
        inspect_archive(
            path, limits=_limits(), file_types=FILE_TYPES, image_suffixes=IMAGES
        )


def test_inspection_enforces_entry_and_total_limits(tmp_path: Path) -> None:
    path = _archive(tmp_path / "large.zip", {"a.stl": b"12", "b.stl": b"34"})

    with pytest.raises(ArchivePolicyError, match="archive_too_many_entries"):
        inspect_archive(
            path,
            limits=_limits(max_entries=1),
            file_types=FILE_TYPES,
            image_suffixes=IMAGES,
        )
    with pytest.raises(ArchivePolicyError, match="archive_too_large"):
        inspect_archive(
            path,
            limits=_limits(max_total_bytes=3),
            file_types=FILE_TYPES,
            image_suffixes=IMAGES,
        )


def test_extract_selected_stages_only_supported_entries(tmp_path: Path) -> None:
    path = _archive(tmp_path / "bundle.zip", {"a.stl": b"solid", "notes.txt": b"n"})
    staging = tmp_path / "staging"

    extracted = extract_selected(
        path,
        ["a.stl", "notes.txt"],
        staging_dir=staging,
        max_entry_bytes=1024,
        importable_suffixes=set(FILE_TYPES),
        name_factory=lambda suffix: f"fixed{suffix}",
    )

    assert extracted == [(staging / "fixed.stl", "a.stl")]
    assert extracted[0][0].read_bytes() == b"solid"
    assert safe_subdir("nested\\part.stl") == "nested"


def test_invalid_zip_has_a_stable_error_code(tmp_path: Path) -> None:
    path = tmp_path / "invalid.zip"
    path.write_bytes(b"not a zip")

    with pytest.raises(ArchivePolicyError, match="archive_invalid"):
        inspect_archive(
            path, limits=_limits(), file_types=FILE_TYPES, image_suffixes=IMAGES
        )


class TestSafeEntryName:
    """The path check that stands between an upload and the rest of the disk."""

    @pytest.mark.parametrize(
        "name",
        [
            pytest.param("", id="empty"),
            pytest.param("dir/", id="trailing-slash"),
            pytest.param("dir\\", id="trailing-backslash"),
            pytest.param("/etc/passwd", id="absolute-posix"),
            pytest.param("\\windows\\system32", id="absolute-windows"),
            pytest.param("C:/secrets.txt", id="drive-letter"),
            pytest.param("../escape.stl", id="traversal"),
            pytest.param("a/../../escape.stl", id="traversal-mid-path"),
            pytest.param("..\\escape.stl", id="traversal-backslash"),
        ],
    )
    def test_refuses_a_name_that_could_escape_the_staging_directory(
        self, name: str
    ) -> None:
        # Every one of these, joined to a staging path, resolves somewhere the
        # archive has no business writing.
        assert safe_entry_name(name) is False

    @pytest.mark.parametrize(
        "name",
        [
            pytest.param("part.stl", id="root"),
            pytest.param("nested/part.stl", id="nested"),
            pytest.param("a/b/c/part.stl", id="deep"),
            pytest.param("nested\\part.stl", id="windows-separator"),
            pytest.param("with..dots.stl", id="dots-in-a-filename"),
        ],
    )
    def test_accepts_an_ordinary_relative_entry(self, name: str) -> None:
        # `with..dots.stl` is the interesting one: a substring `..` is not a
        # traversal, and rejecting it would break real slicer output.
        assert safe_entry_name(name) is True


class TestSafeSubdir:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            pytest.param("part.stl", "", id="root-is-empty"),
            pytest.param("nested/part.stl", "nested", id="one-level"),
            pytest.param("a/b/part.stl", "a/b", id="two-levels"),
            pytest.param("nested\\part.stl", "nested", id="windows-separator"),
        ],
    )
    def test_returns_the_posix_directory_part(self, name: str, expected: str) -> None:
        # The root case returns `""` rather than `"."`, because the caller joins
        # this onto a collection path and `"."` would become a literal directory.
        assert safe_subdir(name) == expected


class TestInspectArchiveLimits:
    def test_refuses_an_oversized_central_directory(self, tmp_path: Path) -> None:
        archive = _archive(
            tmp_path / "many.zip", {f"f{i}.stl": b"x" for i in range(40)}
        )

        # The central directory is read before any entry, so a bomb hidden there
        # has to be refused on its declared size alone.
        with pytest.raises(ArchivePolicyError, match="archive_too_large"):
            inspect_archive(
                archive,
                limits=_limits(max_central_directory_bytes=10),
                file_types=FILE_TYPES,
                image_suffixes=IMAGES,
            )

    def test_refuses_a_path_longer_than_the_byte_budget(self, tmp_path: Path) -> None:
        archive = _archive(tmp_path / "long.zip", {("a" * 300) + ".stl": b"x"})

        # Bytes, not characters: a multi-byte name can be far longer on disk than
        # it looks, which is how a path limit gets bypassed.
        with pytest.raises(ArchivePolicyError, match="archive_path_too_deep"):
            inspect_archive(
                archive,
                limits=_limits(max_path_bytes=64),
                file_types=FILE_TYPES,
                image_suffixes=IMAGES,
            )

    def test_refuses_a_path_nested_deeper_than_the_limit(self, tmp_path: Path) -> None:
        archive = _archive(tmp_path / "deep.zip", {"a/b/c/d/e/part.stl": b"x"})

        with pytest.raises(ArchivePolicyError, match="archive_path_too_deep"):
            inspect_archive(
                archive,
                limits=_limits(max_depth=2),
                file_types=FILE_TYPES,
                image_suffixes=IMAGES,
            )

    def test_skips_directory_entries_without_counting_them(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "dirs.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("nested/", b"")
            archive.writestr("nested/part.stl", b"x")

        entries = inspect_archive(
            path,
            limits=_limits(),
            file_types=FILE_TYPES,
            image_suffixes=IMAGES,
        )

        # A directory entry carries no bytes and must not consume the entry
        # budget, or a deeply-foldered archive fails for the wrong reason.
        assert [entry.name for entry in entries] == ["nested/part.stl"]

    def test_refuses_a_single_entry_over_the_entry_budget(self, tmp_path: Path) -> None:
        archive = _archive(tmp_path / "big.zip", {"part.stl": b"x" * 200})

        with pytest.raises(ArchivePolicyError, match="archive_entry_too_large"):
            inspect_archive(
                archive,
                limits=_limits(max_entry_bytes=100),
                file_types=FILE_TYPES,
                image_suffixes=IMAGES,
            )


class TestExtractSelectedFailures:
    def test_removes_everything_it_staged_when_one_entry_is_refused(
        self, tmp_path: Path
    ) -> None:
        archive = _archive(
            tmp_path / "mixed.zip",
            {"good.stl": b"x", "huge.stl": b"x" * 500},
        )
        staging = tmp_path / "staging"
        staging.mkdir()

        with pytest.raises(ArchivePolicyError, match="archive_entry_too_large"):
            extract_selected(
                archive,
                ["good.stl", "huge.stl"],
                staging_dir=staging,
                max_entry_bytes=100,
                importable_suffixes={".stl"},
            )

        # All-or-nothing: a half-extracted archive leaves staged bytes that no
        # row owns, and nothing will ever clean them up.
        assert list(staging.iterdir()) == []

    def test_refuses_an_unsafe_entry_that_was_explicitly_selected(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "evil.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("../escape.stl", b"x")
        staging = tmp_path / "staging"
        staging.mkdir()

        # `inspect_archive` would have refused this archive already; the check is
        # repeated here because the name list arrives from the client, and a
        # caller could name an entry the inspection never returned.
        with pytest.raises(ArchivePolicyError, match="archive_unsafe_entry"):
            extract_selected(
                path,
                ["../escape.stl"],
                staging_dir=staging,
                max_entry_bytes=1024,
                importable_suffixes={".stl"},
            )

        assert list(staging.iterdir()) == []

    def test_skips_a_selected_entry_whose_type_is_not_importable(
        self, tmp_path: Path
    ) -> None:
        archive = _archive(
            tmp_path / "readme.zip", {"part.stl": b"x", "notes.txt": b"hello"}
        )
        staging = tmp_path / "staging"
        staging.mkdir()

        extracted = extract_selected(
            archive,
            ["part.stl", "notes.txt"],
            staging_dir=staging,
            max_entry_bytes=1024,
            importable_suffixes={".stl"},
        )

        # Selecting a README is not an error — it is simply not imported.
        assert [name for _staged, name in extracted] == ["part.stl"]

    def test_names_each_staged_file_uniquely_by_default(self, tmp_path: Path) -> None:
        archive = _archive(
            tmp_path / "two.zip", {"a/part.stl": b"x", "b/part.stl": b"y"}
        )
        staging = tmp_path / "staging"
        staging.mkdir()

        extracted = extract_selected(
            archive,
            ["a/part.stl", "b/part.stl"],
            staging_dir=staging,
            max_entry_bytes=1024,
            importable_suffixes={".stl"},
        )

        # Two entries can share a basename in different folders; staging them
        # under that name would have the second overwrite the first.
        staged = [path.name for path, _name in extracted]
        assert len(set(staged)) == 2
        assert all(name.endswith(".stl") for name in staged)
