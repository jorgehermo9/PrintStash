"""R2 operations hardening: the Prometheus metrics endpoint.

The health-endpoint tests that used to live here moved to
tests/integration/api/v1/test_health.py, the mirror of the module they defend;
the scan-restart cleanup became the scan source's repair of a stranded
library, tested beside it in tests/integration/modules/sources/external_library/.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import _overlay
from app.db.models import FileType, JobState
from app.modules.work.jobs import jobs
from tests.factories import build_file, build_model, build_print_job


class TestMetricsEndpoint:
    def test_metrics_endpoint_exposes_prometheus_text(self, client: TestClient) -> None:
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers["content-type"]
        assert "printstash_app_info" in resp.text
        assert "printstash_http_request_duration_seconds" in resp.text

    def test_metrics_counts_terminal_jobs_by_definition(
        self, client: TestClient
    ) -> None:
        job_id = jobs.create(
            definition="ingestion.upload", subject_key="metrics/1", owner_user_id=None
        )
        jobs.finish(job_id, state=JobState.COMPLETED)

        body = client.get("/metrics").text

        assert 'printstash_jobs_total{kind="ingestion.upload",result="complete"}' in body

    def test_metrics_reports_jobs_by_state(self, client: TestClient) -> None:
        jobs.create(
            definition="ingestion.upload", subject_key="metrics/2", owner_user_id=None
        )

        body = client.get("/metrics").text

        assert 'printstash_job_depth{state="queued"} 1.0' in body

    def test_metrics_exposes_the_fleet_scheduler_state(
        self,
        client: TestClient,
        db_session: Session,
    ) -> None:
        model = build_model(db_session, name="Blocked", slug="blocked", hash="c" * 64)
        artifact = build_file(
            db_session,
            model,
            path="metrics/blocked.gcode",
            filename="blocked.gcode",
            file_type=FileType.GCODE,
            version=1,
            size_bytes=1,
            sha256="d" * 64,
        )
        build_print_job(
            db_session,
            artifact,
            remote_filename="blocked.gcode",
            blocked_reason="no_eligible_printer",
        )

        body = client.get("/metrics").text

        assert 'printstash_fleet_jobs{state="queued"} 1.0' in body
        assert "printstash_fleet_scheduler_last_tick_timestamp_seconds" in body

    def test_metrics_token_enforced_when_set(self, client: TestClient) -> None:
        _overlay["metrics_token"] = "s3cr3t"
        try:
            assert client.get("/metrics").status_code == 401
            assert (
                client.get(
                    "/metrics", headers={"Authorization": "Bearer wrong"}
                ).status_code
                == 401
            )
            assert (
                client.get(
                    "/metrics", headers={"Authorization": "Bearer s3cr3t"}
                ).status_code
                == 200
            )
        finally:
            _overlay.pop("metrics_token", None)
