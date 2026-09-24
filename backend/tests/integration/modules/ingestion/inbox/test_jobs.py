"""Pending Import Jobs: resolving captures, importing them, and retention.

Resolution is pulled: every captured item with a source URL is owed a
resolve, so a capture never depends on the request that made it having
dispatched anything, and the user is waiting on it (interactive, owned by
them). Cancelling a Pending Import's Job fails the item as cancelled but keeps
it retryable; a Job that fails marks the item failed with a display-safe
reason. Settled items are never touched by either. A Pending Import owns its
retry (it may reselect files), so the Job's own retry defers to that flow.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import InboxItem, InboxItemState, WorkPriority
from app.modules.ingestion import inbox
from app.modules.ingestion.inbox import ResolveSource

SOURCE = ResolveSource()
DEFINITIONS = {definition.name: definition for definition in inbox.definitions()}
RESOLVE = DEFINITIONS[inbox.RESOLVE_DEFINITION]
IMPORT = DEFINITIONS[inbox.IMPORT_DEFINITION]


@pytest.fixture
def owner(make_user):
    return make_user()


def _item(session: Session, item_id: int) -> InboxItem:
    session.expire_all()
    item = session.get(InboxItem, item_id)
    assert item is not None
    return item


class TestResolveSource:
    def test_a_captured_url_is_owed_a_resolve(
        self, db_session: Session, owner, make_inbox_item
    ) -> None:
        item = make_inbox_item(owner, source_url="https://www.printables.com/model/1")

        (work,) = SOURCE.pending(db_session, now=utcnow(), limit=10)

        assert (work.subject_key, work.priority, work.owner_user_id) == (
            f"inbox_item/{item.id}",
            WorkPriority.INTERACTIVE,
            owner.id,
        )

    def test_a_capture_without_a_url_has_nothing_to_resolve(
        self, db_session: Session, owner, make_inbox_item
    ) -> None:
        make_inbox_item(owner, source_url=None)

        assert SOURCE.pending(db_session, now=utcnow(), limit=10) == []

    @pytest.mark.parametrize(
        "state",
        [
            InboxItemState.REVIEW,
            InboxItemState.COMPLETED,
            InboxItemState.FAILED,
            InboxItemState.DISMISSED,
        ],
    )
    def test_an_item_past_capture_is_not_offered(
        self, db_session: Session, owner, make_inbox_item, state: InboxItemState
    ) -> None:
        make_inbox_item(owner, state=state, source_url="https://example.com/model")

        assert SOURCE.pending(db_session, now=utcnow(), limit=10) == []

    def test_never_offers_more_than_asked(
        self, db_session: Session, owner, make_inbox_item
    ) -> None:
        for index in range(3):
            make_inbox_item(owner, source_url=f"https://example.com/model/{index}")

        assert len(SOURCE.pending(db_session, now=utcnow(), limit=2)) == 2

    def test_is_never_due_on_time_alone(self, db_session: Session) -> None:
        assert SOURCE.next_due(db_session, now=utcnow()) is None


class TestWithdraw:
    @pytest.mark.parametrize(
        "state",
        [InboxItemState.CAPTURED, InboxItemState.RESOLVING, InboxItemState.IMPORTING],
    )
    def test_cancelling_fails_the_item_but_keeps_it_retryable(
        self, db_session: Session, owner, make_inbox_item, state: InboxItemState
    ) -> None:
        item = make_inbox_item(owner, state=state)

        IMPORT.cancel(db_session, f"inbox_item/{item.id}")
        db_session.commit()

        withdrawn = _item(db_session, item.id)
        assert (withdrawn.state, withdrawn.error_code, withdrawn.retryable) == (
            InboxItemState.FAILED,
            "cancelled",
            True,
        )

    def test_a_settled_item_is_not_touched(
        self, db_session: Session, owner, make_inbox_item
    ) -> None:
        item = make_inbox_item(owner, state=InboxItemState.COMPLETED)

        RESOLVE.cancel(db_session, f"inbox_item/{item.id}")
        db_session.commit()

        assert _item(db_session, item.id).state == InboxItemState.COMPLETED


class TestFailure:
    def test_a_failed_job_fails_its_item_with_a_safe_reason(
        self, db_session: Session, owner, make_inbox_item
    ) -> None:
        item = make_inbox_item(owner, state=InboxItemState.IMPORTING)

        IMPORT.on_failure(
            db_session, f"inbox_item/{item.id}", "cannot read /srv/private/secret.stl"
        )
        db_session.commit()

        failed = _item(db_session, item.id)
        assert (failed.state, failed.retryable) == (InboxItemState.FAILED, True)
        assert "/srv/private" not in (failed.error_code or "")

    def test_a_captured_item_is_not_failed_by_a_lost_job(
        self, db_session: Session, owner, make_inbox_item
    ) -> None:
        # Still captured, the source offers it again; failing it would strand it.
        item = make_inbox_item(owner, state=InboxItemState.CAPTURED)

        RESOLVE.on_failure(db_session, f"inbox_item/{item.id}", "boom")
        db_session.commit()

        assert _item(db_session, item.id).state == InboxItemState.CAPTURED

    def test_the_job_retry_defers_to_the_pending_import(
        self, db_session: Session, owner, make_inbox_item
    ) -> None:
        item = make_inbox_item(owner, state=InboxItemState.FAILED)

        assert IMPORT.retry(db_session, f"inbox_item/{item.id}") is False


class TestRetention:
    def test_runs_every_hour(self, db_session: Session) -> None:
        source = DEFINITIONS["inbox.retention"].source
        assert source is not None

        assert source.cron(db_session) == "35 * * * *"  # type: ignore[attr-defined]
