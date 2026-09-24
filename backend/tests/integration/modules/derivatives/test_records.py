"""The lifecycle of derivative rows: which kinds an Artifact still needs.

A row's absence at the current recipe is what "pending" means; every state
answers whether the kind is satisfied for now. Ready and skipped are
satisfied until an administrator regenerates the kind. Cancelled is satisfied
(cancelling withdraws intent). Failed waits out an exponential backoff, and a
deterministic failure, or one that exhausted its attempts, is terminal until a
retry or recipe bump. In flight is satisfied unless a lost execution left it
there too long.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlmodel import Session, select

from app.core.config import settings
from app.core.time import utcnow
from app.db.models import ArtifactDerivative, DerivativeRegeneration, DerivativeState
from app.modules.derivatives import records
from app.modules.derivatives.kinds import METADATA, THUMBNAIL


@pytest.fixture
def mesh(make_model, make_file):
    return make_file(make_model(), filename="part.stl")


def _row(**fields) -> ArtifactDerivative:
    fields.setdefault("updated_at", utcnow())
    return ArtifactDerivative(file_id=1, kind=THUMBNAIL, recipe_version=1, **fields)


class TestSatisfied:
    def test_no_row_needs_work(self) -> None:
        assert records.satisfied(None, now=utcnow()) is False

    @pytest.mark.parametrize("state", [DerivativeState.READY, DerivativeState.SKIPPED])
    def test_a_finished_output_is_satisfied(self, state: DerivativeState) -> None:
        assert records.satisfied(_row(state=state), now=utcnow()) is True

    def test_an_output_older_than_a_regeneration_needs_work(self) -> None:
        now = utcnow()
        row = _row(state=DerivativeState.READY, updated_at=now - timedelta(hours=1))

        assert records.satisfied(row, now=now, regenerated_at=now) is False

    def test_cancelled_stays_withdrawn(self) -> None:
        assert records.satisfied(_row(state=DerivativeState.CANCELLED), now=utcnow())

    def test_a_failure_waiting_out_its_backoff_is_satisfied_for_now(self) -> None:
        now = utcnow()
        row = _row(
            state=DerivativeState.FAILED,
            attempts=1,
            next_attempt_at=now + timedelta(minutes=1),
        )

        assert records.satisfied(row, now=now) is True

    def test_a_failure_whose_backoff_expired_needs_work(self) -> None:
        now = utcnow()
        row = _row(
            state=DerivativeState.FAILED,
            attempts=1,
            next_attempt_at=now - timedelta(seconds=1),
        )

        assert records.satisfied(row, now=now) is False

    def test_an_exhausted_failure_is_terminal(self) -> None:
        row = _row(
            state=DerivativeState.FAILED, attempts=settings.derivative_max_attempts
        )

        assert records.satisfied(row, now=utcnow()) is True

    def test_work_in_flight_is_satisfied(self) -> None:
        assert records.satisfied(_row(state=DerivativeState.RUNNING), now=utcnow())

    def test_work_left_in_flight_too_long_needs_work(self) -> None:
        now = utcnow()
        row = _row(
            state=DerivativeState.RUNNING,
            updated_at=now - records.STALE_IN_FLIGHT - timedelta(seconds=1),
        )

        assert records.satisfied(row, now=now) is False


class TestNeeded:
    def test_lists_the_kinds_still_owed(
        self, db_session: Session, mesh, make_derivative
    ) -> None:
        make_derivative(mesh, METADATA)

        needed = records.needed(
            db_session, mesh, {METADATA: 1, THUMBNAIL: 1}, now=utcnow()
        )

        assert needed == {THUMBNAIL}

    def test_a_row_at_an_older_recipe_does_not_count(
        self, db_session: Session, mesh, make_derivative
    ) -> None:
        # A recipe bump is the code saying the output changed.
        make_derivative(mesh, THUMBNAIL, recipe_version=0)

        assert records.needed(db_session, mesh, {THUMBNAIL: 1}, now=utcnow()) == {
            THUMBNAIL
        }

    def test_a_regeneration_makes_a_ready_kind_owed_again(
        self, db_session: Session, mesh, make_derivative
    ) -> None:
        make_derivative(mesh, THUMBNAIL, updated_at=utcnow() - timedelta(hours=1))
        db_session.add(DerivativeRegeneration(kind=THUMBNAIL, requested_at=utcnow()))
        db_session.commit()

        assert records.needed(db_session, mesh, {THUMBNAIL: 1}, now=utcnow()) == {
            THUMBNAIL
        }


class TestBegin:
    def test_opens_a_running_attempt(self, db_session: Session, mesh) -> None:
        row = records.begin(db_session, mesh, THUMBNAIL, 1, now=utcnow())

        assert (row.state, row.attempts) == (DerivativeState.RUNNING, 1)

    def test_a_retry_counts_as_another_attempt(
        self, db_session: Session, mesh, make_derivative
    ) -> None:
        make_derivative(
            mesh,
            THUMBNAIL,
            state=DerivativeState.FAILED,
            attempts=2,
            failure_reason="render_failed",
            next_attempt_at=utcnow(),
        )

        row = records.begin(db_session, mesh, THUMBNAIL, 1, now=utcnow())

        assert (row.attempts, row.failure_reason, row.next_attempt_at) == (
            3,
            None,
            None,
        )


class TestOutcomes:
    def test_ready_records_what_was_published(self, db_session: Session, mesh) -> None:
        row = records.begin(db_session, mesh, THUMBNAIL, 1, now=utcnow())

        records.mark_ready(
            db_session,
            row,
            now=utcnow(),
            storage_key="thumbs/1.webp",
            output={"strategy": "full"},
            duration_ms=12,
            peak_rss_bytes=3 * 1024**3,
        )

        assert (row.state, row.storage_key) == (DerivativeState.READY, "thumbs/1.webp")
        assert row.output_json == '{"strategy":"full"}'
        assert row.peak_rss_bytes == 3 * 1024**3

    def test_ready_without_a_key_keeps_the_published_one(
        self, db_session: Session, mesh
    ) -> None:
        row = records.begin(db_session, mesh, METADATA, 1, now=utcnow())
        row.storage_key = "kept"

        records.mark_ready(db_session, row, now=utcnow())

        assert row.storage_key == "kept"

    def test_skipped_records_why_nothing_was_produced(
        self, db_session: Session, mesh
    ) -> None:
        row = records.begin(db_session, mesh, THUMBNAIL, 1, now=utcnow())

        records.mark_skipped(db_session, row, "x" * 100, now=utcnow())

        assert (row.state, len(row.failure_reason or "")) == (
            DerivativeState.SKIPPED,
            64,
        )

    def test_a_transient_failure_backs_off_exponentially(
        self, db_session: Session, mesh
    ) -> None:
        now = utcnow()
        row = records.begin(db_session, mesh, THUMBNAIL, 1, now=now)
        row.attempts = 3

        records.mark_failed(db_session, row, "storage", now=now, deterministic=False)

        assert row.next_attempt_at == now + timedelta(
            seconds=settings.derivative_backoff_seconds * 4
        )

    def test_the_backoff_is_capped_at_a_day(self, db_session: Session, mesh) -> None:
        from app.core.config import _overlay

        _overlay["derivative_backoff_seconds"] = 86400
        now = utcnow()
        row = records.begin(db_session, mesh, THUMBNAIL, 1, now=now)
        row.attempts = 2

        records.mark_failed(db_session, row, "storage", now=now, deterministic=False)

        assert row.next_attempt_at == now + timedelta(days=1)

    def test_a_deterministic_failure_is_terminal_at_once(
        self, db_session: Session, mesh
    ) -> None:
        # Retrying bytes that cannot render only repeats the failure.
        row = records.begin(db_session, mesh, THUMBNAIL, 1, now=utcnow())

        records.mark_failed(
            db_session, row, "invalid_source", now=utcnow(), deterministic=True
        )

        assert row.attempts == settings.derivative_max_attempts
        assert row.next_attempt_at is None

    def test_a_lost_execution_fails_only_its_in_flight_rows(
        self, db_session: Session, mesh, make_derivative
    ) -> None:
        make_derivative(mesh, METADATA, state=DerivativeState.RUNNING)
        make_derivative(mesh, THUMBNAIL)

        assert records.fail_in_flight(db_session, mesh.id, "lost", now=utcnow()) == 1

        states = {
            row.kind: row.state
            for row in db_session.exec(select(ArtifactDerivative)).all()
        }
        assert states == {
            METADATA: DerivativeState.FAILED,
            THUMBNAIL: DerivativeState.READY,
        }


class TestWithdrawAndRetry:
    def test_cancelling_withdraws_every_unfinished_kind(
        self, db_session: Session, mesh, make_derivative
    ) -> None:
        make_derivative(mesh, METADATA)

        records.cancel(db_session, mesh, {METADATA: 1, THUMBNAIL: 1}, now=utcnow())
        db_session.commit()

        states = {
            row.kind: row.state for row in records.rows_for(db_session, mesh).values()
        }
        assert states == {
            METADATA: DerivativeState.READY,
            THUMBNAIL: DerivativeState.CANCELLED,
        }

    def test_a_retry_forgets_every_unsuccessful_attempt(
        self, db_session: Session, mesh, make_derivative
    ) -> None:
        make_derivative(mesh, METADATA)
        make_derivative(mesh, THUMBNAIL, state=DerivativeState.FAILED, exhausted=True)

        assert records.reset(db_session, mesh) == 1

        assert set(records.rows_for(db_session, mesh)) == {METADATA}

    def test_a_retry_can_target_one_kind(
        self, db_session: Session, mesh, make_derivative
    ) -> None:
        make_derivative(mesh, METADATA, state=DerivativeState.CANCELLED)
        make_derivative(mesh, THUMBNAIL, state=DerivativeState.CANCELLED)

        records.reset(db_session, mesh, {THUMBNAIL: 1})

        assert set(records.rows_for(db_session, mesh)) == {METADATA}

    def test_invalidating_forgets_a_kind_whatever_its_state(
        self, db_session: Session, mesh, make_derivative
    ) -> None:
        # An audit found the output broken even though its row says ready.
        make_derivative(mesh, THUMBNAIL)
        make_derivative(mesh, THUMBNAIL, recipe_version=0)

        assert records.invalidate(db_session, mesh, [THUMBNAIL, "toolpath"]) == 1

        remaining = db_session.exec(select(ArtifactDerivative)).all()
        assert [(row.kind, row.recipe_version) for row in remaining] == [(THUMBNAIL, 0)]


class TestRead:
    def test_reports_every_applicable_kind(
        self, db_session: Session, mesh, make_derivative
    ) -> None:
        make_derivative(
            mesh,
            THUMBNAIL,
            state=DerivativeState.FAILED,
            failure_reason="render_failed",
            exhausted=True,
        )

        reads = {read.kind: read for read in records.read(db_session, mesh)}

        assert reads[METADATA].state == "pending"
        assert (
            reads[THUMBNAIL].state,
            reads[THUMBNAIL].failure_reason,
            reads[THUMBNAIL].retryable,
        ) == ("failed", "render_failed", True)
