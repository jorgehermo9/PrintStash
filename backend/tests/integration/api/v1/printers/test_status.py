"""What a printer is doing right now, and what it has done.

Status is the most-polled surface in the app — every dashboard card refreshes it — so its
failure modes are about *degradation*, not correctness. A printer that cannot be reached
must produce a row that says so, not an error that blanks the dashboard, and a diagnostics
call must report the specific reason (no address, wrong credentials, unreachable) rather
than a generic failure an operator cannot act on.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.models import (
    Collection,
    CollectionPermission,
    CollectionRole,
    File,
    FileType,
    Model,
    Printer,
    PrintJob,
    PrintJobState,
    User,
)
from app.services.printer_provider import ProviderError
from tests.integration.api.v1.printers._helpers import user_headers


class TestPrinterStatus:
    def test_status_returns_printer_and_snapshot(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.get(f"/api/v1/printers/{p.id}/status", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["printer"]["name"] == "Ender 3"
        assert data["snapshot"] == {}

    def test_status_404(self, client: TestClient, auth_headers):
        resp = client.get("/api/v1/printers/99999/status", headers=auth_headers)
        assert resp.status_code == 404


class TestPrinterJobs:
    def test_jobs_empty(self, client: TestClient, auth_headers, db_session: Session):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.get(f"/api/v1/printers/{p.id}/jobs", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_jobs_lists_in_order(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        from app.db.models import File, Model

        m = Model(name="Model", slug="model", hash="i" * 64)
        db_session.add(m)
        db_session.commit()
        db_session.refresh(m)

        f = File(
            model_id=m.id,
            path="/data/model.gcode",
            original_filename="model.gcode",
            file_type="gcode",
            version=1,
            size_bytes=100,
            sha256="j" * 64,
        )
        db_session.add(f)
        db_session.commit()
        db_session.refresh(f)

        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        job = PrintJob(
            printer_id=p.id,
            file_id=f.id,
            model_id=m.id,
            remote_filename="model.gcode",
            state=PrintJobState.COMPLETED,
            progress=1.0,
        )
        db_session.add(job)
        db_session.commit()

        resp = client.get(f"/api/v1/printers/{p.id}/jobs", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["state"] == "completed"
        assert data[0]["remote_filename"] == "model.gcode"

    def test_non_superuser_cannot_list_jobs(
        self, client: TestClient, db_session: Session
    ):
        headers = user_headers(db_session, "job-viewer")
        viewer = db_session.exec(
            select(User).where(User.username == "job-viewer")
        ).one()
        allowed = Collection(name="Allowed", slug="allowed", path="allowed")
        denied = Collection(name="Denied", slug="denied", path="denied")
        db_session.add_all([allowed, denied])
        db_session.commit()
        db_session.refresh(allowed)
        db_session.refresh(denied)
        db_session.add(
            CollectionPermission(
                user_id=viewer.id,
                collection_id=allowed.id,
                role=CollectionRole.VIEW,
            )
        )
        allowed_model = Model(
            name="Allowed job model",
            slug="allowed-job-model",
            hash="7" * 64,
            collection_id=allowed.id,
        )
        denied_model = Model(
            name="Denied job model",
            slug="denied-job-model",
            hash="8" * 64,
            collection_id=denied.id,
        )
        printer = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add_all([allowed_model, denied_model, printer])
        db_session.commit()
        db_session.refresh(allowed_model)
        db_session.refresh(denied_model)
        allowed_file = File(
            model_id=allowed_model.id,
            path="/data/allowed-job.gcode",
            original_filename="allowed-job.gcode",
            file_type=FileType.GCODE,
            version=1,
            size_bytes=100,
            sha256="9" * 64,
        )
        denied_file = File(
            model_id=denied_model.id,
            path="/data/denied-job.gcode",
            original_filename="denied-job.gcode",
            file_type=FileType.GCODE,
            version=1,
            size_bytes=100,
            sha256="a" * 64,
        )
        db_session.add_all([allowed_file, denied_file])
        db_session.commit()
        db_session.refresh(allowed_file)
        db_session.refresh(denied_file)
        db_session.refresh(printer)
        db_session.add_all(
            [
                PrintJob(
                    printer_id=printer.id,
                    file_id=allowed_file.id,
                    model_id=allowed_model.id,
                    remote_filename="allowed.gcode",
                    state=PrintJobState.COMPLETED,
                ),
                PrintJob(
                    printer_id=printer.id,
                    file_id=denied_file.id,
                    model_id=denied_model.id,
                    remote_filename="denied.gcode",
                    state=PrintJobState.COMPLETED,
                ),
            ]
        )
        db_session.commit()

        resp = client.get(f"/api/v1/printers/{printer.id}/jobs", headers=headers)

        assert resp.status_code == 403
        assert resp.json()["detail"] == "printer_permission_denied"


class TestDashboard:
    def test_dashboard_empty(self, client: TestClient, auth_headers):
        resp = client.get("/api/v1/printers/dashboard", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_printers"] == 0
        assert data["status_counts"] == {}
        assert data["active_jobs"] == 0
        assert data["groups"] == []

    def test_dashboard_with_printers(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p1 = Printer(name="P1", moonraker_url="http://10.0.0.1:7125", group="garage")
        p2 = Printer(name="P2", moonraker_url="http://10.0.0.2:7125", group="garage")
        p3 = Printer(name="P3", moonraker_url="http://10.0.0.3:7125")
        db_session.add_all([p1, p2, p3])
        db_session.commit()
        db_session.refresh(p1)
        db_session.refresh(p2)
        db_session.refresh(p3)

        from app.services.printer_hub import PrinterHub

        hub = PrinterHub()
        asyncio.run(hub._mark_status(p1.id, status="printing", error=None))
        asyncio.run(hub._mark_status(p2.id, status="ready", error=None))

        resp = client.get("/api/v1/printers/dashboard", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_printers"] == 3
        assert data["status_counts"].get("printing") == 1
        assert data["status_counts"].get("ready") == 1
        assert data["status_counts"].get("unknown") == 1
        groups = {g["name"]: g["count"] for g in data["groups"]}
        assert groups.get("garage") == 2
        assert groups.get("__ungrouped") == 1


class TestPrinterDiagnostics:
    def test_diagnostics_404_deleted_printer(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        from app.core.time import utcnow

        p = Printer(name="Gone", moonraker_url="http://gone.local")
        p.deleted_at = utcnow()
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.get(f"/api/v1/printers/{p.id}/diagnostics", headers=auth_headers)
        assert resp.status_code == 404
        assert resp.json()["detail"] == "printer_not_found"

    def test_diagnostics_configuration_error(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        # Direct DB insert bypasses the API's own _validate_provider_config,
        # simulating a row whose provider build() itself fails.
        p = Printer(
            name="Bad Elegoo",
            provider="elegoo_centauri",
            moonraker_url="",
            provider_variant="generic",
            elegoo_centauri_host="1.2.3.4",
        )
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.get(f"/api/v1/printers/{p.id}/diagnostics", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        config_check = next(c for c in body["checks"] if c["name"] == "configuration")
        assert config_check["ok"] is False
        assert config_check["code"] == "provider_credentials_missing"

    def test_diagnostics_provider_error_check(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        offline = ProviderError("boom", code="printer_offline")
        # Diagnostics runs provider_info *and* live_status; both talk to the printer, so
        # both are stood in for. Leaving query_status real made the endpoint dial
        # 10.0.0.1:7125 for the seconds it took to time out.
        with (
            patch(
                "app.services.printer_provider.MoonrakerProvider.info",
                new_callable=AsyncMock,
                side_effect=offline,
            ),
            patch(
                "app.services.printer_provider.MoonrakerProvider.query_status",
                new_callable=AsyncMock,
                side_effect=offline,
            ),
        ):
            resp = client.get(
                f"/api/v1/printers/{p.id}/diagnostics", headers=auth_headers
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        info_check = next(c for c in body["checks"] if c["name"] == "provider_info")
        assert info_check["ok"] is False
        assert info_check["code"] == "printer_offline"

    def test_reports_a_printer_that_was_deleted(
        self, client: TestClient, auth_headers, db_session: Session
    ) -> None:
        from app.core.time import utcnow
        from app.db.models import Printer

        printer = Printer(name="Gone", moonraker_url="http://gone.local:7125")
        db_session.add(printer)
        db_session.commit()
        db_session.refresh(printer)
        printer.deleted_at = utcnow()
        db_session.add(printer)
        db_session.commit()

        response = client.get(
            f"/api/v1/printers/{printer.id}/diagnostics", headers=auth_headers
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "printer_not_found"
