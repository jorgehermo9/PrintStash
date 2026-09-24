"""``python -m app.worker``: what a worker checks before it runs any Job.

A worker only makes sense as a worker, on a topology that can share work (the
whole lifecycle runs for real in ``tests/e2e/test_split_topology.py``). It
never migrates: it waits for the API to bring the schema to this build's
head, tolerating a database that is still starting, and gives up with a clear
error rather than running against a schema it was not written for.
"""

from __future__ import annotations

import pytest

from app import worker
from app.core.config import _overlay


@pytest.fixture
def instant(monkeypatch) -> None:
    monkeypatch.setattr(worker, "_SCHEMA_WAIT_S", 0.0)
    monkeypatch.setattr(worker.time, "sleep", lambda _seconds: None)


def _answers(monkeypatch, *answers) -> list:
    remaining = list(answers)
    asked: list = []

    def current() -> bool:
        answer = remaining.pop(0)
        asked.append(answer)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(worker, "_schema_current", current)
    return asked


class TestWaitForSchema:
    def test_returns_once_the_api_has_migrated(self, monkeypatch, instant) -> None:
        asked = _answers(monkeypatch, False, False, True)

        worker.wait_for_schema(timeout=60)

        assert asked == [False, False, True]

    def test_tolerates_a_database_that_is_still_starting(
        self, monkeypatch, instant
    ) -> None:
        asked = _answers(monkeypatch, ConnectionError("refused"), True)

        worker.wait_for_schema(timeout=60)

        assert len(asked) == 2

    def test_gives_up_on_a_schema_that_never_arrives(
        self, monkeypatch, instant
    ) -> None:
        _answers(monkeypatch, False)

        with pytest.raises(RuntimeError, match="migration head"):
            worker.wait_for_schema(timeout=0)


class TestMain:
    def test_refuses_to_run_outside_the_worker_role(self) -> None:
        with pytest.raises(SystemExit, match="VAULT_PROCESS_ROLE=worker"):
            worker.main()

    def test_refuses_a_topology_before_waiting_for_anything(self, monkeypatch) -> None:
        # SQLite cannot be shared by processes; the refusal must come first,
        # not after ten minutes of waiting for a schema.
        _overlay["process_role"] = "worker"
        waited: list[bool] = []
        monkeypatch.setattr(worker, "wait_for_schema", lambda: waited.append(True))

        with pytest.raises(RuntimeError, match="requires PostgreSQL"):
            worker.main()

        assert waited == []
