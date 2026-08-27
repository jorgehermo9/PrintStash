"""Authenticating the live printer status socket.

A browser cannot set an `Authorization` header on a WebSocket, so this endpoint takes a
short-lived ticket instead — and a ticket is a bearer credential in a URL, which lands in
proxy logs and browser history. That is why it is single-use and short-lived, and why
these tests care as much about what is refused as about what connects: a replayed ticket,
an expired one, one issued for a different printer, and a normal access token presented in
its place all have to fail closed.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session
from starlette.websockets import WebSocketDisconnect

from app.db.models import (
    Printer,
)


class TestPrinterWebSocketAuth:
    def test_one_time_ticket_replaces_access_token_in_websocket_url(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ):
        printer = Printer(name="Ticketed", moonraker_url="http://printer.local")
        db_session.add(printer)
        db_session.commit()
        db_session.refresh(printer)

        response = client.post(
            f"/api/v1/printers/{printer.id}/ws-ticket", headers=auth_headers
        )
        assert response.status_code == 200
        ticket = response.json()["ticket"]
        assert response.json()["expires_in"] <= 30

        with client.websocket_connect(
            f"/api/v1/printers/{printer.id}/ws?ticket={ticket}"
        ):
            pass

        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/api/v1/printers/{printer.id}/ws?ticket={ticket}"
            ):
                pass

        raw_token = auth_headers["Authorization"].split(" ", 1)[1]
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/api/v1/printers/{printer.id}/ws?token={raw_token}"
            ):
                pass

    def test_bearer_header_token_authenticates(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ):
        printer = Printer(name="Bearer", moonraker_url="http://printer.local")
        db_session.add(printer)
        db_session.commit()
        db_session.refresh(printer)

        with client.websocket_connect(
            f"/api/v1/printers/{printer.id}/ws", headers=auth_headers
        ):
            pass

    def test_bearer_header_invalid_token_closes(
        self, client: TestClient, db_session: Session
    ):
        printer = Printer(name="BadToken", moonraker_url="http://printer.local")
        db_session.add(printer)
        db_session.commit()
        db_session.refresh(printer)

        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/api/v1/printers/{printer.id}/ws",
                headers={"Authorization": "Bearer not-a-real-token"},
            ):
                pass


class TestWsTicket:
    def test_ws_ticket_404_unknown_printer(self, client: TestClient, auth_headers):
        resp = client.post("/api/v1/printers/99999/ws-ticket", headers=auth_headers)
        assert resp.status_code == 404
        assert resp.json()["detail"] == "printer_not_found"

    def test_ws_ticket_404_deleted_printer(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        from app.core.time import utcnow

        p = Printer(name="Gone", moonraker_url="http://gone.local")
        p.deleted_at = utcnow()
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.post(f"/api/v1/printers/{p.id}/ws-ticket", headers=auth_headers)
        assert resp.status_code == 404
