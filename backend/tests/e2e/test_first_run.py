"""E2E: an installation whose first administrator comes from the environment.

App-store forms (Unraid, Runtipi, CasaOS) and unattended deployments set
``VAULT_SETUP_ADMIN_*`` instead of opening the browser wizard. This drives what an
operator does after that install: startup provisions the owner, the owner signs in,
the server asks for the storage choice nobody has made yet, and after choosing it
the first upload lands in the library. If any step regresses, the installation is
either stuck without storage or has storage no one chose.
"""

from __future__ import annotations

import asyncio

import pytest

from app.modules.administration.setup_bootstrap import provision_from_environment
from tests.paths import FIXTURES_DIR

USERNAME = "store-owner"
PASSWORD = "StoreFormPassword123"
FIXTURE = FIXTURES_DIR / "real_orca_ender3_benchy.gcode"


async def _await_job(api, headers, job_id: str) -> dict:
    for _ in range(50):
        response = await api.get(f"/api/v1/ingest/jobs/{job_id}", headers=headers)
        assert response.status_code == 200, response.text
        job = response.json()
        if job["state"] in ("completed", "failed", "duplicate"):
            return job
        await asyncio.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish: {job}")


class TestEnvironmentAdministrator:
    @pytest.mark.critical
    @pytest.mark.asyncio
    async def test_environment_administrator_reaches_a_first_model(
        self, api, e2e_db, environment_admin
    ) -> None:
        # ── Startup provisions the owner (the lifespan is not run over ASGI) ──
        environment_admin(USERNAME, PASSWORD)
        assert provision_from_environment(e2e_db) is not None
        status = (await api.get("/api/v1/setup/status")).json()
        assert status["storage_choice_required"] is True, status

        # ── The owner signs in with the provisioned credentials ──
        login = await api.post(
            "/api/v1/auth/login", json={"username": USERNAME, "password": PASSWORD}
        )
        assert login.status_code == 200, login.text
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        # ── Nothing was chosen, so the server asks rather than guessing ──
        retry = await api.post("/api/v1/setup/prepare-storage", headers=headers)
        assert retry.json()["detail"] == "setup_storage_choice_required", retry.text

        # ── The owner chooses local storage ──
        chosen = await api.post(
            "/api/v1/setup/prepare-storage",
            json={"storage_backend": "local"},
            headers=headers,
        )
        assert chosen.status_code == 200, chosen.text
        status = (await api.get("/api/v1/setup/status")).json()
        assert status["storage_choice_required"] is False, status

        # ── The first upload lands in the library ──
        uploaded = await api.post(
            "/api/v1/ingest/orca",
            files={"file": (FIXTURE.name, FIXTURE.read_bytes(), "text/plain")},
            data={"model_name": "First model"},
            headers=headers,
        )
        assert uploaded.status_code == 202, uploaded.text
        job = await _await_job(api, headers, uploaded.json()["job_id"])
        assert job["state"] == "completed", job
        models = (await api.get("/api/v1/models", headers=headers)).json()
        assert [model["name"] for model in models] == ["First model"]
