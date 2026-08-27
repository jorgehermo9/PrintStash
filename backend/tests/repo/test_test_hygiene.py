"""Invariants that stop the suite's own structure decaying again.

This suite reached 347 module-level `_helper` functions before anyone counted.
`_user` existed thirteen times and disagreed with itself about whether its default
was a superuser; `make_model` existed twice with incompatible argument orders;
four test files imported private helpers out of *other test files*. None of that
was one bad decision — it was the same reasonable local decision made
independently many times, which is exactly the kind of drift a review does not
catch and a test does.

So each rule here is a habit that has already cost this repo real debugging time,
turned into something that fails loudly instead:

* No test module imports another test module. That coupling meant deleting a
  helper broke collection in an unrelated directory.
* No test builds a `User`, `Model`, `File` or `Printer` row by hand. The
  builders encode which columns silently mislead — a hand-built row that gets one
  wrong inserts cleanly and is then invisible to the code under test, so the test
  passes against nothing.
* No two files define a row builder with the same name. That is the divergence
  that made the identical call mean different things in different files.

Each rule names the fix in its failure message, because the person who trips it is
usually not the person who read the guidance.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

TESTS_ROOT = Path(__file__).resolve().parents[1]

# Row constructions the factories cover. Anything here built inline is either a
# missed migration or a builder that needs extending.
FACTORY_OWNED_MODELS = {
    "User": "build_user",
    "Model": "build_model",
    "File": "build_file",
    "Printer": "build_printer (or printer_config for an unsaved row)",
    "Collection": "build_collection",
    "PrintJob": "build_print_job",
    "CollectionPermission": "grant_collection_role",
}

# Files that legitimately construct rows directly.
CONSTRUCTION_ALLOWED = {
    "tests/factories",  # the builders themselves
    "tests/integration/_backup_harness.py",  # seeds a separate engine's schema
    "tests/repo",  # these invariants, and the factory tests
}

# Names that read like a row builder. A second definition of one of these in a
# different file is the divergence this rule exists to catch.
BUILDER_NAME = re.compile(
    r"^_(make_|build_)?(user|model|file|artifact|printer|collection|gcode|"
    r"source|library|document|job|item|slot|printer_file|print_job)s?$"
)


# ---------------------------------------------------------------------------
# The remaining ratchet.
#
# `PENDING_INLINE_CONSTRUCTION` is gone: every test file now builds its rows
# through `tests/factories/`, so that rule is absolute rather than aspirational.
# This one is the last of the pair — a handful of local builder *names* still
# shadow a factory, and each one removed narrows the gap. **The list may only
# ever shrink.**
#
# Migrating one is usually mechanical: delete the local builder, call the
# factory, and make any state the local default was hiding explicit at the call
# site. See .agents/skills/create-tests/references/fixtures.md
# ---------------------------------------------------------------------------
PENDING_DUPLICATE_BUILDERS = {
    "_job",
    "_make_file",
    "_make_item",
    "_make_model",
    "_make_user",
    "_model",
    "_printer",
    "_source",
}


CORE_TESTS_ROOT = TESTS_ROOT.parent / "packages" / "printstash-core" / "tests"


def _test_modules() -> list[Path]:
    return sorted(
        path
        for path in TESTS_ROOT.rglob("test_*.py")
        if "__pycache__" not in path.parts
    )


def _all_test_modules() -> list[Path]:
    """Every pytest module in the repo, `printstash-core`'s included.

    The rules below split into two sets, and the split is not arbitrary. The
    factory rules are backend-only: `printstash-core` has no database and no
    `tests/factories`, so applying them there would flag class names that merely
    collide with an app model. Everything about *shape* — a contract header, a
    group per unit, a name that names one behaviour — applies to both trees,
    because both are read by the same people for the same reasons.
    """
    return sorted(
        [*_test_modules()]
        + [
            path
            for path in CORE_TESTS_ROOT.rglob("test_*.py")
            if "__pycache__" not in path.parts
        ]
    )


def _relative(path: Path) -> str:
    return str(path.relative_to(TESTS_ROOT.parent))


def _is_allowed(path: Path) -> bool:
    return any(fragment in _relative(path) for fragment in CONSTRUCTION_ALLOWED)


class TestSuiteHygiene:
    @pytest.mark.parametrize("module", _all_test_modules(), ids=_relative)
    def test_every_test_belongs_to_a_group(self, module: Path) -> None:
        """No test is defined at module level. Every one lives in a `class Test*`.

        The group names the production unit its tests exercise, which is what
        turns "what covers `scan_library`?" from a grep into a lookup — and it is
        the only reason a 900-line file is navigable at all. A test at module
        level belongs to nothing, so it accumulates in whatever order it was
        written and drifts away from the code it defends.

        This is absolute rather than a ratchet: the whole suite was converted, so
        the first module-level test to reappear is the regression.
        """
        offenders = [
            node.name
            for node in ast.parse(module.read_text(encoding="utf-8")).body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
        ]

        assert not offenders, (
            f"{_relative(module)} defines {len(offenders)} test(s) at module "
            f"level: {', '.join(offenders[:5])}. Move each into the "
            "`class Test<Unit>` for the production unit it exercises, in that "
            "module's own order. See .agents/skills/create-tests/SKILL.md"
        )

    @pytest.mark.parametrize("module", _all_test_modules(), ids=_relative)
    def test_every_file_opens_with_a_contract_header(self, module: Path) -> None:
        """A test file says what it defends, in prose, before its first import.

        Not a restatement of the filename. The header is where the *reason* a rule
        exists lives — and that reason is the thing a reader needs when the file goes
        red six months from now and the obvious fix is to delete the assertion.
        """
        header = ast.get_docstring(ast.parse(module.read_text(encoding="utf-8")))

        assert header, (
            f"{_relative(module)} has no module docstring. Open it with a few lines "
            "on what this file defends and why it matters when it goes red — see "
            "Inside a test file in .agents/skills/create-tests/SKILL.md"
        )

    @pytest.mark.parametrize("module", _test_modules(), ids=_relative)
    def test_no_test_module_imports_another_test_module(self, module: Path) -> None:
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        offenders = [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("tests.")
            and ".test_" in f".{node.module.rsplit('.', 1)[-1]}"
        ]

        assert not offenders, (
            f"{_relative(module)} imports from another test module: {offenders}. "
            "A test module is not an API. Move the shared thing into "
            "tests/factories/ (rows), tests/_env.py (environment), or the nearest "
            "conftest.py (fixtures) — deleting a helper must not break collection "
            "in another directory."
        )

    @pytest.mark.parametrize("module", _test_modules(), ids=_relative)
    def test_rows_are_built_through_the_factories(self, module: Path) -> None:
        """No test file builds a factory-owned row by hand. Every file, no exemptions.

        This used to carry a per-file exemption list while the migration ran; it does
        not any more, and that is the point of keeping the docstring here. The reason
        the rule is absolute is that an inline row fails *silently*: `deleted_at=`
        instead of `trashed=` produces a row every read path filters out, and a
        printer missing three of its provider's four credential fields inserts
        happily and then fails somewhere unrelated. Neither looks like a setup bug.

        If a file genuinely cannot use a builder, the answer is a factory that covers
        its case — `printer_config` and the `detached_*` helpers exist because of
        exactly that — or an entry in `CONSTRUCTION_ALLOWED` with a reason.
        """
        if _is_allowed(module):
            pytest.skip("this file legitimately constructs rows directly")
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        offenders: set[str] = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in FACTORY_OWNED_MODELS
                # A bare `Model()` with no arguments is a sentinel or a type probe,
                # not a row somebody meant to persist.
                and (node.args or node.keywords)
            ):
                offenders.add(node.func.id)

        assert not offenders, (
            f"{_relative(module)} constructs "
            + ", ".join(
                f"{name}() (use {FACTORY_OWNED_MODELS[name]})"
                for name in sorted(offenders)
            )
            + ". The builders encode which columns silently mislead — `trashed=` "
            "rather than `deleted_at`, `provider=` rather than four credential "
            "fields — and a row that gets one wrong is invisible to the code under "
            "test rather than an error. See "
            ".agents/skills/create-tests/references/fixtures.md"
        )

    def test_no_new_duplicate_row_builder_names_appear(self) -> None:
        """Two files defining the same builder name is how `_user` drifted.

        Thirteen copies, and two different defaults for `superuser`, so the identical
        call meant opposite things depending on which file you were reading. The
        remaining pairs are listed in `PENDING_DUPLICATE_BUILDERS` and that list may
        only shrink — a *new* duplicate name fails here immediately.
        """
        definitions: dict[str, list[str]] = {}
        for module in _test_modules():
            if _is_allowed(module):
                continue
            tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
            for node in tree.body:
                if isinstance(node, ast.FunctionDef) and BUILDER_NAME.match(node.name):
                    definitions.setdefault(node.name, []).append(_relative(module))

        duplicated = {
            name for name, files in definitions.items() if len(files) > 1
        } - PENDING_DUPLICATE_BUILDERS

        assert not duplicated, (
            f"new duplicate row-builder name(s): {sorted(duplicated)}. Promote the "
            "builder to tests/factories/ rather than defining it a second time — see "
            ".agents/skills/create-tests/references/fixtures.md"
        )

    def test_the_duplicate_builder_list_has_no_stale_entries(self) -> None:
        """A name that is no longer duplicated must leave the list in the same commit."""
        definitions: dict[str, int] = {}
        for module in _test_modules():
            if _is_allowed(module):
                continue
            tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
            for node in tree.body:
                if isinstance(node, ast.FunctionDef) and BUILDER_NAME.match(node.name):
                    definitions[node.name] = definitions.get(node.name, 0) + 1

        resolved = sorted(
            name for name in PENDING_DUPLICATE_BUILDERS if definitions.get(name, 0) <= 1
        )

        assert not resolved, (
            f"no longer duplicated: {resolved}. Remove them from "
            "PENDING_DUPLICATE_BUILDERS so the list keeps meaning something."
        )

    def test_no_new_test_names_join_two_behaviours(self) -> None:
        """A test whose name needs "and" usually asserts two things.

        When it does, a failure cannot say which half broke, and the fix is two
        tests. This does not split the existing ones — it stops the count growing,
        and every one removed lowers the cap.
        """
        offenders = []
        for module in _all_test_modules():
            tree = ast.parse(module.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name.startswith("test_")
                    and "_and_" in node.name
                ):
                    offenders.append(f"{_relative(module)}::{node.name}")

        assert len(offenders) <= MAX_CONJUNCTION_NAMES, (
            f"{len(offenders)} test names contain `_and_`, over the cap of "
            f'{MAX_CONJUNCTION_NAMES}. A name needing "and" usually means two '
            "behaviours in one test — split it, so a failure says which half broke. "
            "New: " + ", ".join(sorted(offenders)[:5])
        )
        if len(offenders) < MAX_CONJUNCTION_NAMES:
            raise AssertionError(
                f"{len(offenders)} names now, cap is {MAX_CONJUNCTION_NAMES}. Lower "
                "MAX_CONJUNCTION_NAMES to match so the cap keeps meaning something."
            )


# Test names containing `_and_`, across both test trees. Some are two behaviours
# in one test, which the skill forbids because a failure cannot say which half
# broke; others describe a single invariant that happens to need the word. Both
# are worth reducing, and neither is worth a *mechanical* split — that produces
# duplicated setup and assertions in the wrong test. So the count is capped and
# may only fall, and the honest fix per test is one of two things: split it, or
# rename it to say the single behaviour it actually asserts.
MAX_CONJUNCTION_NAMES = 249
