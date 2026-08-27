"""Keeping `printstash-core` a library rather than part of the application.

`printstash-core` holds the logic that has no business knowing about FastAPI, a
database, or a cloud SDK: G-code parsing, mesh rasterising, URL safety, provider
wire clients. That boundary is what makes the package testable in isolation and
installable without the application's dependency tree — and it is the kind of
boundary that erodes one convenient import at a time.

An `import app...` inside it would not fail any other test. It would simply make
the package depend on the application, and the coupling would be discovered much
later by whoever tried to reuse it.

So this walks the AST of every module and asserts the forbidden roots are absent,
with a narrower list for the testkit (which may know about FastAPI, since it
serves fakes over HTTP) and a check that the runtime package declares no
*mandatory* dependency on the optional infrastructure ones.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.paths import CORE_PACKAGE_ROOT

CORE_ROOT = CORE_PACKAGE_ROOT
RUNTIME_ROOT = CORE_ROOT / "src" / "printstash_core"
TESTKIT_ROOT = CORE_ROOT / "src" / "printstash_core_testkit"
FORBIDDEN_ROOTS = {
    "app",
    "fastapi",
    "sqlmodel",
    "sqlalchemy",
    "boto3",
    "stripe",
    "workos",
    "psycopg",
    "asyncpg",
    "aiosqlite",
    "pymysql",
    "mysql",
    "sqlite3",
}
TESTKIT_FORBIDDEN_ROOTS = FORBIDDEN_ROOTS - {"fastapi"}


class TestImportBoundaries:
    @pytest.mark.parametrize(
        "source_path",
        sorted(RUNTIME_ROOT.rglob("*.py")),
    )
    def test_core_package_has_no_forbidden_imports(self, source_path: Path) -> None:
        tree = ast.parse(
            source_path.read_text(encoding="utf-8"), filename=str(source_path)
        )
        imported_roots: set[str] = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(
                    alias.name.split(".", 1)[0].lower() for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0].lower())

        assert imported_roots.isdisjoint(FORBIDDEN_ROOTS)

    @pytest.mark.parametrize("source_path", sorted(TESTKIT_ROOT.rglob("*.py")))
    def test_testkit_has_no_application_or_infrastructure_imports(
        self,
        source_path: Path,
    ) -> None:
        tree = ast.parse(
            source_path.read_text(encoding="utf-8"), filename=str(source_path)
        )
        imported_roots: set[str] = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(
                    alias.name.split(".", 1)[0].lower() for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0].lower())

        assert imported_roots.isdisjoint(TESTKIT_FORBIDDEN_ROOTS)

    def test_runtime_package_has_no_mandatory_dependencies(self) -> None:
        """The wheel remains importable without any optional integration extras."""
        import tomllib

        metadata = tomllib.loads(
            (CORE_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        assert metadata["project"]["dependencies"] == []
