from __future__ import annotations

import ast
from pathlib import Path

import pytest

CORE_ROOT = Path(__file__).parents[1]
SOURCE_ROOTS = (
    CORE_ROOT / "src" / "printstash_core",
    CORE_ROOT / "testkit" / "printstash_core_testkit",
)
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


@pytest.mark.parametrize(
    "source_path",
    sorted(path for root in SOURCE_ROOTS for path in root.rglob("*.py")),
)
def test_core_package_has_no_forbidden_imports(source_path: Path) -> None:
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    imported_roots: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(
                alias.name.split(".", 1)[0].lower() for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0].lower())

    assert imported_roots.isdisjoint(FORBIDDEN_ROOTS)


def test_runtime_package_has_no_mandatory_dependencies() -> None:
    """The wheel remains importable without any optional integration extras."""
    import tomllib

    metadata = tomllib.loads((CORE_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["project"]["dependencies"] == []
