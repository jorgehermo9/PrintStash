"""The split deployment: an API that runs no jobs, plus worker replicas.

`docker-compose.workers.yml` is the only stack where several processes share a
vault, so what the application refuses at startup (``validate_topology``) must
already be true of the file: PostgreSQL, one set of volumes every process
mounts at the same paths, and that sharing declared. The worker must never run
HTTP or migrate, and the API must not also run jobs, or the stack is a
different topology than the one it documents.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

import pytest

from tests.paths import REPO_ROOT

COMPOSE = REPO_ROOT / "docker-compose.workers.yml"
REQUIRED = {"POSTGRES_PASSWORD": "compose-test", "VAULT_JWT_SECRET": "compose-test"}


def _render(**environment: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "-f",
            str(COMPOSE),
            "config",
            "--format",
            "json",
        ],
        env={"PATH": os.environ["PATH"], **environment},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


@pytest.fixture(scope="module")
def config() -> dict[str, Any]:
    rendered = _render(**REQUIRED)
    assert rendered.returncode == 0, rendered.stderr
    return json.loads(rendered.stdout)


def _mounts(service: dict[str, Any]) -> set[tuple[str, str]]:
    return {(volume["source"], volume["target"]) for volume in service["volumes"]}


class TestWorkersCompose:
    def test_runs_an_api_that_runs_no_jobs(self, config: dict[str, Any]) -> None:
        environment = config["services"]["api"]["environment"]

        assert (
            environment["VAULT_PROCESS_ROLE"],
            environment["VAULT_API_RUNS_JOBS"],
        ) == (
            "api",
            "false",
        )

    def test_runs_workers_as_worker_processes(self, config: dict[str, Any]) -> None:
        worker = config["services"]["worker"]

        assert worker["environment"]["VAULT_PROCESS_ROLE"] == "worker"
        assert worker["command"][-2:] == ["-m", "app.worker"]
        assert "ports" not in worker

    def test_runs_more_than_one_worker(self, config: dict[str, Any]) -> None:
        assert config["services"]["worker"]["deploy"]["replicas"] >= 2

    @pytest.mark.parametrize("service", ["api", "worker"])
    def test_every_process_uses_postgres(
        self, config: dict[str, Any], service: str
    ) -> None:
        url = config["services"][service]["environment"]["VAULT_DB_URL"]

        assert url.startswith("postgresql://") and "@postgres:5432/" in url

    @pytest.mark.parametrize("service", ["api", "worker"])
    def test_every_process_declares_the_shared_volume(
        self, config: dict[str, Any], service: str
    ) -> None:
        assert (
            config["services"][service]["environment"]["VAULT_SHARED_STORAGE"] == "true"
        )

    def test_every_process_mounts_the_same_volumes(
        self, config: dict[str, Any]
    ) -> None:
        # A worker committing an upload reads the bytes the API staged.
        api = _mounts(config["services"]["api"])

        assert api == _mounts(config["services"]["worker"])
        assert any(target == "/data/staging" for _, target in api)

    def test_the_processes_agree_on_every_path(self, config: dict[str, Any]) -> None:
        paths = (
            "VAULT_DATA_DIR",
            "VAULT_THUMB_DIR",
            "VAULT_STAGING_DIR",
            "VAULT_BACKUP_DIR",
        )
        api = config["services"]["api"]["environment"]
        worker = config["services"]["worker"]["environment"]

        assert {key: api[key] for key in paths} == {key: worker[key] for key in paths}

    @pytest.mark.parametrize("missing", sorted(REQUIRED))
    def test_refuses_to_render_without_a_secret(self, missing: str) -> None:
        environment = {key: value for key, value in REQUIRED.items() if key != missing}

        assert _render(**environment).returncode != 0
