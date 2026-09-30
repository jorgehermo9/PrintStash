"""Defend scheduled-audit policy behavior through services and HTTP boundaries."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import select

from app.core.time import ensure_utc
from app.db.models import (
    VaultAuditMode,
    VaultAuditPolicy,
    VaultAuditRun,
    VaultAuditRunState,
)
from app.modules.administration import vault_audit
from app.modules.administration.vault_audit_policy import (
    claim_due,
    health,
    list_policies,
    next_slot,
    skip_once,
)

NOW = datetime(2026, 9, 6, 2, 30, tzinfo=UTC)


class TestVaultAuditPolicy:
    def test_defaults_disabled(self, db_session):
        assert [
            (row.mode, row.enabled, row.cadence) for row in list_policies(db_session)
        ] == [("quick", False, "weekly"), ("full", False, "monthly")]

    def test_claims_one_due_audit(self, db_session, make_user, make_audit_policy):
        policy = make_audit_policy(
            make_user(), enabled=True, next_due_at=NOW - timedelta(minutes=30)
        )
        run_id = claim_due(db_session, now=NOW)
        run = db_session.get(VaultAuditRun, run_id)
        assert run.trigger == "scheduled"
        assert ensure_utc(run.scheduled_for) == NOW - timedelta(minutes=30)
        assert run.requested_by == policy.requested_by
        assert run.active_slot == "audit"
        assert ensure_utc(policy.next_due_at) > NOW

    def test_prevents_manual_scheduling_overlap(
        self, db_session, make_user, make_audit_policy
    ):
        user = make_user()
        make_audit_policy(user, enabled=True, next_due_at=NOW)
        run, created = vault_audit.create_run(db_session, user.id, VaultAuditMode.QUICK)
        assert created
        assert claim_due(db_session, now=NOW) is None
        assert [row.id for row in db_session.exec(select(VaultAuditRun)).all()] == [
            run.id
        ]

    def test_catches_up_once(self, db_session, make_user, make_audit_policy):
        policy = make_audit_policy(
            make_user(), enabled=True, next_due_at=NOW - timedelta(days=100)
        )
        run_id = claim_due(db_session, now=NOW)
        run = db_session.get(VaultAuditRun, run_id)
        run.state = VaultAuditRunState.FAILED
        run.active_slot = None
        db_session.add(run)
        db_session.commit()
        assert claim_due(db_session, now=NOW) is None
        assert ensure_utc(policy.next_due_at) > NOW

    @pytest.mark.parametrize(
        "reason", ["maintenance", "storage_unavailable", "shutdown"]
    )
    def test_defers_work(self, db_session, make_user, make_audit_policy, reason):
        policy = make_audit_policy(make_user(), enabled=True, next_due_at=NOW)
        assert claim_due(db_session, now=NOW, deferred_reason=reason) is None
        assert policy.deferred_reason == reason
        assert db_session.exec(select(VaultAuditRun)).all() == []

    def test_defers_outside_window(self, db_session, make_user, make_audit_policy):
        policy = make_audit_policy(make_user(), enabled=True, next_due_at=NOW)
        assert claim_due(db_session, now=NOW + timedelta(hours=4)) is None
        assert policy.deferred_reason == "outside_window"

    def test_paused_policy_does_not_run(self, db_session, make_user, make_audit_policy):
        make_audit_policy(make_user(), enabled=True, paused=True, next_due_at=NOW)
        assert claim_due(db_session, now=NOW) is None

    def test_skip_advances_once(self, db_session, make_user, make_audit_policy):
        from app.db.models import VaultAuditMode

        policy = make_audit_policy(make_user(), enabled=True, next_due_at=NOW)
        skip_once(db_session, VaultAuditMode.QUICK, now=NOW)
        assert ensure_utc(policy.next_due_at) == datetime(2026, 9, 13, 2, tzinfo=UTC)

    def test_clamps_month_day(self, db_session, make_user, make_audit_policy):
        policy = make_audit_policy(make_user(), mode="full", month_day=31)
        assert next_slot(policy, datetime(2026, 2, 1, tzinfo=UTC)) == datetime(
            2026, 2, 28, 2, tzinfo=UTC
        )

    def test_reports_overdue_health(self, db_session, make_user, make_audit_policy):
        make_audit_policy(
            make_user(), enabled=True, next_due_at=NOW - timedelta(days=1)
        )
        assert health(db_session, now=NOW)["ok"] is False

    def test_deadline_cancels_run(self, db_session, make_user, make_audit_run):
        run = make_audit_run(
            make_user(),
            state=VaultAuditRunState.PENDING,
            active_slot="audit",
            deadline_at=NOW,
        )
        vault_audit.execute_run(run.id)
        db_session.refresh(run)
        assert run.state == VaultAuditRunState.CANCELLED
        assert run.error_code == "audit_window_expired"
        assert run.result_recorded is False
        assert run.active_slot is None

    @pytest.mark.parametrize(
        "payload",
        [
            {"timezone": "Invalid/Zone"},
            {"start_time": "24:99"},
            {"read_concurrency": 2},
            {"repair_actions": ["restore_recommended_revision"]},
            {"window_minutes": 0},
        ],
    )
    def test_rejects_invalid_policy(self, client, auth_headers, payload):
        response = client.put(
            "/api/v1/maintenance/audit-policies/quick",
            json=payload,
            headers=auth_headers,
        )
        assert response.status_code == 422

    def test_requires_full_cost_acknowledgement(self, client, auth_headers):
        response = client.put(
            "/api/v1/maintenance/audit-policies/full",
            json={"enabled": True},
            headers=auth_headers,
        )
        assert response.status_code == 400

    def test_saves_policy_through_api(self, client, auth_headers, db_session):
        response = client.put(
            "/api/v1/maintenance/audit-policies/quick",
            json={"enabled": True},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["next_due_at"]
        assert db_session.get(VaultAuditPolicy, "quick").enabled

    def test_denies_anonymous_policy_control(self, client):
        assert (
            client.put(
                "/api/v1/maintenance/audit-policies/quick", json={"enabled": True}
            ).status_code
            == 401
        )

    def test_pause_preserves_due_slot(self, db_session, make_user, make_audit_policy):
        from app.modules.administration.vault_audit_policy import update_policy

        user = make_user()
        policy = make_audit_policy(user, enabled=True, next_due_at=NOW)
        update_policy(
            db_session, VaultAuditMode.QUICK, {"paused": True}, user.id, now=NOW
        )
        assert ensure_utc(policy.next_due_at) == NOW

    def test_manual_success_satisfies_due_slot(
        self, db_session, make_user, make_audit_policy, make_audit_run
    ):
        from app.modules.administration.vault_audit_results import record_success

        user = make_user()
        policy = make_audit_policy(user, enabled=True, next_due_at=NOW)
        run = make_audit_run(
            user, started_at=NOW, finished_at=NOW + timedelta(minutes=1)
        )
        record_success(db_session, run)
        db_session.commit()
        assert ensure_utc(policy.next_due_at) > NOW
        assert ensure_utc(policy.last_success_at) == NOW + timedelta(minutes=1)

    def test_denies_nonadmin_policy_control(self, client, make_user, headers_for):
        user = make_user(superuser=False)
        assert (
            client.put(
                "/api/v1/maintenance/audit-policies/quick",
                json={"enabled": True},
                headers=headers_for(user),
            ).status_code
            == 403
        )

    def test_restart_releases_completed_claim(
        self, db_session, make_user, make_audit_run
    ):
        run = make_audit_run(make_user(), active_slot="audit")
        vault_audit.fail_interrupted_run(run.id)
        db_session.refresh(run)
        assert run.state == VaultAuditRunState.COMPLETED
        assert run.active_slot is None

    def test_estimates_known_remote_bytes(self, db_session, make_owned_storage_object):
        from app.modules.administration.vault_audit_policy import estimated_remote_bytes

        make_owned_storage_object(
            backend="s3",
            size_bytes=100,
            object_kind="artifact",
            key="files/source.gcode",
        )
        make_owned_storage_object(
            backend="s3",
            size_bytes=20,
            object_kind="thumbnail",
            key="thumbs/source.webp",
        )
        make_owned_storage_object(
            backend="local", size_bytes=500, object_kind="artifact"
        )
        assert estimated_remote_bytes(db_session, VaultAuditMode.FULL) == 120

    def test_quick_estimate_excludes_artifact_hash_reads(
        self, db_session, make_owned_storage_object
    ):
        from app.modules.administration.vault_audit_policy import estimated_remote_bytes

        make_owned_storage_object(
            backend="s3",
            size_bytes=100,
            object_kind="artifact",
            key="files/source.gcode",
        )
        make_owned_storage_object(
            backend="s3",
            size_bytes=20,
            object_kind="thumbnail",
            key="thumbs/source.webp",
        )
        assert estimated_remote_bytes(db_session, VaultAuditMode.QUICK) == 20

    def test_enforces_stream_bandwidth(
        self, db_session, local_storage, make_user, make_audit_run, monkeypatch
    ):
        import hashlib
        from types import SimpleNamespace

        from app.modules.storage.storage_backend.runtime import get_backend

        elapsed = [0.0]

        def sleep(seconds):
            elapsed[0] += seconds

        monkeypatch.setattr(
            vault_audit,
            "time",
            SimpleNamespace(monotonic=lambda: elapsed[0], sleep=sleep),
        )
        path = str(local_storage / "rate-check.bin")
        payload = b"a" * 2048
        get_backend().write_bytes(payload, path)
        run = make_audit_run(
            make_user(), state=VaultAuditRunState.RUNNING, bytes_per_second=1024
        )
        assert (
            vault_audit._hash_blob(path, db_session, run)
            == hashlib.sha256(payload).hexdigest()
        )
        assert elapsed[0] == pytest.approx(2.0)

    def test_unavailable_storage_preserves_success_baseline(
        self, db_session, make_user, make_audit_policy
    ):
        policy = make_audit_policy(
            make_user(),
            enabled=True,
            next_due_at=NOW,
            last_success_at=NOW - timedelta(days=8),
        )
        assert (
            claim_due(db_session, now=NOW, deferred_reason="storage_unavailable")
            is None
        )
        assert ensure_utc(policy.last_success_at) == NOW - timedelta(days=8)

    def test_throttled_hash_allows_cancellation_from_another_session(
        self, tmp_path, local_storage, monkeypatch
    ):
        from types import SimpleNamespace

        from sqlmodel import Session, SQLModel, create_engine

        from app.modules.storage.storage_backend.runtime import get_backend
        from tests.factories import build_audit_run, build_user

        engine = create_engine(
            f"sqlite:///{tmp_path / 'audit-cancellation.sqlite'}",
            connect_args={"timeout": 0.1},
        )
        SQLModel.metadata.create_all(engine)
        path = str(local_storage / "cancel-during-throttle.bin")
        get_backend().write_bytes(b"audit bytes", path)
        try:
            with Session(engine) as session:
                run = build_audit_run(
                    session,
                    build_user(session),
                    state=VaultAuditRunState.RUNNING,
                    bytes_per_second=1,
                )
                run_id = run.id

                def cancel(_seconds):
                    with Session(engine) as requesting_session:
                        vault_audit.request_cancel(requesting_session, run_id)

                monkeypatch.setattr(
                    vault_audit,
                    "time",
                    SimpleNamespace(monotonic=lambda: 0.0, sleep=cancel),
                )
                with pytest.raises(vault_audit.AuditWindowExpired):
                    vault_audit._hash_blob(path, session, run)
                session.refresh(run)
                assert run.state == VaultAuditRunState.CANCELLED
                assert run.cancel_requested
                assert run.bytes_read == len(b"audit bytes")
        finally:
            engine.dispose()
