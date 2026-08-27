"""Row builders and named scenarios — the arrange step for the whole suite.

Every builder here takes an explicit `Session` first and **commits**. Most tests
never import them directly: `tests/integration/conftest.py` exposes the
session-bound ones as `make_*` fixtures, so a test writes `make_model("Bracket")`
and never threads a session through its arrange step. Import from here when you
need a builder somewhere a fixture cannot reach — inside another fixture, in a
`conftest.py`, or from a test that manages its own engine.

**What belongs in a builder.** One row, and the *state* a caller cares about
named as a keyword rather than as the column that encodes it: `trashed=True`
rather than `deleted_at=utcnow()`, `provider=BAMBU_LAN` rather than four
credential fields, `scanning=True` rather than a token plus an expiry plus a job
id. Where a keyword exists, it is because getting the encoding wrong produces a
row that inserts cleanly and is then invisible to the code under test — a silent
false pass, not a failure.

**What does not.** Anything that is only true for one test. Every builder takes
`**overrides` straight through to the model, so a one-off field is set at the
call site where the reader can see it, and the builder stays readable for the
next person.

Layout mirrors the domain, not the tables: `identity` (who is asking),
`library` (models and artifacts), `printers` (the fleet), `provenance` (where a
model came from), `capture` (the inbox pipeline), `ops` (everything operational).
`scenarios` holds multi-row shapes promoted once three files needed them; read
its docstring before adding one.

Full guidance: `.agents/skills/create-tests/references/fixtures.md`.
"""

from __future__ import annotations

from tests.factories._support import nth, reset_counters, save, unique_hash
from tests.factories.capture import (
    build_capture_slot,
    build_inbox_item,
    build_inbox_result,
    capture_source,
    manifest_for_source,
)
from tests.factories.identity import (
    PASSWORD,
    bearer,
    build_user,
    grant_collection_role,
    grant_printer_role,
)
from tests.factories.library import (
    build_collection,
    build_file,
    build_metadata,
    build_model,
    build_tag,
    tag_model,
)
from tests.factories.ops import (
    build_audit_finding,
    build_audit_run,
    build_background_job,
    build_document,
    build_external_library,
    build_filament_profile,
    build_notification_channel,
    build_share_link,
)
from tests.factories.printers import (
    build_print_job,
    build_printer,
    build_printer_file,
)
from tests.factories.provenance import (
    build_artifact_link,
    build_capture,
    build_cover,
    build_provenance_source,
)
from tests.factories.scenarios import (
    a_member_who_can_see_one_collection,
    a_model_with_gcode,
    a_printer_with_a_queue,
)

__all__ = [
    "PASSWORD",
    "a_member_who_can_see_one_collection",
    "a_model_with_gcode",
    "a_printer_with_a_queue",
    "bearer",
    "build_artifact_link",
    "build_audit_finding",
    "build_audit_run",
    "build_background_job",
    "build_capture",
    "build_capture_slot",
    "build_collection",
    "build_cover",
    "build_document",
    "build_external_library",
    "build_file",
    "build_filament_profile",
    "build_inbox_item",
    "build_inbox_result",
    "build_metadata",
    "build_model",
    "build_notification_channel",
    "build_print_job",
    "build_printer",
    "build_printer_file",
    "build_provenance_source",
    "build_share_link",
    "build_tag",
    "build_user",
    "capture_source",
    "grant_collection_role",
    "grant_printer_role",
    "manifest_for_source",
    "nth",
    "reset_counters",
    "save",
    "tag_model",
    "unique_hash",
]
