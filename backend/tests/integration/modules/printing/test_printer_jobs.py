"""Gap-fill for app.modules.printing.printer_jobs: transfer_artifact's storage-error
branch, _dispatch_claimed's dependency/capability/readiness guards, and one
dispatch slice (drain until nothing is eligible, end on a bad claim, respect
the slice budget).

test_fleet_api.py already covers the happy path and the generic
except-wraps-into-FAILED branch (a real connection failure to a fake host);
this file targets the specific guard clauses in between.
"""

from __future__ import annotations

import asyncio
import hashlib
import struct
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from printstash_core.printers import Capability, PrintArtifactFormat
from sqlmodel import Session

from app.db.models import (
    FileType,
    Printer,
    PrinterPermission,
    PrinterRole,
    PrinterStatus,
    PrintJob,
    PrintJobState,
    RoutingStrategy,
)
from app.modules.printing import printer_jobs
from app.modules.printing.printer_jobs import (
    DispatchOutcomeUnknownError,
    PrinterJobError,
    transfer_artifact,
)
from app.modules.printing.printer_provider import (
    PrinterProviderClient,
    ProviderCapabilities,
    ProviderError,
)
from tests.factories import (
    a_gcode_artifact,
    build_print_job,
    build_printer,
    detached_file,
    printer_config,
    user_config,
)


def _provider_builder(provider: PrinterProviderClient):
    return lambda _printer: provider


def _unused_provider_builder(_printer: Printer) -> PrinterProviderClient:
    raise AssertionError("provider construction should not be reached")


# ---------------------------------------------------------------------------
# transfer_artifact
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _dispatch_claimed guard clauses
# ---------------------------------------------------------------------------


def _seeded_upload_job(db_session: Session) -> tuple[Printer, PrintJob]:
    printer = build_printer(
        db_session,
        name="Capabilities",
        moonraker_url="http://caps",
        status=PrinterStatus.READY,
    )
    artifact = a_gcode_artifact(db_session, "Queue cube")
    job = build_print_job(
        db_session,
        artifact,
        printer_id=printer.id,
        remote_filename="x.gcode",
        state=PrintJobState.UPLOADING,
    )
    return printer, job


# ---------------------------------------------------------------------------
# run_fleet_scheduler tick loop
# ---------------------------------------------------------------------------


