"""Background work survives the process that was running it, on real DBOS.

Every other E2E flow runs the inline engine so it can assert settled state
deterministically. These tests boot the production composition in child
processes, on the durable engine, and kill one mid-step:

- a process dies while a derivative is running; a restarted process finds the
  execution stranded on a dead executor, interrupts it, and runs it again to
  completion;
- a process of the next application version starts on a vault whose previous
  version died mid-step; it cancels the old version's execution immediately
  and reruns the work on its own code.

Both run on SQLite and on PostgreSQL, the two supported databases, because the
engine keeps its state in a different place on each (a sibling file, or the
``dbos`` schema of the application database).
"""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.e2e._processes import fresh_postgres_database, vault_environment
from tests.paths import BACKEND_DIR

_ROLE = "tests.fakes.job_engine_process"


@pytest.fixture(params=["sqlite", "postgresql"])
def vault_env(request, tmp_path: Path) -> dict[str, str]:
    db_url = (
        fresh_postgres_database("job_engine")
        if request.param == "postgresql"
        else f"sqlite:///{tmp_path / 'vault.sqlite'}"
    )
    return vault_environment(tmp_path, db_url)


def _stall(environment: dict[str, str], marker: Path) -> tuple[subprocess.Popen, int]:
    process = subprocess.Popen(
        [sys.executable, "-m", _ROLE, "stall", str(marker)],
        cwd=BACKEND_DIR,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    deadline = time.monotonic() + 120
    for line in process.stdout:
        if line.startswith("{"):
            return process, json.loads(line)["file_id"]
        assert time.monotonic() < deadline
    _, stderr = process.communicate(timeout=10)
    raise AssertionError(f"stalling process exited early:\n{stderr}")


def _kill(process: subprocess.Popen) -> None:
    # SIGKILL: no shutdown hook runs, exactly like a crash or an OOM kill.
    process.send_signal(signal.SIGKILL)
    process.wait(timeout=30)


def _converge(environment: dict[str, str], file_id: int) -> dict[str, str]:
    result = subprocess.run(
        [sys.executable, "-m", _ROLE, "converge", str(file_id)],
        cwd=BACKEND_DIR,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
    assert lines, result.stdout + result.stderr
    outcome = json.loads(lines[-1])
    assert set(outcome["states"].values()) == {"ready"}, (
        outcome,
        (result.stdout + result.stderr)[-6000:],
    )
    return outcome["states"]


class TestCrashRecovery:
    @pytest.mark.critical
    def test_work_on_a_dead_executor_is_rerun_by_a_restarted_process(
        self, vault_env: dict[str, str], tmp_path: Path
    ) -> None:
        process, file_id = _stall(vault_env, tmp_path / "running")
        _kill(process)

        states = _converge(vault_env, file_id)

        assert states == {"metadata": "ready", "thumbnail": "ready"}

    def test_a_new_version_reruns_what_the_old_one_left_running(
        self, vault_env: dict[str, str], tmp_path: Path
    ) -> None:
        # An upgrade does not wait for the old executor to go stale: work of
        # another version is cancelled at startup and rerun on the new code.
        old = {**vault_env, "VAULT_APP_VERSION": "0.0.1-old"}
        process, file_id = _stall(old, tmp_path / "running")
        _kill(process)
        new = {
            **vault_env,
            "VAULT_APP_VERSION": "0.0.2-new",
            # Long enough that only the version sweep can explain recovery.
            "VAULT_JOBS_EXECUTOR_STALE_SECONDS": "3600",
            "JOB_ENGINE_DEADLINE_S": "60",
        }

        states = _converge(new, file_id)

        assert states == {"metadata": "ready", "thumbnail": "ready"}
