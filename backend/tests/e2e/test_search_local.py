"""Explicit HTTPS acquisition to local ONNX indexing and semantic HTTP search."""

import asyncio

import pytest

from app.core.config import _overlay
from app.db.session import get_session_factory
from app.modules.inference import model_cache
from app.modules.inference.query import close_queries
from app.modules.inference.worker_pool import pool
from app.modules.search.model_warmup import ModelWarmup
from tests.e2e._jobs import settle
from tests.factories.embeddings import text_embedding_assets
from tests.fixtures.model_acquisition import model_host as _model_host  # noqa: F401

pytestmark = pytest.mark.asyncio


class TestLocalSearch:
    async def test_keeps_search_available_during_restart_warmup(
        self, api, superuser_headers, tmp_path, monkeypatch
    ):
        cache = tmp_path / "cached-models"
        model = model_cache.inspect(text_embedding_assets(cache / "text"))
        monkeypatch.setitem(_overlay, "embedding_cache_dir", cache)
        monkeypatch.setitem(_overlay, "embedding_local_model_dir", "")
        response = await api.put(
            "/api/v1/config/ai-search",
            headers=superuser_headers,
            json={"enabled": True, "local_models_enabled": True},
        )
        assert response.status_code == 200, response.text
        response = await api.post(
            "/api/v1/documents",
            headers=superuser_headers,
            json={"name": "red", "body": "red assembly"},
        )
        assert response.status_code == 201, response.text
        document_id = response.json()["id"]
        response = await api.post(
            "/api/v1/config/ai-search/generations",
            headers=superuser_headers,
            json={"local_model_id": model.id, "index_backend": "numpy"},
        )
        assert response.status_code == 202, response.text
        generation_id = response.json()["id"]
        processor = ModelWarmup(get_session_factory())
        try:
            # The projection and the generation's build Job run to completion.
            await asyncio.to_thread(settle)
            response = await api.get(
                "/api/v1/config/ai-search/generations", headers=superuser_headers
            )
            generation = next(
                row for row in response.json() if row["id"] == generation_id
            )
            assert generation["state"] == "active", generation
            close_queries()
            pool.close()
            response = await api.get(
                "/api/v1/search",
                headers=superuser_headers,
                params={"q": "red", "legs[]": ["lexical", "semantic_text"]},
            )
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["leg_errors"] == {"semantic_text": "embedding_model_warming"}
            assert any(item["subject_id"] == document_id for item in result["items"])
            assert await asyncio.to_thread(processor.work_one)
            response = await api.get(
                "/api/v1/search",
                headers=superuser_headers,
                params={"q": "red", "legs[]": "semantic_text"},
            )
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["leg_errors"] == {}
            assert any(item["subject_id"] == document_id for item in result["items"])
            assert any(
                evidence["leg"] == "semantic_text"
                for item in result["items"]
                if item["subject_id"] == document_id
                for evidence in item["evidence"]
            )
        finally:
            processor.stop()
            close_queries()
            pool.close()

    async def test_activates_an_acquired_text_model(
        self, api, superuser_headers, model_host
    ):
        fake, cache = model_host
        try:
            response = await api.put(
                "/api/v1/config/ai-search",
                headers=superuser_headers,
                json={
                    "enabled": True,
                    "local_models_enabled": True,
                    "download_enabled": True,
                    "query_timeout_seconds": 10,
                },
            )
            assert response.status_code == 200, response.text
            response = await api.post(
                "/api/v1/documents",
                headers=superuser_headers,
                json={"name": "red", "body": "red assembly"},
            )
            assert response.status_code == 201, response.text
            document_id = response.json()["id"]
            response = await api.post(
                f"/api/v1/inference/models/{fake.entry.id}/download",
                headers=superuser_headers,
            )
            assert response.status_code == 202, response.text
            job_id = response.json()["job_id"]
            await asyncio.to_thread(settle)
            job = await api.get(f"/api/v1/jobs/{job_id}", headers=superuser_headers)
            assert job.json()["state"] == "completed", job.json()
            assert (cache / fake.entry.id / "manifest.json").exists()
            response = await api.post(
                "/api/v1/config/ai-search/generations",
                headers=superuser_headers,
                json={"local_model_id": fake.entry.id, "index_backend": "numpy"},
            )
            assert response.status_code == 202, response.text
            generation_id = response.json()["id"]
            await asyncio.to_thread(settle)
            response = await api.get(
                "/api/v1/config/ai-search/generations", headers=superuser_headers
            )
            generation = next(
                row for row in response.json() if row["id"] == generation_id
            )
            assert generation["state"] == "active", generation
            # Select only the dense leg. This original token table proves
            # native wiring; language quality has a separate real-model corpus.
            response = await api.get(
                "/api/v1/search",
                headers=superuser_headers,
                params={"q": "red", "legs[]": "semantic_text"},
            )
            assert response.status_code == 200, response.text
            result = response.json()
            assert any(
                item["subject_id"] == document_id and item["subject_type"] == "document"
                for item in result["items"]
            ), result
            assert result["generations"]
            assert result["leg_errors"] == {}
            assert any(
                evidence["leg"] == "semantic_text"
                for item in result["items"]
                for evidence in item["evidence"]
            )
            response = await api.delete(
                f"/api/v1/inference/models/{fake.entry.id}", headers=superuser_headers
            )
            assert response.status_code == 409
            assert (cache / fake.entry.id / "model.onnx").exists() or (
                cache / fake.entry.id / "text.onnx"
            ).exists()
        finally:
            close_queries()
            pool.close()
