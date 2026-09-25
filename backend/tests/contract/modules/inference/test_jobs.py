"""A model download is a Job that records and cancels a real HTTPS transfer.

The transfer itself (verification, atomic install) is ``test_model_acquisition``.
This defends the Job around it: one download at a time, its failure recorded
with a safe code and no partial files, and a cancel through the Jobs API that
stops the transfer mid-flight and leaves nothing installed.
"""

import contextvars
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core.errors import OperationError
from app.db.models import Job, JobKind
from app.db.session import get_session_factory
from app.modules.inference import jobs as inference_jobs
from app.modules.search.configuration import update
from app.modules.work import service as work_service
from app.modules.work.jobs import jobs
from app.schemas.inference import SearchSettings
from tests.factories import build_user
from tests.fixtures.model_acquisition import model_host as _model_host  # noqa: F401


@pytest.fixture
def download_case(threaded_hub_db, model_host):
    fake, cache = model_host
    with get_session_factory().scoped_session() as session:
        actor = build_user(session, superuser=True)
        update(
            session,
            SearchSettings(
                enabled=True, local_models_enabled=True, download_enabled=True
            ),
        )
        session.commit()
        yield session, actor, fake, cache


def _request(session, actor, key: str) -> str:
    """What the download route does: record the Job, commit, then nudge."""
    from app.modules.work import nudge

    job_id = inference_jobs.request(session, actor, key)
    session.commit()
    nudge(JobKind.INFERENCE_MODEL_DOWNLOAD)
    return job_id


class TestModelDownload:
    def test_installs_the_model(self, download_case, work_engine) -> None:
        session, actor, fake, cache = download_case
        job_id = _request(session, actor, fake.entry.id)

        work_engine.drain()

        status = jobs.get(job_id)
        assert status is not None and status.state == "completed"
        assert (cache / fake.entry.id / "manifest.json").exists()

    def test_records_a_failed_transfer(self, download_case, work_engine) -> None:
        session, actor, fake, cache = download_case
        fake.fault = "corrupt"
        job_id = _request(session, actor, fake.entry.id)

        work_engine.drain()

        status = jobs.get(job_id)
        assert status is not None
        assert (status.state, status.error) == (
            "failed",
            "embedding_asset_digest_mismatch",
        )
        assert status.result == {"model_id": fake.entry.id, "error_code": status.error}
        assert not (cache / fake.entry.id).exists()
        assert not list(cache.glob(".download-*"))

    def test_one_download_runs_at_a_time(self, download_case) -> None:
        session, actor, fake, _ = download_case
        _request(session, actor, fake.entry.id)

        with pytest.raises(OperationError, match="embedding_download_busy"):
            _request(session, actor, fake.entry.id)

    def test_every_download_claims_the_same_subject(self, download_case) -> None:
        # One subject for all models, so the active-subject index (not a check
        # two racing requests could both pass) keeps downloads one at a time.
        session, actor, fake, _ = download_case
        job_id = _request(session, actor, fake.entry.id)

        with get_session_factory().scoped_session() as rows:
            job = rows.get(Job, job_id)
            assert job is not None
            assert job.subject_key == inference_jobs.DOWNLOAD_SUBJECT
        status = jobs.get(job_id)
        assert status is not None and status.result == {"model_id": fake.entry.id}

    def test_a_cancel_stops_the_transfer(self, download_case, work_engine) -> None:
        session, actor, fake, cache = download_case
        entered, release = threading.Event(), threading.Event()

        def hold_reply():
            entered.set()
            assert release.wait(5)

        fake.before_reply = hold_reply
        job_id = _request(session, actor, fake.entry.id)
        with ThreadPoolExecutor(1) as executor:
            # In this test's context, so the drain sees this test's database.
            running = executor.submit(contextvars.copy_context().run, work_engine.drain)
            try:
                assert entered.wait(5)
                work_service.cancel(job_id, actor=actor)
            finally:
                release.set()
            running.result(timeout=10)

        status = jobs.get(job_id)
        assert status is not None and status.state == "cancelled"
        assert not (cache / fake.entry.id).exists()
        assert not list(cache.glob(".download-*"))
