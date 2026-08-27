"""Named multi-row states that three or more test files all need.

A scenario is a *promotion*, never a first draft. The bar for adding one is:

1. Three separate test files build the same multi-row shape, and
2. the shape has a name someone would use out loud ("a printed model", "a
   printer with a queue"), and
3. every row in it is load-bearing for all three callers.

Below three, the assembly stays inline in the test that needs it — a scenario
with one caller is a helper with extra indirection, and a scenario nobody can
name is a bag of rows whose contents the reader has to go and look up anyway.
Failing (3) is the common trap: if one caller needs a row the others do not, the
scenario is really two scenarios, and merging them means every test carries setup
it does not use and readers cannot tell which rows matter.

Each function here documents *why its shape is a unit* — what breaks if a row is
missing — because that is the thing a caller cannot see from the call site. When
a scenario stops having three callers, delete it and inline it back.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from app.db.models import (
    File,
    FileRevisionStatus,
    FileType,
    Model,
    Printer,
    PrintJobState,
    User,
)
from tests.factories.identity import build_user, grant_collection_role
from tests.factories.library import build_collection, build_file, build_model
from tests.factories.printers import build_print_job, build_printer


def a_model_with_gcode(
    session: Session, name: str = "Bracket", **overrides: Any
) -> tuple[Model, File]:
    """A model with one recommended, known-good G-code revision.

    This is the shape the print and fleet paths assume: dispatch only considers a
    *recommended* revision, and the queue endpoints only accept a `known_good`
    one. A model with an unmarked G-code file looks complete in the database and
    is invisible to both, which is why so many "the queue is empty" failures
    trace back to this setup rather than to the code under test.
    """
    model = build_model(session, name, **overrides)
    gcode = build_file(
        session,
        model,
        file_type=FileType.GCODE,
        recommended=True,
        status=FileRevisionStatus.KNOWN_GOOD,
    )
    return model, gcode


def a_printer_with_a_queue(
    session: Session, *, depth: int = 2, **overrides: Any
) -> tuple[Printer, list[File]]:
    """A ready printer with *depth* queued jobs, in queue order.

    Ordering is the point: `queue_position` is what the scheduler reads, and jobs
    created without it all sit at position 0, where "the next job" becomes
    whichever row the database happens to return first. Every reordering,
    draining and dispatch test needs a queue whose order is actually defined.
    """
    printer = build_printer(session, **overrides)
    artifacts: list[File] = []
    for position in range(depth):
        _model, gcode = a_model_with_gcode(session, f"Queued {position + 1}")
        build_print_job(
            session,
            gcode,
            printer=printer,
            state=PrintJobState.QUEUED,
            queue_position=position,
        )
        artifacts.append(gcode)
    return printer, artifacts


def a_member_who_can_see_one_collection(
    session: Session, *, role=None
) -> tuple[User, Model, Model]:
    """A non-superuser with a grant on one of two collections.

    Returns the user, the model they can reach, and the model they cannot. Both
    halves are needed for the assertion to mean anything: a test that only builds
    the visible model passes identically against a broken filter that returns
    everything.
    """
    from app.db.models import CollectionRole

    member = build_user(session)
    visible = build_collection(session, "Visible")
    hidden = build_collection(session, "Hidden")
    grant_collection_role(session, member, visible, role or CollectionRole.VIEW)
    return (
        member,
        build_model(session, "Allowed", collection=visible),
        build_model(session, "Denied", collection=hidden),
    )
