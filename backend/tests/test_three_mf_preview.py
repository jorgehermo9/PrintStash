from __future__ import annotations

import zipfile
import zlib
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
from typing import Iterator

import pytest

from app.core.config import settings
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


def test_storage_reads_through_direct_path_without_persisting_copy(
    tmp_path: Path,
) -> None:
    archive_path = _archive(
        tmp_path / "project.3mf", {"Metadata/plate_1.gcode": b"G28\n"}
    )
    calls: list[str] = []

    class Backend:
        def stat_size(self, key: str) -> int:
            calls.append(key)
            return archive_path.stat().st_size

        def direct_path(self, key: str) -> Path:
            calls.append(key)
            return archive_path

    result = read_embedded_gcode(
        Backend(),
        "opaque-3mf-key",
        plate_index=1,  # type: ignore[arg-type]
    )

    assert result.content == b"G28\n"
    assert calls == ["opaque-3mf-key", "opaque-3mf-key"]
    assert list(tmp_path.glob("*.gcode")) == []


@pytest.mark.parametrize("plate_index", [None, 3, -2])
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

    suffix = (
        f"?plate_index={plate_index}"
        if plate_index is not None and plate_index >= 0
        else ""
    )
    expected = f"/api/v1/files/42/embedded-gcode{suffix}"
    assert payload["toolpath_preview_url"] == expected
    assert payload["reproducibility"]["toolpath_preview_url"] == expected


def test_remote_archive_size_is_checked_before_transfer() -> None:
    calls = 0

    class RemoteBackend:
        def stat_size(self, _key: str) -> int:
            return 11

        def direct_path(self, _key: str) -> None:
            return None

        def stream_chunks(self, _key: str, chunk_size: int) -> Iterator[bytes]:
            nonlocal calls
            calls += 1
            yield b"never downloaded"

    with pytest.raises(EmbeddedGcodeError) as failure:
        read_embedded_gcode(
            RemoteBackend(),
            "remote.3mf",
            max_archive_bytes=10,  # type: ignore[arg-type]
        )

    assert failure.value.code == "embedded_gcode_archive_too_large"
    assert calls == 0


def test_remote_archive_transfer_stops_at_cap() -> None:
    calls = 0

    class RemoteBackend:
        def stat_size(self, _key: str) -> int:
            return 1

        def direct_path(self, _key: str) -> None:
            return None

        def stream_chunks(self, _key: str, chunk_size: int) -> Iterator[bytes]:
            nonlocal calls
            calls += 1
            yield b"x" * 11
            calls += 1
            yield b"must not be requested"

    with pytest.raises(EmbeddedGcodeError) as failure:
        read_embedded_gcode(
            RemoteBackend(),
            "remote.3mf",
            max_archive_bytes=10,  # type: ignore[arg-type]
        )

    assert failure.value.code == "embedded_gcode_archive_too_large"
    assert calls == 1


def test_many_zip_entries_are_rejected_before_selection(tmp_path: Path) -> None:
    entries = {f"Metadata/extra_{index}.txt": b"" for index in range(5)}
    entries["Metadata/plate_1.gcode"] = b"G28\n"
    path = _archive(tmp_path / "many.3mf", entries)

    with pytest.raises(EmbeddedGcodeError) as failure:
        extract_embedded_gcode(path, max_entries=5)

    assert failure.value.code == "embedded_gcode_too_many_entries"


def test_central_directory_size_is_bounded_before_selection(tmp_path: Path) -> None:
    path = _archive(tmp_path / "central.3mf", {"Metadata/plate_1.gcode": b"G28\n"})

    with pytest.raises(EmbeddedGcodeError) as failure:
        extract_embedded_gcode(path, max_central_directory_bytes=1)

    assert failure.value.code == "embedded_gcode_central_directory_too_large"


@pytest.mark.parametrize(
    "fault",
    [EOFError("truncated"), NotImplementedError("compression"), zlib.error("crc")],
)
def test_zip_read_faults_have_stable_malformed_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: Exception
) -> None:
    path = _archive(tmp_path / "fault.3mf", {"Metadata/plate_1.gcode": b"G28\n"})

    def fail_open(_archive: zipfile.ZipFile, *_args, **_kwargs):
        raise fault

    monkeypatch.setattr(zipfile.ZipFile, "open", fail_open)

    with pytest.raises(EmbeddedGcodeError) as failure:
        extract_embedded_gcode(path)

    assert failure.value.code == "embedded_gcode_malformed"


def test_preview_capacity_fails_fast_before_second_inflate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _archive(tmp_path / "busy.3mf", {"Metadata/plate_1.gcode": b"G28\n"})
    monkeypatch.setattr(settings._frozen, "three_mf_preview_max_concurrent", 1)
    entered = Event()
    release = Event()
    original = extract_embedded_gcode

    def blocking_extract(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return original(*args, **kwargs)

    monkeypatch.setattr(
        "app.services.three_mf_preview.extract_embedded_gcode", blocking_extract
    )

    class LocalBackend:
        def stat_size(self, _key: str) -> int:
            return path.stat().st_size

        def direct_path(self, _key: str) -> Path:
            return path

    worker = Thread(
        target=read_embedded_gcode,
        args=(LocalBackend(), "busy.3mf"),  # type: ignore[arg-type]
    )
    worker.start()
    assert entered.wait(2)
    with pytest.raises(EmbeddedGcodeError) as failure:
        read_embedded_gcode(LocalBackend(), "busy.3mf")  # type: ignore[arg-type]
    assert failure.value.code == "embedded_gcode_busy"
    release.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
