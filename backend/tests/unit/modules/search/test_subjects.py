"""How AI Search Jobs key their subjects.

A generation's Job and a caption's Job are keyed by the row they drive, so a
subject must read back as the same row id, and a subject of any other shape is
refused rather than read as some row.
"""

from __future__ import annotations

import pytest

from app.modules.search.subjects import (
    caption_of,
    caption_subject,
    generation_of,
    generation_subject,
)


class TestSubjects:
    def test_a_generation_subject_names_its_row(self) -> None:
        assert generation_subject(12) == "index_generation/12"
        assert generation_of(generation_subject(12)) == 12

    def test_a_caption_subject_names_its_row(self) -> None:
        assert caption_subject(7) == "subject_caption/7"
        assert caption_of(caption_subject(7)) == 7

    @pytest.mark.parametrize(
        "subject",
        ["subject_caption/7", "index_generation/", "index_generation/x", "12"],
    )
    def test_refuses_what_is_not_a_generation(self, subject: str) -> None:
        with pytest.raises(ValueError, match="not_a_index_generation_subject"):
            generation_of(subject)

    def test_refuses_a_generation_as_a_caption(self) -> None:
        with pytest.raises(ValueError, match="not_a_subject_caption_subject"):
            caption_of(generation_subject(3))
