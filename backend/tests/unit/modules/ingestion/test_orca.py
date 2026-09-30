"""Orca intake accepts only bounded, internally consistent native context.

The hook is an untrusted client. These pure contract tests keep malformed source
identity out of the database path before exact, permission-aware resolution runs.
"""

from __future__ import annotations

import json

import pytest

from app.modules.ingestion import orca


@pytest.fixture
def native_payload() -> dict:
    return {
        "version": 1,
        "classification": "single_object",
        "source": {
            "filename": "bracket.stl",
            "object_count": 1,
            "object_labels": [
                {"name": "bracket.stl", "object_id": "0", "copy_index": 0}
            ],
        },
        "slicer": {},
        "printer": {},
        "filaments": [],
        "process": {},
        "print_stats": {},
        "field_sources": {"source.filename": "gcode_object_label"},
    }


class TestParseNativeContext:
    def test_treats_an_empty_field_as_legacy_intake(self) -> None:
        assert orca.parse_native_context("  ") is None

    def test_rejects_an_oversized_context(self) -> None:
        with pytest.raises(
            orca.OrcaContextError, match="orca_native_context_too_large"
        ):
            orca.parse_native_context("x" * (64 * 1024 + 1))

    def test_rejects_an_invalid_contract(self) -> None:
        with pytest.raises(orca.OrcaContextError, match="orca_native_context_invalid"):
            orca.parse_native_context('{"version": 2}')

    def test_rejects_multi_object_labels_disguised_as_single(
        self, native_payload: dict
    ) -> None:
        native_payload["source"]["object_labels"].append(
            {"name": "stand.stl", "object_id": "1", "copy_index": 0}
        )

        with pytest.raises(orca.OrcaContextError, match="orca_native_context_invalid"):
            orca.parse_native_context(json.dumps(native_payload))


class TestNormalizedSourceFilename:
    def test_reduces_a_client_path_to_its_exact_basename(self) -> None:
        assert (
            orca.normalized_source_filename(r'"C:\\Models\\bracket.stl"')
            == "bracket.stl"
        )

    def test_rejects_a_path_without_a_filename(self) -> None:
        with pytest.raises(orca.OrcaContextError, match="orca_source_filename_invalid"):
            orca.normalized_source_filename("..")


class TestStoredContext:
    def test_preserves_which_optional_fields_the_hook_supplied(
        self, native_payload: dict
    ) -> None:
        context = orca.parse_native_context(json.dumps(native_payload))

        stored = orca.stored_context(context)

        assert stored == native_payload


class TestIngestionKey:
    def test_is_stable_for_identical_input(self, native_payload: dict) -> None:
        context = orca.parse_native_context(json.dumps(native_payload))
        assert context is not None

        first = orca.ingestion_key(7, "a" * 64, context)
        second = orca.ingestion_key(7, "a" * 64, context)

        assert first == second

    def test_rejects_noncanonical_hex(self, native_payload: dict) -> None:
        context = orca.parse_native_context(json.dumps(native_payload))
        assert context is not None

        with pytest.raises(orca.OrcaContextError, match="orca_submission_id_invalid"):
            orca.ingestion_key(7, " " + "a" * 62 + " ", context)
