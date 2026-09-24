"""Names of the AI Search Job Definitions and how their subjects are keyed.

A leaf module: the code that records search intent (a library change, a
proposed generation, a queued caption, a settings change) nudges these
definitions by name, and ``search.jobs`` runs them. Keeping the names here
lets the recording side import them without importing the runners, which
import the recording side.
"""

from __future__ import annotations

PROJECT_DEFINITION = "search.project"
INDEX_DEFINITION = "search.index"
GENERATION_DEFINITION = "search.generation"
REPAIR_DEFINITION = "search.repair"
CAPTION_DEFINITION = "search.caption"
CAPTION_QUEUE_DEFINITION = "search.caption_queue"
EXPAND_DEFINITION = "search.expand"


def generation_subject(generation_id: int) -> str:
    return f"index_generation/{generation_id}"


def caption_subject(caption_id: int) -> str:
    return f"subject_caption/{caption_id}"


def subject_id(subject: str) -> int:
    """The row id a generation or caption subject names."""
    return int(subject.split("/", 1)[1])
