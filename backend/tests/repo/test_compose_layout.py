"""The Compose files a self-hoster can pick from, and where each one lives.

The repository root carries exactly two: `docker-compose.yml`, the short file a
newcomer downloads and runs with no configuration, and
`docker-compose.advanced.yml`, which wires every documented setting and doubles
as the reference. Maintainer stacks live under `deploy/`. A third root file is
one more choice for someone who only wanted to start the app, so adding one is a
decision this test makes visible rather than something that accumulates.

Moving the maintainer stacks under `deploy/` changed what their relative paths
resolve against, and the docs quote these paths in copy-paste commands; both are
checked here because neither fails anywhere else until someone runs them.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.paths import REPO_ROOT

ADVANCED = REPO_ROOT / "docker-compose.advanced.yml"
MANUAL = REPO_ROOT / "deploy/manual-testing/compose.yml"
MINIO = REPO_ROOT / "deploy/minio-migration/compose.yml"
LOCAL_VOLUMES = {
    "printstash_data",
    "printstash_thumbs",
    "printstash_db",
    "printstash_staging",
    "printstash_backups",
}


def _render(*args: str, **environment: str) -> dict[str, Any]:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            *args,
            "config",
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        env={"PATH": os.environ["PATH"], **environment},
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return json.loads(result.stdout)


def _documented_api_settings() -> set[str]:
    guide = (REPO_ROOT / "docs/deployment.md").read_text()
    section = guide.split("## API settings reference", 1)[1].split("\n## ", 1)[0]
    return set(re.findall(r"^\| `(VAULT_[A-Z0-9_]+)` \|", section, re.MULTILINE))


class TestRootLayout:
    def test_root_offers_exactly_two_compose_files(self) -> None:
        compose_files = {
            path.name
            for pattern in (
                "docker-compose*.yml",
                "docker-compose*.yaml",
                "compose*.y*ml",
            )
            for path in REPO_ROOT.glob(pattern)
        }

        assert compose_files == {"docker-compose.yml", "docker-compose.advanced.yml"}

    def test_both_files_keep_the_same_local_volumes(self) -> None:
        default = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
        advanced = yaml.safe_load(ADVANCED.read_text())

        assert LOCAL_VOLUMES <= set(default["volumes"])
        assert LOCAL_VOLUMES <= set(advanced["volumes"])

    @pytest.mark.parametrize(
        "document",
        sorted(
            str(path.relative_to(REPO_ROOT))
            for pattern in (
                "*.md",
                "docs/**/*.md",
                "deploy/**/*.md",
                "unraid/*.md",
                "backend/README.md",
                ".env.example",
                "scripts/*.sh",
                ".agents/skills/printstash/**/*.md",
            )
            for path in REPO_ROOT.glob(pattern)
            # Both name retired files on purpose, to tell upgraders what replaced them.
            if path.name not in {"CHANGELOG.md", "UPGRADE.md"}
        ),
    )
    def test_documented_compose_paths_exist(self, document: str) -> None:
        text = (REPO_ROOT / document).read_text()
        referenced = set(re.findall(r"docker-compose[\w.-]*\.yml", text)) - {
            "docker-compose.override.yml"
        }
        referenced |= set(re.findall(r"deploy/[\w-]+/compose\.yml", text))

        assert {
            name for name in referenced if not (REPO_ROOT / name).is_file()
        } == set()


class TestAdvancedCompose:
    def test_wires_every_documented_api_setting(self) -> None:
        environment = yaml.safe_load(ADVANCED.read_text())["services"]["api"][
            "environment"
        ]

        assert _documented_api_settings() - set(environment) == set()

    def test_runs_only_the_app_without_profiles(self) -> None:
        config = _render("-f", str(ADVANCED))

        assert set(config["services"]) == {"frontend", "api"}

    def test_profiles_add_postgres_with_seaweedfs(self) -> None:
        config = _render(
            "-f", str(ADVANCED), "--profile", "postgres", "--profile", "s3"
        )

        assert set(config["services"]) == {"frontend", "api", "postgres", "seaweedfs"}

    def test_rotates_logs_for_every_service(self) -> None:
        services = yaml.safe_load(ADVANCED.read_text())["services"]

        assert {
            name: service.get("logging", {}).get("options")
            for name, service in services.items()
        } == {name: {"max-size": "10m", "max-file": "3"} for name in services}

    def test_pulls_prebuilt_images_by_default(self) -> None:
        services = _render("-f", str(ADVANCED))["services"]

        assert {name: "build" in service for name, service in services.items()} == {
            "frontend": False,
            "api": False,
        }

    def test_accepts_optional_web_port(self) -> None:
        config = _render("-f", str(ADVANCED), PRINTSTASH_HTTP_PORT="8080")

        assert [
            (p["published"], p["target"])
            for p in config["services"]["frontend"]["ports"]
        ] == [("8080", 3000)]


class TestRelocatedStacks:
    def test_manual_stack_builds_from_the_checkout(self) -> None:
        services = _render(
            "-p",
            "printstash-manual",
            "-f",
            str(MANUAL),
            "--profile",
            "emulators",
        )["services"]

        assert {
            name: Path(service["build"]["context"])
            for name, service in services.items()
            if "build" in service
        } == {
            "frontend": REPO_ROOT / "frontend",
            "api": REPO_ROOT / "backend",
            "mock-moonraker": REPO_ROOT,
            "mock-octoprint": REPO_ROOT,
            "mock-prusalink": REPO_ROOT,
        }

    def test_manual_stack_mounts_checkout_fixtures(self) -> None:
        services = _render(
            "-p", "printstash-manual", "-f", str(MANUAL), "--profile", "identity"
        )["services"]
        binds = [
            Path(mount["source"])
            for name in ("api", "authentik-worker")
            for mount in services[name]["volumes"]
            if mount["type"] == "bind"
        ]

        assert [path for path in binds if not path.exists()] == []

    def test_minio_overlay_extends_the_advanced_stack(self) -> None:
        config = _render(
            "--project-directory",
            str(REPO_ROOT),
            "-f",
            str(ADVANCED),
            "-f",
            str(MINIO),
            "--profile",
            "s3",
            "--profile",
            "minio-migration",
        )
        helper = config["services"]["minio-migrate"]

        assert set(helper["depends_on"]) == {"minio", "seaweedfs"}
        assert all(Path(mount["source"]).is_file() for mount in helper["volumes"])
