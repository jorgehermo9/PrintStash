"""Anchors for everything a test reads off disk.

Tests live in a mirrored tree, so a file's depth below ``tests/`` changes whenever it
moves. Computing ``Path(__file__).resolve().parents[2]`` inside a test hard-codes that
depth: the test then breaks on a move for a reason that has nothing to do with what it
asserts. Import the anchor instead — it is resolved from this module, which does not
move.
"""

from __future__ import annotations

from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = TESTS_DIR.parent
REPO_ROOT = BACKEND_DIR.parent

FIXTURES_DIR = TESTS_DIR / "fixtures"
"""Committed test data: real slicer output, meshes, the OpenAPI contract snapshot."""

TESTDATA_DIR = REPO_ROOT / "testdata"
"""Large real-world models, gitignored and optional — guard reads with ``.exists()``."""

ALEMBIC_INI = BACKEND_DIR / "alembic.ini"
ALEMBIC_DIR = BACKEND_DIR / "alembic"

CORE_PACKAGE_ROOT = BACKEND_DIR / "packages" / "printstash-core"
