"""The engine-agnostic vocabulary: retry policies, lanes, definitions, priority.

These types are what definitions are written in, so a definition that cannot
work is refused when it is declared (at startup) rather than when a Job first
runs it.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.db.models import WorkPriority
from app.modules.work.contracts import (
    JobDefinition,
    Lane,
    RetryPolicy,
    Step,
    narrower_priority,
)


def _noop(_ctx) -> None:
    return None


class TestNarrowerPriority:
    @pytest.mark.parametrize(
        ("parent", "requested", "expected"),
        [
            (
                WorkPriority.INTERACTIVE,
                WorkPriority.INTERACTIVE,
                WorkPriority.INTERACTIVE,
            ),
            (WorkPriority.INTERACTIVE, WorkPriority.BACKFILL, WorkPriority.BACKFILL),
            (WorkPriority.BACKFILL, WorkPriority.INTERACTIVE, WorkPriority.BACKFILL),
            (WorkPriority.BACKFILL, WorkPriority.BACKFILL, WorkPriority.BACKFILL),
        ],
    )
    def test_a_child_may_lower_its_parents_priority_never_raise_it(
        self, parent, requested, expected
    ) -> None:
        assert narrower_priority(parent, requested) is expected


class TestRetryPolicy:
    def test_retries_only_the_errors_it_names(self) -> None:
        policy = RetryPolicy(max_attempts=3, retry_on=(TimeoutError,))

        assert policy.should_retry(TimeoutError()) is True
        assert policy.should_retry(ValueError()) is False

    def test_retries_nothing_when_it_names_nothing(self) -> None:
        assert RetryPolicy(max_attempts=5).should_retry(TimeoutError()) is False

    def test_backs_off_exponentially(self) -> None:
        policy = RetryPolicy(interval_seconds=2.0, backoff_rate=3.0)

        assert [policy.delay_before(n) for n in (1, 2, 3)] == [2.0, 6.0, 18.0]

    def test_refuses_fewer_than_one_attempt(self) -> None:
        with pytest.raises(ValueError, match="retry_max_attempts_below_one"):
            RetryPolicy(max_attempts=0)

    @pytest.mark.parametrize(
        ("interval", "backoff"), [(-1.0, 2.0), (1.0, 0.5)], ids=["interval", "backoff"]
    )
    def test_refuses_a_shrinking_or_negative_interval(
        self, interval: float, backoff: float
    ) -> None:
        with pytest.raises(ValueError, match="retry_interval_invalid"):
            RetryPolicy(interval_seconds=interval, backoff_rate=backoff)


class TestLane:
    def test_refuses_a_lane_that_can_run_nothing(self) -> None:
        with pytest.raises(ValueError, match="lane_concurrency_below_one"):
            Lane("empty", 0)

    def test_headroom_scales_with_concurrency(self) -> None:
        assert Lane("wide", 3).headroom == 3 * settings.jobs_lane_headroom_factor


class TestJobDefinition:
    def test_refuses_a_definition_without_steps(self) -> None:
        with pytest.raises(ValueError, match="job_definition_without_steps"):
            JobDefinition(name="empty", lane="ingest", steps=())

    def test_refuses_two_steps_with_one_name(self) -> None:
        # Step names are checkpoint keys: a duplicate would replay one step's
        # recorded result as the other's.
        with pytest.raises(ValueError, match="job_definition_duplicate_step"):
            JobDefinition(
                name="dup",
                lane="ingest",
                steps=(Step("same", _noop), Step("same", _noop)),
            )

    def test_every_hook_is_optional(self) -> None:
        definition = JobDefinition(
            name="bare", lane="ingest", steps=(Step("a", _noop),)
        )

        assert definition.retry(None, "subject") is True  # type: ignore[arg-type]
        assert definition.cancel(None, "subject") is None  # type: ignore[arg-type]
        assert definition.mutating is True
