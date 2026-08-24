from __future__ import annotations

import zipfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator

import pytest

from app.db.models import FileType
from app.services.printer_jobs import reproducibility_payload
from app.services.three_mf_preview import (
    EmbeddedGcodeError,
    extract_embedded_gcode,
    read_embedded_gcode,
)


def _archive(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return path


def test_requested_plate_is_selected_strictly(tmp_path: Path) -> None:
    path = _archive(
        tmp_path / "project.3mf",
        {
            "Metadata/plate_1.gcode": b"; plate 1\n",
            "Metadata/plate_2.gcode": b"; plate 2\n",
        },
    )

    result = extract_embedded_gcode(path, plate_index=2)

    assert result.filename == "plate_2.gcode"
    assert result.content == b"; plate 2\n"


def test_single_candidate_is_fallback_without_plate_index(tmp_path: Path) -> None:
    path = _archive(tmp_path / "project.3mf", {"Metadata/plate_7.gcode": b"G28\n"})

    result = extract_embedded_gcode(path)

    assert result.filename == "plate_7.gcode"
    assert result.content == b"G28\n"


@pytest.mark.parametrize(
    ("entries", "code"),
    [
        ({"Metadata/plate_1.gcode": b"G28\n"}, "embedded_gcode_not_found"),
        (
            {
                "Metadata/plate_1.gcode": b"one\n",
                "Metadata/plate_2.gcode": b"two\n",
            },
            "embedded_gcode_ambiguous",
        ),
    ],
)
def test_missing_or_ambiguous_candidates_are_stable(
    tmp_path: Path, entries: dict[str, bytes], code: str
) -> None:
    path = _archive(tmp_path / "project.3mf", entries)

    with pytest.raises(EmbeddedGcodeError) as failure:
        extract_embedded_gcode(path, plate_index=9 if code.endswith("found") else None)

    assert failure.value.code == code


def test_traversal_and_case_variants_are_ignored(tmp_path: Path) -> None:
    path = _archive(
        tmp_path / "project.3mf",
        {
            "../Metadata/plate_1.gcode": b"unsafe\n",
            "metadata/plate_1.gcode": b"wrong case\n",
            "Metadata/plate_1.gcode": b"safe\n",
        },
    )

    result = extract_embedded_gcode(path, plate_index=1)

    assert result.content == b"safe\n"


def test_malformed_zip_has_stable_code(tmp_path: Path) -> None:
    path = tmp_path / "broken.3mf"
    path.write_bytes(b"not a zip")

    with pytest.raises(EmbeddedGcodeError) as failure:
        extract_embedded_gcode(path)

    assert failure.value.code == "embedded_gcode_malformed"


def test_uncompressed_cap_is_enforced_before_and_during_read(tmp_path: Path) -> None:
    path = _archive(tmp_path / "project.3mf", {"Metadata/plate_1.gcode": b"12345"})

    with pytest.raises(EmbeddedGcodeError) as failure:
        extract_embedded_gcode(path, max_uncompressed_bytes=4)

    assert failure.value.code == "embedded_gcode_too_large"


def test_compression_ratio_is_rejected_as_bomb(tmp_path: Path) -> None:
    path = _archive(
        tmp_path / "project.3mf",
        {"Metadata/plate_1.gcode": b"G28\n" * 100},
    )

    with pytest.raises(EmbeddedGcodeError) as failure:
        extract_embedded_gcode(path, max_compression_ratio=2)

    assert failure.value.code == "embedded_gcode_bomb"


def test_storage_reads_through_local_path_without_persisting_copy(tmp_path: Path) -> None:
    archive_path = _archive(
        tmp_path / "project.3mf", {"Metadata/plate_1.gcode": b"G28\n"}
    )
    calls: list[str] = []

    class Backend:
        @contextmanager
        def local_path(self, key: str) -> Iterator[Path]:
            calls.append(key)
            yield archive_path

    result = read_embedded_gcode(Backend(), "opaque-3mf-key", plate_index=1)  # type: ignore[arg-type]

    assert result.content == b"G28\n"
    assert calls == ["opaque-3mf-key"]
    assert list(tmp_path.glob("*.gcode")) == []


@pytest.mark.parametrize("plate_index", [None, 3])
def test_reproducibility_contract_links_only_archived_3mf(
    plate_index: int | None,
) -> None:
    job = SimpleNamespace(
        artifact_evidence="project_archived",
        source="external",
        file_id=42,
        external_display_name="Benchy",
        external_task_id="task-1",
        external_subtask_id=None,
        external_project_id="project-1",
        external_profile_id=None,
        external_gcode_file="benchy.gcode",
        external_plate_index=plate_index,
        external_current_layer=None,
        external_total_layers=None,
        external_nozzle_diameter=None,
        artifact_capture_error=None,
        artifact_capture_error_code=None,
        artifact_capture_error_message=None,
    )

    payload = reproducibility_payload(
        job,
        file_type=FileType.THREE_MF,
        download_url="/api/v1/files/42/download",
    )

    suffix = f"?plate_index={plate_index}" if plate_index is not None else ""
    expected = f"/api/v1/files/42/embedded-gcode{suffix}"
    assert payload["toolpath_preview_url"] == expected
    assert payload["reproducibility"]["toolpath_preview_url"] == expected
