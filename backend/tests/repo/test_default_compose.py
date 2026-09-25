"""The default deployment works without a checkout or environment file.

`docker-compose.yml` is what the README tells a newcomer to download: one
container, no `.env`. Removing optional settings must preserve persistent state
and settings-driven restarts, and every fragment the deployment guide tells users
to merge into it must still produce that same stack. The image, port and volume
targets are covered by `test_unified_container.py::TestUnifiedCompose`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.paths import REPO_ROOT


@pytest.fixture
def compose_dir(tmp_path: Path) -> Path:
    (tmp_path / "docker-compose.yml").write_text(
        (REPO_ROOT / "docker-compose.yml").read_text()
    )
    return tmp_path


def _render(directory: Path, **environment: str) -> dict[str, Any]:
    result = subprocess.run(
        ["docker", "compose", "--env-file", "/dev/null", "config", "--format", "json"],
        cwd=directory,
        env={"PATH": os.environ["PATH"], **environment},
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return json.loads(result.stdout)


def _documented_fragments() -> list[str]:
    """Every Compose fragment the deployment guide tells users to merge in."""
    return re.findall(
        r"```yaml\n(services:\n.*?)```",
        (REPO_ROOT / "docs/deployment.md").read_text(),
        re.DOTALL,
    )


@pytest.fixture
def default_config(compose_dir: Path) -> dict[str, Any]:
    return _render(compose_dir)


class TestDefaultCompose:
    def test_needs_no_environment_file(self, default_config: dict[str, Any]) -> None:
        assert not default_config["services"]["printstash"].get("env_file")

    def test_uses_the_prebuilt_image(self, default_config: dict[str, Any]) -> None:
        service = default_config["services"]["printstash"]

        assert "build" not in service
        assert not service.get("entrypoint")
        assert not service.get("command")

    def test_persists_application_state_in_one_volume(
        self, default_config: dict[str, Any]
    ) -> None:
        # One mount is what lets an import hard-link its staged file into the
        # library: link(2) fails across mount points, even on the same disk.
        mounts = default_config["services"]["printstash"]["volumes"]

        assert [(m["type"], m["source"], m["target"]) for m in mounts] == [
            ("volume", "printstash", "/data")
        ]

    def test_declares_the_data_volume(self, default_config: dict[str, Any]) -> None:
        assert "printstash" in default_config["volumes"]

    def test_documented_host_folder_replaces_the_volume_with_one_mount(
        self, compose_dir: Path
    ) -> None:
        fragment = next(
            fragment for fragment in _documented_fragments() if "PUID" in fragment
        )
        (compose_dir / "docker-compose.override.yml").write_text(fragment)

        mounts = _render(compose_dir)["services"]["printstash"]["volumes"]

        assert [(m["type"], m["target"]) for m in mounts] == [("bind", "/data")]

    def test_gives_both_processes_time_to_stop(
        self, default_config: dict[str, Any]
    ) -> None:
        assert default_config["services"]["printstash"]["stop_grace_period"] == "1m0s"

    def test_ignores_unwired_host_database_setting(self, compose_dir: Path) -> None:
        config = _render(compose_dir, VAULT_DB_URL="sqlite:///./ephemeral.sqlite")

        assert "VAULT_DB_URL" not in config["services"]["printstash"]["environment"]

    @pytest.mark.parametrize(
        "fragment",
        _documented_fragments(),
        ids=["session-lifetime", "setup-host", "host-folder", "upload-limit"],
    )
    def test_documented_overrides_preserve_startup(
        self, compose_dir: Path, fragment: str
    ) -> None:
        (compose_dir / "docker-compose.override.yml").write_text(fragment)

        config = _render(
            compose_dir, VAULT_SETUP_ALLOWED_HOSTS="printstash.example.net"
        )
        service = config["services"]["printstash"]

        assert set(config["services"]) == {"printstash"}
        assert service["environment"]["VAULT_RESTART_ENABLED"] == "true"
        assert service["restart"] == "unless-stopped"


class TestBrowserRegistrationMode:
    def test_default_compose_enables_browser_registration_on_a_trusted_network(
        self,
        default_config,
    ):
        assert (
            default_config["services"]["printstash"]["environment"]["VAULT_SETUP_MODE"]
            == "trusted_network"
        )
