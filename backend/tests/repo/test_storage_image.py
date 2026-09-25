"""The release wheel and native image jobs cover every advertised transport.

Development wheels cannot prove which services a custom release wheel compiled.
These build contracts keep both backend image variants on both architectures.
"""

from __future__ import annotations

import re

import yaml

from tests.paths import BACKEND_DIR, REPO_ROOT


class TestStorageImage:
    def test_builds_the_required_s3_service(self) -> None:
        dockerfile = (BACKEND_DIR / "Dockerfile").read_text()

        feature_line = re.search(r"--features\s+([^\s]+)", dockerfile)

        assert feature_line is not None
        assert "services-s3" in feature_line.group(1).split(",")

    def test_checks_each_backend_image_on_its_native_architecture(self) -> None:
        workflow = yaml.safe_load(
            (REPO_ROOT / ".github/workflows/container-publish.yml").read_text()
        )
        job = workflow["jobs"]["build"]
        images = [
            row
            for row in job["strategy"]["matrix"]["include"]
            if row["image"].startswith("printstash-api")
        ]

        assert {(row["image"], row["arch"]) for row in images} == {
            ("printstash-api", "amd64"),
            ("printstash-api", "arm64"),
            ("printstash-api-lite", "amd64"),
            ("printstash-api-lite", "arm64"),
        }
        assert all(
            row["platform"] == f"linux/{row['arch']}"
            and row["runner"]
            == ("ubuntu-latest" if row["arch"] == "amd64" else "ubuntu-24.04-arm")
            for row in images
        )
        steps = job["steps"]
        assert any(
            step.get("uses", "").startswith("docker/build-push-action@")
            for step in steps
        )
        smoke = next(
            step
            for step in steps
            if step.get("name") == "Test backend image before exporting digest"
        )
        assert smoke["if"] == "startsWith(matrix.image, 'printstash-api')"
        assert smoke["env"]["VARIANT"] == (
            "${{ matrix.image == 'printstash-api' && 'full' || 'lite' }}"
        )
        assert smoke["env"]["DIGEST"] == "${{ steps.build.outputs.digest }}"
        assert smoke["run"].index('docker pull "$image"') < smoke["run"].index(
            "./scripts/test.sh image"
        )
        assert '--image "$image" --variant "$VARIANT"' in smoke["run"]
        assert steps.index(smoke) < next(
            index
            for index, step in enumerate(steps)
            if step.get("name") == "Export digest"
        )
        assert any(
            "test-unified-image.sh" in step.get("run", "") for step in steps
        )
