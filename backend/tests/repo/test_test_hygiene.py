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
    "Printer": "build_printer",
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
# The ratchet.
#
# These files still build rows inline; the migration to `tests/factories/` is
# in progress. **This list may only ever shrink.** A new file cannot be added to
# it — `test_the_pending_list_has_no_stale_entries` fails if a listed file has
# already been cleaned up, so the count here is the real remaining debt rather
# than a number somebody forgot to update.
#
# Migrating one is usually mechanical: delete its local builder, call the
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

PENDING_INLINE_CONSTRUCTION = {
    "tests/contract/services/test_elegoo_centauri.py",
    "tests/contract/services/test_octoprint.py",
    "tests/contract/services/test_prusalink.py",
    "tests/e2e/test_fleet.py",
    "tests/e2e/test_library_transfer.py",
    "tests/e2e/test_printer_rbac.py",
    "tests/integration/api/v1/inbox/test_lifecycle.py",
    "tests/integration/api/v1/ingest/test_import_progress.py",
    "tests/integration/api/v1/ingest/test_ingest_api.py",
    "tests/integration/api/v1/models/test_listing.py",
    "tests/integration/api/v1/models/test_print_jobs.py",
    "tests/integration/api/v1/models/test_print_stats.py",
    "tests/integration/api/v1/models/test_provenance.py",
    "tests/integration/api/v1/models/test_star.py",
    "tests/integration/api/v1/printers/test_config.py",
    "tests/integration/api/v1/printers/test_control.py",
    "tests/integration/api/v1/printers/test_files.py",
    "tests/integration/api/v1/printers/test_rbac.py",
    "tests/integration/api/v1/printers/test_status.py",
    "tests/integration/api/v1/printers/test_websocket.py",
    "tests/integration/api/v1/taxonomy/test_collection_readme.py",
    "tests/integration/api/v1/taxonomy/test_taxonomy_api.py",
    "tests/integration/api/v1/test_config.py",
    "tests/integration/api/v1/test_documents.py",
    "tests/integration/api/v1/test_fleet.py",
    "tests/integration/api/v1/test_health.py",
    "tests/integration/api/v1/test_setup.py",
    "tests/integration/api/v1/test_share.py",
    "tests/integration/core/metrics/test_r2_ops.py",
    "tests/integration/core/test_secrets.py",
    "tests/integration/core/test_security.py",
    "tests/integration/db/migrations/test_migrations.py",
    "tests/integration/main/test_main_lifespan.py",
    "tests/integration/postgres/test_contracts.py",
    "tests/integration/services/fleet/test_material_aware_fleet.py",
    "tests/integration/services/ingestion/test_ingestion_atomicity.py",
    "tests/integration/services/model_views/test_export_payload.py",
    "tests/integration/services/model_views/test_listing.py",
    "tests/integration/services/model_views/test_model_views_n_plus_one.py",
    "tests/integration/services/model_views/test_service_helpers.py",
    "tests/integration/services/model_views/test_structured_filters.py",
    "tests/integration/services/provider_connections/test_cults.py",
    "tests/integration/services/provider_connections/test_mmf_tokens.py",
    "tests/integration/services/provider_connections/test_oauth.py",
    "tests/integration/services/provider_connections/test_pairing.py",
    "tests/integration/services/rbac/test_collection_rbac.py",
    "tests/integration/services/rbac/test_rbac_sql.py",
    "tests/integration/services/runtime_config/test_runtime_config.py",
    "tests/integration/services/test_audit.py",
    "tests/integration/services/test_auth.py",
    "tests/integration/services/test_backup.py",
    "tests/integration/services/test_import_resolvers.py",
    "tests/integration/services/test_job_import.py",
    "tests/integration/services/test_jobs.py",
    "tests/integration/services/test_library_transfer.py",
    "tests/integration/services/test_oidc.py",
    "tests/integration/services/test_print_results.py",
    "tests/integration/services/test_printer_hub.py",
    "tests/integration/services/test_printer_jobs.py",
    "tests/integration/services/test_provenance.py",
    "tests/integration/services/test_share.py",
    "tests/integration/services/test_source_covers.py",
    "tests/integration/services/test_spoolman.py",
    "tests/integration/services/test_vault_audit.py",
    "tests/integration/services/trash/test_external_trash_corner_cases.py",
    "tests/integration/services/trash/test_gc.py",
    "tests/integration/services/trash/test_hard_delete.py",
    "tests/integration/services/trash/test_purge_claims.py",
    "tests/integration/services/trash/test_trash_remote_backend.py",
    "tests/unit/services/bambu_adapter/test_bambu_adapter.py",
    "tests/unit/services/bambu_adapter/test_printer_provider.py",
    "tests/unit/services/printer_provider/test_build_requirements.py",
    "tests/unit/services/printer_provider/test_provider_conformance.py",
}


def _test_modules() -> list[Path]:
    return sorted(
        path
        for path in TESTS_ROOT.rglob("test_*.py")
        if "__pycache__" not in path.parts
    )


def _relative(path: Path) -> str:
    return str(path.relative_to(TESTS_ROOT.parent))


def _is_allowed(path: Path) -> bool:
    return any(fragment in _relative(path) for fragment in CONSTRUCTION_ALLOWED)


@pytest.mark.parametrize("module", _test_modules(), ids=_relative)
def test_every_file_opens_with_a_contract_header(module: Path) -> None:
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
def test_no_test_module_imports_another_test_module(module: Path) -> None:
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
def test_rows_are_built_through_the_factories(module: Path) -> None:
    if _is_allowed(module):
        pytest.skip("this file legitimately constructs rows directly")
    if _relative(module) in PENDING_INLINE_CONSTRUCTION:
        pytest.xfail(
            "not migrated to the factories yet; see PENDING_INLINE_CONSTRUCTION"
        )

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
            f"{name}() (use {FACTORY_OWNED_MODELS[name]})" for name in sorted(offenders)
        )
        + ". The builders encode which columns silently mislead — `trashed=` "
        "rather than `deleted_at`, `provider=` rather than four credential "
        "fields — and a row that gets one wrong is invisible to the code under "
        "test rather than an error. See "
        ".agents/skills/create-tests/references/fixtures.md"
    )


def test_no_new_duplicate_row_builder_names_appear() -> None:
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


def test_the_duplicate_builder_list_has_no_stale_entries() -> None:
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


# Test names containing `_and_`. Some are two behaviours in one test, which the
# skill forbids because a failure cannot say which half broke; others describe a
# single invariant that happens to need the word. Both are worth reducing, and
# neither is worth a mechanical split — that produces duplicated setup and
# assertions in the wrong test. So the count is capped and may only fall.
MAX_CONJUNCTION_NAMES = 323


def test_no_new_test_names_join_two_behaviours() -> None:
    """A test whose name needs "and" usually asserts two things.

    When it does, a failure cannot say which half broke, and the fix is two
    tests. This does not split the existing ones — it stops the count growing,
    and every one removed lowers the cap.
    """
    offenders = []
    for module in _test_modules():
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


def test_the_pending_list_has_no_stale_entries() -> None:
    """A migrated file must be removed from the ratchet in the same commit.

    Without this the list would only ever be appended to, and its length would
    stop meaning anything. It is also the nudge that makes the next migration
    cheap: the failure names the exact line to delete.
    """
    known = {_relative(module) for module in _test_modules()}
    missing = sorted(PENDING_INLINE_CONSTRUCTION - known)

    assert not missing, (
        f"these files no longer exist: {missing}. Remove them from "
        "PENDING_INLINE_CONSTRUCTION."
    )
