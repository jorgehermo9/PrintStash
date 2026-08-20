"""Public G-code metadata and binary-container API."""

from . import bgcode
from .bgcode import (
    MAGIC,
    THUMBNAIL_FORMATS,
    is_bgcode,
    is_valid_container,
    iter_thumbnails,
    read_metadata_text,
)
from .models import (
    GcodeMetadata,
    LegacyGcodeMetadata,
    LegacyMaterialRequirement,
    MaterialRequirement,
    to_legacy_dict,
)
from .parser import parse, parse_duration

__all__ = [
    "GcodeMetadata",
    "LegacyGcodeMetadata",
    "LegacyMaterialRequirement",
    "MAGIC",
    "MaterialRequirement",
    "THUMBNAIL_FORMATS",
    "bgcode",
    "is_bgcode",
    "is_valid_container",
    "iter_thumbnails",
    "parse",
    "parse_duration",
    "read_metadata_text",
    "to_legacy_dict",
]