class TestTransferArtifact:
    def test_transfer_artifact_wraps_download_failure_as_storage_error(
        self,
        tmp_path: Path,
    ) -> None:
        class Backend:
            def exists(self, _key: str) -> bool:
                return True

            @contextmanager
            def local_path(self, _key: str):
                raise OSError("disk full")
                yield tmp_path / "unreachable.gcode"

        backend = Backend()
        artifact = detached_file(
            id=1,
            path="vault-data/x.gcode",
            original_filename="x.gcode",
            file_type=FileType.GCODE,
            sha256="a" * 64,
        )

        async def _run() -> None:
            with pytest.raises(PrinterJobError, match="storage_error"):
                await transfer_artifact(
                    backend, AsyncMock(), artifact, "x.gcode", start_print=True
                )

        asyncio.run(_run())

    def test_transfer_artifact_raises_when_blob_missing(self) -> None:
        backend = AsyncMock()
        backend.exists = lambda _key: False
        artifact = detached_file(
            id=1,
            path="vault-data/gone.gcode",
            original_filename="gone.gcode",
            file_type=FileType.GCODE,
            sha256="a" * 64,
        )

        async def _run() -> None:
            with pytest.raises(PrinterJobError, match="file_blob_missing"):
                await transfer_artifact(
                    backend, AsyncMock(), artifact, "gone.gcode", start_print=True
                )

        asyncio.run(_run())

    def test_transfer_artifact_marks_provider_upload_timeout_as_outcome_unknown(
        self,
        tmp_path: Path,
    ) -> None:
        class Backend:
            def exists(self, _key: str) -> bool:
                return True

            @contextmanager
            def local_path(self, _key: str):
                target = tmp_path / "x.gcode"
                target.write_bytes(b"G28\n")
                yield target

        artifact = detached_file(
            id=1,
            path="vault-data/x.gcode",
            original_filename="x.gcode",
            file_type=FileType.GCODE,
            sha256="a" * 64,
        )
        provider = AsyncMock()
        provider.capabilities = ProviderCapabilities(
            supported=frozenset({Capability.START, Capability.UPLOAD}),
            accepted_print_formats=frozenset({PrintArtifactFormat.GCODE_TEXT}),
        )
        provider.upload.side_effect = ProviderError(
            "timed out", code="provider_timeout"
        )

        async def _run() -> None:
            with pytest.raises(DispatchOutcomeUnknownError):
                await transfer_artifact(
                    provider=provider,
                    backend=Backend(),
                    artifact=artifact,
                    remote_filename="x.gcode",
                    start_print=True,
                    mark_outcome_unknown=True,
                )

        asyncio.run(_run())

    def test_transfer_artifact_reads_an_external_source_without_the_vault_backend(
        self, tmp_path: Path
    ) -> None:
        payload = b"G28\n; from NAS\n"
        source = tmp_path / "nas" / "external.gcode"
        source.parent.mkdir()
        source.write_bytes(payload)
        artifact = detached_file(
            id=1,
            path=str(source),
            original_filename=source.name,
            file_type=FileType.GCODE,
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            is_external=True,
        )

        class VaultBackend:
            def exists(self, _key: str) -> bool:
                raise AssertionError("external path reached the active vault backend")

        uploaded: list[bytes] = []

        class Provider:
            capabilities = ProviderCapabilities(
                supported=frozenset({Capability.START, Capability.UPLOAD}),
                accepted_print_formats=frozenset({PrintArtifactFormat.GCODE_TEXT}),
            )

            async def upload(self, local: Path, _remote: str) -> None:
                uploaded.append(local.read_bytes())

            async def start(self, _remote: str) -> None:
                return None

        asyncio.run(
            transfer_artifact(
                VaultBackend(),
                Provider(),
                artifact,
                "external.gcode",
                start_print=True,
            )
        )

        assert uploaded == [payload]

    def test_invalid_bgcode_is_rejected_before_provider_io(
        self, tmp_path: Path
    ) -> None:
        class Backend:
            def exists(self, _key: str) -> bool:
                return True

            @contextmanager
            def local_path(self, _key: str):
                target = tmp_path / "broken.bgcode"
                target.write_bytes(b"GCDE-broken")
                yield target

        artifact = detached_file(
            id=1,
            path="vault-data/broken.bgcode",
            original_filename="broken.bgcode",
            file_type=FileType.GCODE,
        )
        provider = AsyncMock()
        provider.capabilities = ProviderCapabilities(
            supported=frozenset({Capability.START, Capability.UPLOAD}),
            accepted_print_formats=frozenset({PrintArtifactFormat.BGCODE_BINARY}),
        )

        with pytest.raises(PrinterJobError, match="invalid_binary_gcode"):
            asyncio.run(
                transfer_artifact(
                    Backend(), provider, artifact, "broken.bgcode", start_print=False
                )
            )

        provider.upload.assert_not_awaited()

    def test_valid_bgcode_reaches_a_compatible_provider(self, tmp_path: Path) -> None:
        body = b"G1 X1\n"
        payload = (
            b"GCDE"
            + struct.pack("<IH", 1, 0)
            + struct.pack("<HHI", 1, 0, len(body))
            + struct.pack("<H", 2)
            + body
        )

        class Backend:
            def exists(self, _key: str) -> bool:
                return True

            @contextmanager
            def local_path(self, _key: str):
                target = tmp_path / "valid.bgcode"
                target.write_bytes(payload)
                yield target

        artifact = detached_file(
            id=1,
            path="vault-data/valid.bgcode",
            original_filename="valid.bgcode",
            file_type=FileType.GCODE,
        )
        provider = AsyncMock()
        provider.capabilities = ProviderCapabilities(
            supported=frozenset({Capability.START, Capability.UPLOAD}),
            accepted_print_formats=frozenset({PrintArtifactFormat.BGCODE_BINARY}),
        )

        asyncio.run(
            transfer_artifact(
                Backend(), provider, artifact, "valid.bgcode", start_print=False
            )
        )

        provider.upload.assert_awaited_once()
        provider.start.assert_not_awaited()


