"""How AI Search Jobs key their subjects: by the row each one drives.

A leaf module, so the code that records search intent (a proposed generation,
a queued caption) can key its Job without importing the runners in
``search.jobs``, which import the recording side.
"""

from __future__ import annotations

_GENERATION = "index_generation"
_CAPTION = "subject_caption"


def generation_subject(generation_id: int) -> str:
    return f"{_GENERATION}/{generation_id}"


def caption_subject(caption_id: int) -> str:
    return f"{_CAPTION}/{caption_id}"


def _row_id(prefix: str, subject: str) -> int:
    kind, _, value = subject.partition("/")
    if kind != prefix or not value.isdigit():
        raise ValueError(f"not_a_{prefix}_subject:{subject}")
    return int(value)


def generation_of(subject: str) -> int:
    """The generation a ``search.generation`` subject names."""
    return _row_id(_GENERATION, subject)


def caption_of(subject: str) -> int:
    """The caption a ``search.caption`` subject names."""
    return _row_id(_CAPTION, subject)
