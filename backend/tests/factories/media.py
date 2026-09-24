"""Thumbnail receipts use the Artifact's actual source identity."""

from typing import Any

from sqlmodel import Session

from app.db.models import File, ThumbnailGeneration
from app.modules.media.thumbnail_generations import recipe_fingerprint
from tests.factories._support import save


def build_thumbnail_generation(
    session: Session, file: File, **overrides: Any
) -> ThumbnailGeneration:
    defaults = {
        "file_id": file.id,
        "source_sha256": file.sha256,
        "recipe_fingerprint": recipe_fingerprint(),
    }
    return save(session, ThumbnailGeneration(**(defaults | overrides)))
