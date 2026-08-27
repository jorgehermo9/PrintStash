"""Starring a model, and the favorites filter that reads those stars.

A star is per user, so the same model is a favorite for one person and not for another,
and `?favorites=true` must answer from the caller's own stars. Starring is also a write
against a model the caller may not be allowed to see: it goes through the same
collection-role check as reading the model, or a star becomes a way to confirm that a
private model exists.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db.models import Collection, CollectionPermission, CollectionRole, Model, User


@pytest.fixture
def collection(db_session: Session) -> Collection:
    row = Collection(name="Shared", slug="shared", path="shared")
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


@pytest.fixture
def viewer(db_session: Session, make_user, headers_for, collection: Collection):
    """A user with VIEW on the shared collection, and their bearer headers."""

    def build(username: str) -> dict[str, str]:
        user: User = make_user(username)
        db_session.add(
            CollectionPermission(
                user_id=user.id, collection_id=collection.id, role=CollectionRole.VIEW
            )
        )
        db_session.commit()
        return headers_for(user)

    return build


@pytest.fixture
def model(db_session: Session, collection: Collection) -> Model:
    row = Model(
        name="Starred", slug="starred", hash="1" * 64, collection_id=collection.id
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


class TestStarModel:
    def test_reports_the_model_as_starred(
        self, client: TestClient, viewer, model: Model
    ) -> None:
        headers = viewer("starrer")

        response = client.put(f"/api/v1/models/{model.id}/star", headers=headers)

        assert response.status_code == 200, response.text
        assert response.json() == {"model_id": model.id, "starred": True}

    def test_is_idempotent(self, client: TestClient, viewer, model: Model) -> None:
        headers = viewer("double-starrer")
        client.put(f"/api/v1/models/{model.id}/star", headers=headers)

        again = client.put(f"/api/v1/models/{model.id}/star", headers=headers)

        assert again.status_code == 200, again.text
        assert again.json()["starred"] is True

    def test_shows_on_the_model_detail(
        self, client: TestClient, viewer, model: Model
    ) -> None:
        headers = viewer("detail-reader")
        client.put(f"/api/v1/models/{model.id}/star", headers=headers)

        detail = client.get(f"/api/v1/models/{model.id}", headers=headers)

        assert detail.status_code == 200, detail.text
        assert detail.json()["starred"] is True

    def test_requires_read_access_to_the_model(
        self, client: TestClient, db_session: Session, user_headers
    ) -> None:
        headers = user_headers("no-access")
        private = Collection(name="Private", slug="private", path="private")
        db_session.add(private)
        db_session.commit()
        db_session.refresh(private)
        hidden = Model(
            name="Private model",
            slug="private-model",
            hash="3" * 64,
            collection_id=private.id,
        )
        db_session.add(hidden)
        db_session.commit()
        db_session.refresh(hidden)

        response = client.put(f"/api/v1/models/{hidden.id}/star", headers=headers)

        assert response.status_code == 403, response.text

    def test_reports_a_model_that_does_not_exist(
        self, client: TestClient, auth_headers
    ) -> None:
        response = client.put("/api/v1/models/999999/star", headers=auth_headers)

        assert response.status_code == 404, response.text

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, model: Model
    ) -> None:
        assert client.put(f"/api/v1/models/{model.id}/star").status_code == 401


class TestUnstarModel:
    def test_reports_the_model_as_unstarred(
        self, client: TestClient, viewer, model: Model
    ) -> None:
        headers = viewer("unstarrer")
        client.put(f"/api/v1/models/{model.id}/star", headers=headers)

        response = client.delete(f"/api/v1/models/{model.id}/star", headers=headers)

        assert response.status_code == 200, response.text
        assert response.json()["starred"] is False

    def test_reports_a_model_that_does_not_exist(
        self, client: TestClient, auth_headers
    ) -> None:
        response = client.delete("/api/v1/models/999999/star", headers=auth_headers)

        assert response.status_code == 404, response.text

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, model: Model
    ) -> None:
        assert client.delete(f"/api/v1/models/{model.id}/star").status_code == 401


class TestFavoritesFilter:
    def test_lists_only_starred_models(
        self,
        client: TestClient,
        db_session: Session,
        viewer,
        collection: Collection,
        model: Model,
    ) -> None:
        headers = viewer("favoriter")
        db_session.add(
            Model(
                name="Plain", slug="plain", hash="2" * 64, collection_id=collection.id
            )
        )
        db_session.commit()
        client.put(f"/api/v1/models/{model.id}/star", headers=headers)

        favorites = client.get(
            "/api/v1/models?favorites=true&limit=500", headers=headers
        )

        assert favorites.status_code == 200, favorites.text
        assert [row["id"] for row in favorites.json()] == [model.id]

    def test_marks_the_rows_it_returns_as_starred(
        self, client: TestClient, viewer, model: Model
    ) -> None:
        headers = viewer("flag-reader")
        client.put(f"/api/v1/models/{model.id}/star", headers=headers)

        favorites = client.get("/api/v1/models?favorites=true", headers=headers).json()

        assert favorites[0]["starred"] is True

    def test_is_empty_once_the_star_is_removed(
        self, client: TestClient, viewer, model: Model
    ) -> None:
        headers = viewer("unfavoriter")
        client.put(f"/api/v1/models/{model.id}/star", headers=headers)
        client.delete(f"/api/v1/models/{model.id}/star", headers=headers)

        favorites = client.get("/api/v1/models?favorites=true", headers=headers)

        assert favorites.json() == []

    def test_does_not_show_another_users_stars(
        self, client: TestClient, viewer, model: Model
    ) -> None:
        mine = viewer("my-stars")
        theirs = viewer("their-stars")
        client.put(f"/api/v1/models/{model.id}/star", headers=theirs)

        favorites = client.get("/api/v1/models?favorites=true", headers=mine)

        assert favorites.json() == [], "a star belongs to the user who made it"
