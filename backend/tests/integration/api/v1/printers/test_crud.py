"""Registering a printer, editing it, and taking it out of the fleet.

A printer row is a set of credentials plus an address, and the two things that must hold
across every edit are that the **secret never comes back out** and that changing the
address does not silently keep talking to the old one. Both are easy to get wrong in a
partial update, which is why the update tests assert on what the API returns *and* on the
row.

Provider validation lives here too: each provider needs a different shape of connection
detail, and a printer accepted with the wrong shape is a printer that fails at the moment
someone tries to print.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db.models import (
    Printer,
    PrinterStatus,
)


class TestListPrinters:
    def test_list_empty(self, client: TestClient, auth_headers):
        resp = client.get("/api/v1/printers", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_with_printer(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.get("/api/v1/printers", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "Ender 3"
        assert data[0]["status"] == PrinterStatus.UNKNOWN.value


class TestCreatePrinter:
    def test_create_requires_auth(self, client: TestClient):
        resp = client.post(
            "/api/v1/printers",
            json={"name": "Ender 3", "moonraker_url": "http://10.0.0.1:7125"},
        )
        assert resp.status_code == 401

    def test_create_with_auth(self, client: TestClient, auth_headers):
        resp = client.post(
            "/api/v1/printers",
            json={
                "name": "Ender 3",
                "moonraker_url": "http://10.0.0.1:7125",
                "api_key": "secret",
                "notes": "Garage printer",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Ender 3"
        assert data["moonraker_url"] == "http://10.0.0.1:7125"
        assert data["has_api_key"] is True
        assert data["notes"] == "Garage printer"
        assert data["status"] == PrinterStatus.UNKNOWN.value

    def test_create_strips_trailing_slashes(self, client: TestClient, auth_headers):
        resp = client.post(
            "/api/v1/printers",
            json={"name": "Prusa", "moonraker_url": "http://10.0.0.2:7125/"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["moonraker_url"] == "http://10.0.0.2:7125"

    def test_create_detects_neptune4_model(self, client: TestClient, auth_headers):
        resp = client.post(
            "/api/v1/printers",
            json={
                "name": "Neptune",
                "moonraker_url": "http://10.0.0.3:7125",
                "provider_variant": "elegoo_neptune4",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["detected_model"] == "Elegoo Neptune 4 family"
        assert data["model_name"] is None

    def test_create_with_manual_model_name(self, client: TestClient, auth_headers):
        resp = client.post(
            "/api/v1/printers",
            json={
                "name": "Voron",
                "moonraker_url": "http://10.0.0.4:7125",
                "model_name": "Voron 2.4",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["model_name"] == "Voron 2.4"
        assert data["detected_model"] is None


class TestGetPrinter:
    def test_get_returns_printer(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.get(f"/api/v1/printers/{p.id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["name"] == "Ender 3"

    def test_get_404(self, client: TestClient, auth_headers):
        resp = client.get("/api/v1/printers/99999", headers=auth_headers)
        assert resp.status_code == 404
        assert resp.json()["detail"] == "printer_not_found"


class TestUpdatePrinter:
    def test_update_requires_auth(self, client: TestClient, db_session: Session):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.patch(f"/api/v1/printers/{p.id}", json={"name": "Ender 3 Pro"})
        assert resp.status_code == 401

    def test_update_name(self, client: TestClient, auth_headers, db_session: Session):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.patch(
            f"/api/v1/printers/{p.id}",
            json={"name": "Ender 3 Pro"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Ender 3 Pro"

    def test_update_404(self, client: TestClient, auth_headers):
        resp = client.patch(
            "/api/v1/printers/99999",
            json={"name": "Nope"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_update_manual_model_name_overrides_display(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(
            name="Neptune",
            moonraker_url="http://10.0.0.1:7125",
            provider_variant="elegoo_neptune4",
            detected_model="Elegoo Neptune 4 family",
        )
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.patch(
            f"/api/v1/printers/{p.id}",
            json={"model_name": "Neptune 4 Pro"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["model_name"] == "Neptune 4 Pro"
        assert data["detected_model"] == "Elegoo Neptune 4 family"

    @pytest.mark.parametrize(
        "payload,expected_detail",
        [
            ({"moonraker_url": ""}, "moonraker_url_required"),
            ({"provider": "bambu_lan"}, "bambu_host_required"),
            (
                {"provider": "bambu_lan", "bambu_host": "h"},
                "bambu_serial_required",
            ),
            (
                {"provider": "bambu_lan", "bambu_host": "h", "bambu_serial": "s"},
                "bambu_access_code_required",
            ),
            ({"provider": "prusalink"}, "prusalink_url_required"),
            (
                {"provider": "prusalink", "prusalink_url": "http://p"},
                "prusalink_auth_mode_required",
            ),
            (
                {
                    "provider": "prusalink",
                    "prusalink_url": "http://p",
                    "prusalink_auth_mode": "digest",
                    "prusalink_username": "u",
                },
                "prusalink_digest_credentials_required",
            ),
            (
                {
                    "provider": "prusalink",
                    "prusalink_url": "http://p",
                    "prusalink_auth_mode": "api_key",
                },
                "prusalink_api_key_required",
            ),
            ({"provider": "elegoo_centauri"}, "elegoo_centauri_model_required"),
            (
                {
                    "provider": "elegoo_centauri",
                    "provider_variant": "elegoo_centauri_carbon",
                },
                "elegoo_centauri_host_required",
            ),
            (
                {
                    "provider": "elegoo_centauri",
                    "provider_variant": "elegoo_centauri_carbon_2",
                    "elegoo_centauri_host": "h",
                },
                "elegoo_centauri_access_code_required",
            ),
            ({"provider": "octoprint"}, "octoprint_url_required"),
            (
                {"provider": "octoprint", "octoprint_url": "http://o"},
                "octoprint_api_key_required",
            ),
        ],
    )
    def test_update_provider_validation_errors(
        self,
        client: TestClient,
        auth_headers,
        db_session: Session,
        payload,
        expected_detail,
    ):
        p = Printer(name="X", moonraker_url="http://x.local:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.patch(
            f"/api/v1/printers/{p.id}", json=payload, headers=auth_headers
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == expected_detail

    def test_update_sets_all_optional_fields(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(name="X", moonraker_url="http://x.local:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        payload = {
            "provider": "moonraker",
            "name": "Renamed",
            "moonraker_url": "http://renamed.local:7125",
            "api_key": "key1",
            "provider_variant": "generic",
            "bambu_host": "1.2.3.4",
            "bambu_serial": "SN1",
            "bambu_access_code": "code1",
            "prusalink_url": "http://prusa.local",
            "prusalink_auth_mode": "digest",
            "prusalink_username": "user1",
            "prusalink_password": "pass1",
            "prusalink_api_key": "key2",
            "elegoo_centauri_host": "5.6.7.8",
            "elegoo_centauri_access_code": "code2",
            "elegoo_centauri_mainboard_id": "board1",
            "octoprint_url": "http://octo.local",
            "octoprint_api_key": "key3",
            "model_name": "Model X",
            "notes": "some notes",
            "group": "lab",
        }
        resp = client.patch(
            f"/api/v1/printers/{p.id}", json=payload, headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Renamed"
        assert data["moonraker_url"] == "http://renamed.local:7125"
        assert data["has_api_key"] is True
        assert data["bambu_host"] == "1.2.3.4"
        assert data["bambu_serial"] == "SN1"
        assert data["prusalink_url"] == "http://prusa.local"
        assert data["prusalink_username"] == "user1"
        assert data["has_prusalink_password"] is True
        assert data["has_prusalink_api_key"] is True
        assert data["elegoo_centauri_host"] == "5.6.7.8"
        assert data["elegoo_centauri_mainboard_id"] == "board1"
        assert data["octoprint_url"] == "http://octo.local"
        assert data["has_octoprint_api_key"] is True
        assert data["model_name"] == "Model X"
        assert data["notes"] == "some notes"
        assert data["group"] == "lab"


class TestDeletePrinter:
    def test_delete_requires_auth(self, client: TestClient, db_session: Session):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.delete(f"/api/v1/printers/{p.id}")
        assert resp.status_code == 401

    def test_delete_removes_printer(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.delete(f"/api/v1/printers/{p.id}", headers=auth_headers)
        assert resp.status_code == 204

        resp2 = client.get(f"/api/v1/printers/{p.id}", headers=auth_headers)
        assert resp2.status_code == 404

    def test_delete_404(self, client: TestClient, auth_headers):
        resp = client.delete("/api/v1/printers/99999", headers=auth_headers)
        assert resp.status_code == 404


class TestGroupFilter:
    def test_filter_by_group(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p1 = Printer(name="Prusa", moonraker_url="http://10.0.0.1:7125", group="garage")
        p2 = Printer(
            name="Ender", moonraker_url="http://10.0.0.2:7125", group="workshop"
        )
        db_session.add_all([p1, p2])
        db_session.commit()

        resp = client.get("/api/v1/printers", headers=auth_headers)
        assert len(resp.json()) == 2

        resp = client.get("/api/v1/printers?group=garage", headers=auth_headers)
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "Prusa"
        assert data[0]["group"] == "garage"

        resp = client.get("/api/v1/printers?group=workshop", headers=auth_headers)
        assert len(resp.json()) == 1

    def test_create_with_group(self, client: TestClient, auth_headers):
        resp = client.post(
            "/api/v1/printers",
            json={
                "name": "Garage Printer",
                "moonraker_url": "http://10.0.0.1:7125",
                "group": "garage",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["group"] == "garage"

    def test_update_group(self, client: TestClient, auth_headers, db_session: Session):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.patch(
            f"/api/v1/printers/{p.id}",
            json={"group": "workshop"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["group"] == "workshop"
