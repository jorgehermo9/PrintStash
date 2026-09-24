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

from tests.paths import BACKEND_DIR, CORE_PACKAGE_ROOT

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


class TestSoftDeleteScopes:
    """Soft-delete filtering goes through `app.db.scopes`, never spelled out by hand.

    `live()` and `trashed()` are the single place the rule lives. A query that writes
    `deleted_at.is_(None)` itself is a query that will not follow when the rule
    changes — and there is more to it than one column: `CONTEXT.md` makes live/trashed
    binding vocabulary, and the scopes are its only implementation.

    Two of these had drifted into `app/api/v1/admin.py`. Both were correct on the day
    they were written, which is exactly why a guard is worth more than a review.
    """

    def test_no_production_module_filters_soft_deletes_by_hand(self) -> None:
        offenders = []
        for module in sorted((BACKEND_DIR / "app").rglob("*.py")):
            if module.name == "scopes.py":
                continue
            source = module.read_text(encoding="utf-8")
            for pattern in ("deleted_at.is_(None)", "deleted_at == None"):
                if pattern in source:
                    offenders.append(f"{module.relative_to(BACKEND_DIR)} ({pattern})")

        assert not offenders, (
            "these modules filter soft-deleted rows by hand: "
            + ", ".join(offenders)
            + ". Use `live(Model)` / `trashed(Model)` from app.db.scopes."
        )


APP_ROOT = BACKEND_DIR / "app"
ENGINE_ROOT = APP_ROOT / "runtime" / "engine"
# Long-lived supervisors and request-scoped tasks are not background work: they
# end with their process or request and only ever nudge the engine.
BACKGROUND_TASK_OWNERS = {
    "app/runtime/realtime.py": "the event bus's NOTIFY listener",
    "app/modules/printing/printer_hub.py": "printer connection supervisors",
    "app/modules/sources/library_watcher.py": "folder watchers, which nudge",
    "app/api/vault_generation.py": "an admission the request itself awaits",
    "app/api/v1/system.py": "a restart of this API process after the response",
}


def _imported_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def _schedules_work(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in {
            "create_task",
            "ensure_future",
            "add_task",
        }:
            return True
        if isinstance(node, ast.Name) and node.id == "BackgroundTasks":
            return True
    return False


class TestBackgroundWorkBoundaries:
    """Background work goes through the ``JobEngine`` port, and only its adapter knows DBOS.

    A job hand-rolled with ``asyncio.create_task`` or ``BackgroundTasks`` is lost
    on a restart, invisible to the Jobs API, runs outside every lane's
    concurrency and is never reconciled. And a module that imports ``dbos``
    directly ties itself to one engine, so the inline test engine no longer
    stands in for it.
    """

    def test_only_the_engine_adapter_imports_dbos(self) -> None:
        offenders = [
            str(module.relative_to(BACKEND_DIR))
            for module in sorted(APP_ROOT.rglob("*.py"))
            if ENGINE_ROOT not in module.parents
            and "dbos" in _imported_roots(ast.parse(module.read_text(encoding="utf-8")))
        ]

        assert not offenders, f"dbos imported outside app/runtime/engine: {offenders}"

    def test_the_core_library_knows_no_engine(self) -> None:
        offenders = [
            str(module)
            for module in sorted(RUNTIME_ROOT.rglob("*.py"))
            if "dbos" in _imported_roots(ast.parse(module.read_text(encoding="utf-8")))
        ]

        assert not offenders

    def test_no_module_schedules_work_behind_the_engines_back(self) -> None:
        offenders = [
            path
            for module in sorted(APP_ROOT.rglob("*.py"))
            if (path := str(module.relative_to(BACKEND_DIR)))
            not in BACKGROUND_TASK_OWNERS
            and _schedules_work(ast.parse(module.read_text(encoding="utf-8")))
        ]

        assert not offenders, (
            "background work must be a job definition run by the JobEngine, not "
            f"create_task/BackgroundTasks: {offenders}"
        )

    def test_every_listed_owner_still_needs_its_exception(self) -> None:
        # An exception that outlived its reason is how the list grows quietly.
        stale = [
            path
            for path in BACKGROUND_TASK_OWNERS
            if not _schedules_work(
                ast.parse((BACKEND_DIR / path).read_text(encoding="utf-8"))
            )
        ]

        assert not stale

    @pytest.mark.parametrize(
        ("source", "schedules"),
        [
            ("asyncio.create_task(work())", True),
            ("loop.ensure_future(work())", True),
            ("def route(tasks: BackgroundTasks): tasks.add_task(work)", True),
            ("asyncio.to_thread(work)", False),
            ("nudge('derive.mesh')", False),
        ],
        ids=["create-task", "ensure-future", "background-tasks", "to-thread", "nudge"],
    )
    def test_recognises_what_counts_as_scheduling_work(
        self, source: str, schedules: bool
    ) -> None:
        assert _schedules_work(ast.parse(source)) is schedules
