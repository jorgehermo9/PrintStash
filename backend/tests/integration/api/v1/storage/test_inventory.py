"""Administrative inventory is safe to read; cleanup is explicit and audited."""

import pytest
from sqlmodel import select

from app.core.config import _overlay
from app.db.models import AuditLog
from app.modules.storage.storage_backend.runtime import get_backend
from tests.factories import store_owned_bytes


class TestStorageInventory:
    def test_reports_current_capacity(self, client, auth_headers):
        response = client.get("/api/v1/storage/inventory", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["inventory"]["volumes"]
        assert response.json()["forecast"]["status"] == "insufficient_data"

    def test_denies_non_admin_inventory(self, client, user_headers):
        response = client.get("/api/v1/storage/inventory", headers=user_headers())
        assert response.status_code == 403

    def test_persists_explicit_measurement(self, client, auth_headers):
        assert (
            client.post(
                "/api/v1/storage/inventory/sample", headers=auth_headers
            ).status_code
            == 200
        )
        assert (
            len(
                client.get("/api/v1/storage/inventory", headers=auth_headers).json()[
                    "history"
                ]
            )
            == 1
        )

    def test_audits_explicit_cleanup(self, client, auth_headers, db_session):
        response = client.post(
            "/api/v1/storage/inventory/cleanup-staging", headers=auth_headers
        )
        assert response.status_code == 200
        assert response.json()["files_removed"] == 0
        assert (
            db_session.exec(
                select(AuditLog).where(
                    AuditLog.action == "storage.cleanup_expired_staging"
                )
            ).first()
            is not None
        )

    @pytest.mark.parametrize(
        "endpoint",
        [
            "/api/v1/storage/inventory/cleanup-staging",
            "/api/v1/storage/inventory/cleanup-cache",
        ],
    )
    def test_denies_non_admin_cleanup(self, client, user_headers, endpoint):
        assert client.post(endpoint, headers=user_headers()).status_code == 403

    def test_bounds_drilldown_page_size(self, client, auth_headers):
        assert (
            client.get(
                "/api/v1/storage/inventory/models?limit=101", headers=auth_headers
            ).status_code
            == 422
        )

    def test_reports_privacy_safe_capacity_activity(
        self, client, auth_headers, make_capacity_reservation
    ):
        make_capacity_reservation(operation_id="backup:private-backup-id")

        response = client.get(
            "/api/v1/storage/inventory/activity", headers=auth_headers
        )

        assert response.status_code == 200
        assert response.json()["active_reservations"][0]["operation_kind"] == "backup"
        assert "private-backup-id" not in response.text

    def test_previews_cleanup_without_deleting(self, client, auth_headers):
        response = client.get(
            "/api/v1/storage/inventory/cleanup-opportunities", headers=auth_headers
        )

        assert response.status_code == 200
        assert [item["owner"] for item in response.json()] == [
            "staging",
            "trash",
            "backups",
            "cache",
        ]
        assert all("key" not in item and "path" not in item for item in response.json())

    def test_clears_only_receipt_verified_derived_cache(
        self, client, auth_headers, db_session
    ):
        backend = get_backend()
        cache_key = backend.stl_cache_key("a" * 64)
        thumbnail_key = backend.thumbnail_key(987654)
        store_owned_bytes(
            db_session,
            backend,
            cache_key,
            b"cache",
            object_kind="derived_stl_cache",
        )
        store_owned_bytes(
            db_session,
            backend,
            thumbnail_key,
            b"thumbnail",
            object_kind="thumbnail",
        )

        preview = client.get(
            "/api/v1/storage/inventory/cleanup-opportunities", headers=auth_headers
        )
        cache = next(item for item in preview.json() if item["owner"] == "cache")
        assert cache == {
            "owner": "cache",
            "candidate_count": 1,
            "candidate_bytes": 5,
            "action": "cleanup_derived_cache",
            "available": True,
        }
        assert backend.exists(cache_key)

        response = client.post(
            "/api/v1/storage/inventory/cleanup-cache", headers=auth_headers
        )

        assert response.status_code == 200
        assert response.json()["enqueued"] == 1
        assert not backend.exists(cache_key)
        assert backend.exists(thumbnail_key)
        assert (
            db_session.exec(
                select(AuditLog).where(
                    AuditLog.action == "storage.cleanup_derived_cache"
                )
            ).first()
            is not None
        )

    def test_denies_upload_when_headroom_unavailable(
        self, client, auth_headers, monkeypatch
    ):
        monkeypatch.setitem(_overlay, "storage_min_free_bytes", 10**18)
        response = client.post(
            "/api/v1/ingest/model",
            headers=auth_headers,
            files={"file": ("model.stl", b"solid x\nendsolid x")},
        )
        assert response.status_code == 507
        assert response.json()["detail"] == "storage_capacity_exceeded"
        assert response.json()["capacity"]["headroom_bytes"] == 10**18
        assert response.json()["capacity"]["required_bytes"] > 0
        assert response.headers["retry-after"] == "60"

    def test_resets_headroom_to_environment_default(self, client, auth_headers):
        original = client.get("/api/v1/config", headers=auth_headers).json()[
            "storage_min_free_bytes"
        ]
        updated = client.put(
            "/api/v1/config",
            headers=auth_headers,
            json={"storage_min_free_bytes": 1234},
        )
        assert updated.status_code == 200
        assert (
            client.get("/api/v1/config", headers=auth_headers).json()[
                "storage_min_free_bytes"
            ]
            == 1234
        )
        assert (
            client.put(
                "/api/v1/config",
                headers=auth_headers,
                json={"storage_min_free_bytes": -1},
            ).status_code
            == 200
        )
        assert (
            client.get("/api/v1/config", headers=auth_headers).json()[
                "storage_min_free_bytes"
            ]
            == original
        )


class TestCollectionInventory:
    def test_hides_inaccessible_collection_storage(
        self,
        client,
        make_user,
        headers_for,
        grant_role,
        make_collection,
        make_model,
        make_file,
    ):
        from app.db.models import CollectionRole

        reader = make_user()
        visible = make_collection("Visible")
        private = make_collection("Private")
        grant_role(reader, visible, CollectionRole.VIEW)
        make_file(make_model(collection_id=visible.id), size_bytes=30)
        make_file(make_model(collection_id=private.id), size_bytes=9000)

        response = client.get(
            "/api/v1/storage/inventory/collections", headers=headers_for(reader)
        )

        assert response.status_code == 200
        assert response.json() == [
            {
                "collection_id": visible.id,
                "name": "Visible",
                "logical_bytes": 30,
                "external_bytes": 0,
                "model_count": 1,
            }
        ]

    def test_paginates_collection_storage(
        self, client, auth_headers, make_collection, make_model, make_file
    ):
        small = make_collection("Small")
        large = make_collection("Large")
        make_file(make_model(collection_id=small.id), size_bytes=10)
        make_file(make_model(collection_id=large.id), size_bytes=20)

        response = client.get(
            "/api/v1/storage/inventory/collections?offset=1&limit=1",
            headers=auth_headers,
        )

        assert [row["collection_id"] for row in response.json()] == [small.id]

    def test_filters_models_by_authorized_collection(
        self, client, auth_headers, make_collection, make_model, make_file
    ):
        selected = make_collection("Selected")
        model = make_model(collection_id=selected.id)
        make_file(model, size_bytes=11)
        make_file(make_model(), size_bytes=999)

        response = client.get(
            f"/api/v1/storage/inventory/models?collection_id={selected.id}",
            headers=auth_headers,
        )

        assert response.json() == [
            {"model_id": model.id, "name": model.name, "logical_bytes": 11}
        ]

    def test_excludes_trashed_models_from_collection_storage(
        self, client, auth_headers, make_collection, make_model, make_file
    ):
        collection = make_collection()
        make_file(make_model(collection_id=collection.id, trashed=True), size_bytes=50)

        response = client.get(
            "/api/v1/storage/inventory/collections", headers=auth_headers
        )

        assert response.json() == []
