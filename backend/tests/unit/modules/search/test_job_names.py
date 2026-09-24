"""How AI Search Jobs name their subjects.

A generation's Job and a caption's Job are keyed by the row they drive, so the
subject a source renders must read back as the same row id.
"""

from __future__ import annotations

from app.modules.search.job_names import (
    caption_subject,
    generation_subject,
    subject_id,
)


class TestSubjects:
    def test_a_generation_subject_names_its_row(self) -> None:
        assert generation_subject(12) == "index_generation/12"
        assert subject_id(generation_subject(12)) == 12

    def test_a_caption_subject_names_its_row(self) -> None:
        assert caption_subject(7) == "subject_caption/7"
        assert subject_id(caption_subject(7)) == 7
