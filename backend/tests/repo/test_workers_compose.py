"""The advanced Compose file's optional workers: a split deployment, opted into.

Without the profile the advanced file is one API that runs its own background
work, like the default file. ``--profile workers`` adds worker containers that
share the API's settings and volumes exactly, because what the application
refuses at startup (``validate_topology``: PostgreSQL, one set of volumes
every process mounts, that sharing declared) must be something the file can
express by setting the documented variables, never by editing two services.
A worker never serves HTTP or migrates.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

import pytest

from tests.paths import REPO_ROOT

COMPOSE = REPO_ROOT / "docker-compose.advanced.yml"
SPLIT = {
    "VAULT_PROCESS_ROLE": "api",
    "VAULT_API_RUNS_JOBS": "false",
    "VAULT_SHARED_STORAGE": "true",
}


def _render(*profiles: str, **environment: str) -> dict[str, Any]:
    arguments = [
        "docker",
        "compose",
        "--env-file",
        "/dev/null",
        "-f",
        str(COMPOSE),
    ]
    for profile in profiles:
        arguments += ["--profile", profile]
    rendered = subprocess.run(
        [*arguments, "config", "--format", "json"],
        env={"PATH": os.environ["PATH"], **environment},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert rendered.returncode == 0, rendered.stderr
    return json.loads(rendered.stdout)


@pytest.fixture(scope="module")
def split() -> dict[str, Any]:
    return _render("postgres", "workers", **SPLIT)["services"]


def _mounts(service: dict[str, Any]) -> set[tuple[str, str]]:
    return {(volume["source"], volume["target"]) for volume in service["volumes"]}


class TestWithoutWorkers:
    def test_the_api_runs_every_job_itself(self) -> None:
        environment = _render()["services"]["api"]["environment"]

        assert (
            environment["VAULT_PROCESS_ROLE"],
            environment["VAULT_API_RUNS_JOBS"],
        ) == ("all", "true")

    def test_no_worker_starts(self) -> None:
        assert "worker" not in _render()["services"]


class TestWithWorkers:
    def test_runs_workers_as_worker_processes(self, split: dict[str, Any]) -> None:
        worker = split["worker"]

        assert worker["environment"]["VAULT_PROCESS_ROLE"] == "worker"
        assert worker["command"][-2:] == ["-m", "app.worker"]
        assert "ports" not in worker

    def test_runs_two_workers_by_default(self, split: dict[str, Any]) -> None:
        assert split["worker"]["deploy"]["replicas"] == 2

    def test_accepts_a_worker_count(self) -> None:
        services = _render("workers", PRINTSTASH_WORKERS="4")["services"]

        assert services["worker"]["deploy"]["replicas"] == 4

    def test_the_api_takes_the_documented_split_settings(
        self, split: dict[str, Any]
    ) -> None:
        environment = split["api"]["environment"]

        assert {key: environment[key] for key in SPLIT} == SPLIT

    def test_a_worker_reads_every_api_setting(self, split: dict[str, Any]) -> None:
        # One database URL, one set of paths and secrets: a worker configured
        # apart from its API would run work against another vault.
        api = dict(split["api"]["environment"])
        worker = dict(split["worker"]["environment"])
        api.pop("VAULT_PROCESS_ROLE")
        worker.pop("VAULT_PROCESS_ROLE")

        assert worker == api

    def test_every_process_mounts_the_same_volumes(self, split: dict[str, Any]) -> None:
        # A worker committing an upload reads the bytes the API staged, which
        # live with everything else under the one data volume.
        api = _mounts(split["api"])

        assert api == _mounts(split["worker"])
        assert any(target == "/data" for _, target in api)

    def test_a_worker_starts_after_the_api_migrated(
        self, split: dict[str, Any]
    ) -> None:
        assert split["worker"]["depends_on"]["api"]["condition"] == "service_healthy"