class TestDispatchClaimed:
    def test_dispatch_claimed_raises_when_the_printer_row_cannot_be_loaded(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The `queue_dependency_missing` guard, which a correct schema makes
        unreachable.

        This used to hard-delete the printer out from under a queued job. That is
        refused now — `print_jobs.printer_id` is a RESTRICT foreign key and the suite
        enforces foreign keys, as production does — and refusing it is right: the
        state is not reachable through any code path. The guard still earns its
        keep, because an installation upgraded from an older release is missing
        several of those constraints (see
        `tests/integration/db/migrations/test_models_versus_chain.py`), so on that
        schema the row really can vanish.

        Making the lookup return `None` is therefore the honest way to reach it:
        the behaviour under test is how dispatch reacts to a dependency it cannot
        load, not the database's ability to lose one.
        """
        printer = build_printer(
            db_session,
            name="Vanishing",
            moonraker_url="http://vanish",
            status=PrinterStatus.READY,
        )
        artifact = a_gcode_artifact(db_session, "Queue cube")
        job = build_print_job(
            db_session,
            artifact,
            printer_id=printer.id,
            remote_filename="x.gcode",
            state=PrintJobState.UPLOADING,
        )
        real_get = Session.get

        def get(self, entity, ident, *args, **kwargs):
            if entity is Printer:
                return None
            return real_get(self, entity, ident, *args, **kwargs)

        monkeypatch.setattr(Session, "get", get)

        with pytest.raises(RuntimeError, match="queue_dependency_missing"):
            asyncio.run(
                printer_jobs._dispatch_claimed(job.id, _unused_provider_builder)
            )  # noqa: SLF001

    def test_dispatch_claimed_raises_when_job_has_no_printer(
        self, db_session: Session
    ) -> None:
        artifact = a_gcode_artifact(db_session, "Queue cube")
        job = build_print_job(
            db_session,
            artifact,
            printer_id=None,
            remote_filename="x.gcode",
            state=PrintJobState.UPLOADING,
        )

        with pytest.raises(RuntimeError, match="queue_job_not_found"):
            asyncio.run(
                printer_jobs._dispatch_claimed(job.id, _unused_provider_builder)
            )  # noqa: SLF001

    def test_dispatch_claimed_raises_when_provider_cannot_upload_or_start(
        self,
        db_session: Session,
    ) -> None:
        _printer, job = _seeded_upload_job(db_session)
        provider = AsyncMock()
        provider.capabilities = ProviderCapabilities(
            supported=frozenset()
        )  # no START/UPLOAD

        with pytest.raises(ProviderError, match="operation_not_supported_for_provider"):
            asyncio.run(
                printer_jobs._dispatch_claimed(job.id, _provider_builder(provider))
            )  # noqa: SLF001

    def test_dispatch_claimed_raises_printer_not_ready_when_requires_ready_before_send(
        self,
        db_session: Session,
    ) -> None:
        _printer, job = _seeded_upload_job(db_session)
        provider = AsyncMock()
        from app.modules.printing.printer_provider import Capability

        provider.capabilities = ProviderCapabilities(
            supported=frozenset({Capability.START, Capability.UPLOAD}),
            accepted_print_formats=frozenset({PrintArtifactFormat.GCODE_TEXT}),
            requires_ready_before_send=True,
        )
        provider.query_status.return_value = {
            "result": {"status": {"print_stats": {"state": "printing"}}}
        }

        with pytest.raises(ProviderError, match="printer_not_ready"):
            asyncio.run(
                printer_jobs._dispatch_claimed(job.id, _provider_builder(provider))
            )  # noqa: SLF001

    def test_dispatch_claimed_proceeds_when_ready_before_send_reports_idle(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        _printer, job = _seeded_upload_job(db_session)
        provider = AsyncMock()
        from app.modules.printing.printer_provider import Capability

        provider.capabilities = ProviderCapabilities(
            supported=frozenset({Capability.START, Capability.UPLOAD}),
            accepted_print_formats=frozenset({PrintArtifactFormat.GCODE_TEXT}),
            requires_ready_before_send=True,
        )
        provider.query_status.return_value = {
            "result": {"status": {"print_stats": {"state": "idle"}}}
        }

        class _Backend:
            def exists(self, _key: str) -> bool:
                return True

            @contextmanager
            def local_path(self, _key: str):
                target = tmp_path / "dispatch-artifact.gcode"
                target.write_text("G28\n")
                yield target

        with (
            patch("app.modules.printing.printer_jobs.get_backend", return_value=_Backend()),
        ):
            asyncio.run(
                printer_jobs._dispatch_claimed(job.id, _provider_builder(provider))
            )  # noqa: SLF001

        provider.upload.assert_awaited_once()
        provider.start.assert_awaited_once()


class TestDrainDispatchQueue:
    """One ``printing.dispatch`` slice: dispatch until nothing is eligible."""

    def test_dispatches_until_nothing_is_eligible(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        queue = [41, 42, None]

        async def fake_dispatch_next(_provider_builder) -> int | None:
            return queue.pop(0)

        monkeypatch.setattr(printer_jobs, "dispatch_next", fake_dispatch_next)
        printer_jobs.scheduler_status.last_dispatch_at = None

        dispatched = asyncio.run(
            printer_jobs.drain_dispatch_queue(
                _unused_provider_builder, budget_seconds=5
            )
        )

        assert dispatched == 2
        assert queue == []
        assert printer_jobs.scheduler_status.last_dispatch_at is not None

    def test_a_bad_claim_ends_the_slice_and_is_recorded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The next slice starts from the database again; a failing claim must
        # not spin inside this one.
        calls: list[int] = []

        async def failing_dispatch_next(_provider_builder) -> int | None:
            calls.append(1)
            raise RuntimeError("simulated tick failure")

        monkeypatch.setattr(printer_jobs, "dispatch_next", failing_dispatch_next)
        printer_jobs.scheduler_status.last_error = None

        dispatched = asyncio.run(
            printer_jobs.drain_dispatch_queue(
                _unused_provider_builder, budget_seconds=5
            )
        )

        assert (dispatched, len(calls)) == (0, 1)
        assert printer_jobs.scheduler_status.last_error == "RuntimeError"

    def test_a_successful_claim_clears_the_last_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def nothing_queued(_provider_builder) -> int | None:
            return None

        monkeypatch.setattr(printer_jobs, "dispatch_next", nothing_queued)
        printer_jobs.scheduler_status.last_error = "RuntimeError"

        asyncio.run(
            printer_jobs.drain_dispatch_queue(
                _unused_provider_builder, budget_seconds=5
            )
        )

        assert printer_jobs.scheduler_status.last_error is None

    def test_stops_when_the_slice_budget_is_spent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A slice returns so the lane can run other Jobs; a queue that never
        # empties is continued by the next slice, not by this one.
        async def always_one(_provider_builder) -> int | None:
            await asyncio.sleep(0.01)
            return 7

        monkeypatch.setattr(printer_jobs, "dispatch_next", always_one)

        dispatched = asyncio.run(
            printer_jobs.drain_dispatch_queue(
                _unused_provider_builder, budget_seconds=0.05
            )
        )

        assert 1 <= dispatched < 50


class TestPrinter:
    def test_dispatch_rechecks_printer_grant_after_enqueue(
        self, db_session: Session
    ) -> None:
        printer = printer_config(
            "Revoked", moonraker_url="http://revoked", status=PrinterStatus.READY
        )
        user = user_config("revoked-user")
        db_session.add_all([printer, user])
        db_session.commit()
        db_session.refresh(printer)
        db_session.refresh(user)
        artifact = a_gcode_artifact(db_session, "revoked-cube")
        permission = PrinterPermission(
            printer_id=printer.id, user_id=user.id, role=PrinterRole.PRINT
        )
        db_session.add(permission)
        db_session.commit()
        job = build_print_job(
            db_session,
            artifact,
            printer_id=printer.id,
            remote_filename="revoked.gcode",
            state=PrintJobState.QUEUED,
            routing_strategy=RoutingStrategy.MANUAL,
            requested_by=user.id,
        )
        db_session.delete(permission)
        db_session.commit()

        assert asyncio.run(printer_jobs.dispatch_next(_unused_provider_builder)) is None
        db_session.refresh(job)
        assert job.state == PrintJobState.QUEUED
        assert job.blocked_reason == "printer_access_revoked"
