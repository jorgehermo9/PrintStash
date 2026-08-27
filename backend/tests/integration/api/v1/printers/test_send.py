"""Sending a library revision to a printer.

This is the app's one write into somebody else's storage, so it refuses more than it
accepts. The caller needs the operator role **on that printer**; the file has to be
G-code, live, and actually present in the vault; and the printer has to be in a state that
can receive it. Every one of those is checked before a byte leaves, because a partial
upload to a machine's SD card is something the operator has to go and clean up by hand.

The material gate is the subtle one: sending PETG G-code to a machine loaded with PLA
produces a failed print and a blocked nozzle, so a mismatch is refused unless it is
explicitly overridden.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.models import (
    Printer,
    PrintJob,
    PrintJobState,
)
from app.services.printer_jobs import PrinterJobError
from app.services.printer_provider import ProviderError


class TestSendToPrinter:
    def test_send_requires_auth(self, client: TestClient, db_session: Session):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.post(
            f"/api/v1/printers/{p.id}/send",
            json={"file_id": 1, "start_print": False},
        )
        assert resp.status_code == 401

    def test_send_non_gcode_rejected(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        from app.db.models import File, Model

        m = Model(name="Model", slug="model-stl", hash="k" * 64)
        db_session.add(m)
        db_session.commit()
        db_session.refresh(m)

        f = File(
            model_id=m.id,
            path="/data/model.stl",
            original_filename="model.stl",
            file_type="stl",
            version=1,
            size_bytes=100,
            sha256="l" * 64,
        )
        db_session.add(f)
        db_session.commit()
        db_session.refresh(f)

        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.post(
            f"/api/v1/printers/{p.id}/send",
            json={"file_id": f.id, "start_print": False},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "file_not_gcode"

    def test_send_busy_bambu_creates_no_job(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        """The Bambu ready-state guard must run before creating a PrintJob."""
        from app.db.models import File, Model

        m = Model(name="Model", slug="model-bambu-send", hash="m" * 64)
        db_session.add(m)
        db_session.commit()
        db_session.refresh(m)

        f = File(
            model_id=m.id,
            path="/data/part.gcode",
            original_filename="part.gcode",
            file_type="gcode",
            version=1,
            size_bytes=4,
            sha256="n" * 64,
        )
        db_session.add(f)
        db_session.commit()
        db_session.refresh(f)

        p = Printer(
            name="Bambu",
            provider="bambu_lan",
            moonraker_url="",
            bambu_host="192.168.1.50",
            bambu_serial="SN123",
            bambu_access_code="access",
        )
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        with patch(
            "app.services.printer_provider.BambuLanProvider.query_status",
            new_callable=AsyncMock,
            return_value={"result": {"status": {"print_stats": {"state": "printing"}}}},
        ):
            resp = client.post(
                f"/api/v1/printers/{p.id}/send",
                json={"file_id": f.id, "start_print": False},
                headers=auth_headers,
            )

        assert resp.status_code == 409
        assert resp.json()["detail"] == "printer_not_ready"
        jobs = db_session.exec(
            select(PrintJob).where(PrintJob.printer_id == p.id)
        ).all()
        assert jobs == []

    def test_send_404_printer(self, client: TestClient, auth_headers):
        resp = client.post(
            "/api/v1/printers/99999/send",
            json={"file_id": 1, "start_print": False},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_send_404_file(self, client: TestClient, auth_headers, db_session: Session):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.post(
            f"/api/v1/printers/{p.id}/send",
            json={"file_id": 99999, "start_print": False},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_send_rejects_path_traversal_remote_filename(
        self, client: TestClient, auth_headers
    ):
        resp = client.post(
            "/api/v1/printers/1/send",
            json={"file_id": 1, "remote_filename": "../escape.gcode"},
            headers=auth_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"] == "request_validation_failed"

    def test_send_provider_crash_returns_stable_error(
        self, client: TestClient, auth_headers, db_session: Session, tmp_path
    ):
        from app.db.models import File, Model

        local = tmp_path / "bracket.gcode"
        local.write_text("G28\n")
        m = Model(name="Bracket", slug="send-crash-bracket", hash="t" * 64)
        db_session.add(m)
        db_session.commit()
        db_session.refresh(m)
        f = File(
            model_id=m.id,
            path="/data/bracket.gcode",
            original_filename="bracket.gcode",
            file_type="gcode",
            version=1,
            size_bytes=4,
            sha256="u" * 64,
        )
        db_session.add(f)
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(f)
        db_session.refresh(p)

        class FakeBackend:
            def exists(self, _path):
                return True

            def download_to_path(self, _path, _target):
                return local

        with (
            patch("app.api.v1.printers.get_backend", return_value=FakeBackend()),
            patch(
                "app.services.moonraker.MoonrakerClient.upload_gcode",
                new_callable=AsyncMock,
            ) as mock_upload,
        ):
            mock_upload.side_effect = RuntimeError("secret provider stack")
            resp = client.post(
                f"/api/v1/printers/{p.id}/send",
                json={"file_id": f.id, "start_print": False},
                headers=auth_headers,
            )

        assert resp.status_code == 502
        assert resp.json()["detail"] == "provider_error"
        assert "secret provider stack" not in resp.text
        job = db_session.exec(select(PrintJob).where(PrintJob.printer_id == p.id)).one()
        assert job.state == PrintJobState.FAILED

    def test_send_records_printer_file_inventory(
        self, client: TestClient, auth_headers, db_session: Session, tmp_path
    ):
        from app.db.models import File, Model, PrinterFile

        local = tmp_path / "bracket.gcode"
        local.write_text("G28\n")
        m = Model(name="Bracket", slug="send-bracket", hash="s" * 64)
        db_session.add(m)
        db_session.commit()
        db_session.refresh(m)
        f = File(
            model_id=m.id,
            path="/data/bracket.gcode",
            original_filename="bracket.gcode",
            file_type="gcode",
            version=1,
            size_bytes=4,
            sha256="d" * 64,
        )
        db_session.add(f)
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(f)
        db_session.refresh(p)

        class FakeBackend:
            def exists(self, _path):
                return True

            def download_to_path(self, _path, _target):
                return local

        with (
            patch("app.api.v1.printers.get_backend", return_value=FakeBackend()),
            patch(
                "app.services.moonraker.MoonrakerClient.upload_gcode",
                new_callable=AsyncMock,
            ) as mock_upload,
        ):
            mock_upload.return_value = {"result": "ok"}
            resp = client.post(
                f"/api/v1/printers/{p.id}/send",
                json={"file_id": f.id, "start_print": False},
                headers=auth_headers,
            )

        assert resp.status_code == 200
        assert resp.json()["state"] == PrintJobState.COMPLETED.value
        row = db_session.exec(
            select(PrinterFile).where(PrinterFile.printer_id == p.id)
        ).one()
        assert row.file_id == f.id
        assert row.remote_filename == f"bracket__vault-f{f.id}-{'d' * 12}.gcode"
        assert row.matched_by == "upload_history"
        mock_upload.assert_awaited_once()
        assert mock_upload.await_args.args[1] == row.remote_filename

    def _gcode_file(self, db_session: Session, suffix: str = ""):
        from app.db.models import File, Model

        m = Model(name=f"M{suffix}", slug=f"m{suffix}", hash=f"{suffix or '0'}" * 64)
        db_session.add(m)
        db_session.commit()
        db_session.refresh(m)
        f = File(
            model_id=m.id,
            path=f"/data/part{suffix}.gcode",
            original_filename=f"part{suffix}.gcode",
            file_type="gcode",
            version=1,
            size_bytes=10,
            sha256=f"{suffix or '1'}" * 64,
        )
        db_session.add(f)
        db_session.commit()
        db_session.refresh(f)
        return m, f

    def test_send_rejected_when_provider_cannot_upload(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        # Every registered provider currently supports upload, so there's no
        # real fixture for "provider without upload" — force the gate the
        # /send route actually checks (capabilities.can_upload) instead.
        from app.services.printer_provider import ElegooCentauriProvider

        _, f = self._gcode_file(db_session, "eleg")
        p = Printer(
            name="Centauri",
            provider="elegoo_centauri",
            moonraker_url="",
            provider_variant="elegoo_centauri_carbon",
            elegoo_centauri_host="192.168.1.60",
        )
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        no_upload = replace(ElegooCentauriProvider.capabilities, supported=frozenset())
        with patch.object(ElegooCentauriProvider, "capabilities", no_upload):
            resp = client.post(
                f"/api/v1/printers/{p.id}/send",
                json={"file_id": f.id, "start_print": False},
                headers=auth_headers,
            )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "operation_not_supported_for_provider"

    def test_send_ready_check_provider_error_502(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        _, f = self._gcode_file(db_session, "rdy")
        p = Printer(
            name="Bambu",
            provider="bambu_lan",
            moonraker_url="",
            bambu_host="192.168.1.50",
            bambu_serial="SN123",
            bambu_access_code="access",
        )
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        with patch(
            "app.services.printer_provider.BambuLanProvider.query_status",
            new_callable=AsyncMock,
            side_effect=ProviderError("boom", code="printer_offline"),
        ):
            resp = client.post(
                f"/api/v1/printers/{p.id}/send",
                json={"file_id": f.id, "start_print": False},
                headers=auth_headers,
            )
        assert resp.status_code == 502
        assert resp.json()["detail"] == "printer_offline"

    def test_send_appends_gcode_extension_when_missing(
        self, client: TestClient, auth_headers, db_session: Session, tmp_path
    ):
        _, f = self._gcode_file(db_session, "ext")
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        local = tmp_path / "part.gcode"
        local.write_text("G28\n")

        class FakeBackend:
            def exists(self, _path):
                return True

            def download_to_path(self, _path, _target):
                return local

        with (
            patch("app.api.v1.printers.get_backend", return_value=FakeBackend()),
            patch(
                "app.services.moonraker.MoonrakerClient.upload_gcode",
                new_callable=AsyncMock,
                return_value={"result": "ok"},
            ),
        ):
            resp = client.post(
                f"/api/v1/printers/{p.id}/send",
                json={
                    "file_id": f.id,
                    "start_print": False,
                    "remote_filename": "no_extension",
                },
                headers=auth_headers,
            )
        assert resp.status_code == 200
        assert resp.json()["remote_filename"] == "no_extension.gcode"

    def test_send_file_blob_missing_returns_410(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        _, f = self._gcode_file(db_session, "blob")
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        class FakeBackend:
            def exists(self, _path):
                return False

        with patch("app.api.v1.printers.get_backend", return_value=FakeBackend()):
            resp = client.post(
                f"/api/v1/printers/{p.id}/send",
                json={"file_id": f.id, "start_print": False},
                headers=auth_headers,
            )
        assert resp.status_code == 410
        assert resp.json()["detail"] == "file_blob_missing"

    def test_send_file_role_404_when_model_deleted(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        from app.core.time import utcnow

        m, f = self._gcode_file(db_session, "del")
        m.deleted_at = utcnow()
        db_session.add(m)
        db_session.commit()
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.post(
            f"/api/v1/printers/{p.id}/send",
            json={"file_id": f.id, "start_print": False},
            headers=auth_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "file_not_found"

    def test_send_provider_error_marks_job_failed(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        _, f = self._gcode_file(db_session, "pe")
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        with (
            patch(
                "app.api.v1.printers.get_backend",
                return_value=type(
                    "FB", (), {"exists": staticmethod(lambda _p: True)}
                )(),
            ),
            patch(
                "app.api.v1.printers.transfer_artifact",
                new_callable=AsyncMock,
                side_effect=ProviderError("boom", code="printer_offline"),
            ),
        ):
            resp = client.post(
                f"/api/v1/printers/{p.id}/send",
                json={"file_id": f.id, "start_print": False},
                headers=auth_headers,
            )
        assert resp.status_code == 502
        assert resp.json()["detail"] == "printer_offline"
        job = db_session.exec(select(PrintJob).where(PrintJob.printer_id == p.id)).one()
        assert job.state == PrintJobState.FAILED
        assert job.error == "printer_offline"

    def test_send_printer_job_error_marks_job_failed(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        _, f = self._gcode_file(db_session, "pje")
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        with (
            patch(
                "app.api.v1.printers.get_backend",
                return_value=type(
                    "FB", (), {"exists": staticmethod(lambda _p: True)}
                )(),
            ),
            patch(
                "app.api.v1.printers.transfer_artifact",
                new_callable=AsyncMock,
                side_effect=PrinterJobError("dispatch_failed"),
            ),
        ):
            resp = client.post(
                f"/api/v1/printers/{p.id}/send",
                json={"file_id": f.id, "start_print": False},
                headers=auth_headers,
            )
        assert resp.status_code == 502
        assert resp.json()["detail"] == "dispatch_failed"
        job = db_session.exec(select(PrintJob).where(PrintJob.printer_id == p.id)).one()
        assert job.state == PrintJobState.FAILED
        assert job.error == "dispatch_failed"

    def test_send_http_exception_from_transfer_passes_through(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        from fastapi import HTTPException

        _, f = self._gcode_file(db_session, "http")
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        with (
            patch(
                "app.api.v1.printers.get_backend",
                return_value=type(
                    "FB", (), {"exists": staticmethod(lambda _p: True)}
                )(),
            ),
            patch(
                "app.api.v1.printers.transfer_artifact",
                new_callable=AsyncMock,
                side_effect=HTTPException(status_code=418, detail="teapot"),
            ),
        ):
            resp = client.post(
                f"/api/v1/printers/{p.id}/send",
                json={"file_id": f.id, "start_print": False},
                headers=auth_headers,
            )
        assert resp.status_code == 418
        assert resp.json()["detail"] == "teapot"
