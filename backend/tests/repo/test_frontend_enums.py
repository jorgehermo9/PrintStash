"""The frontend's closed sets of background work match the backend's enums.

The client switches over a Job's kind, a lane and a derivative kind. Each is a
TypeScript union in ``frontend/src/types/models.ts`` spelling out the backend
enum by hand; if one drifts, the client either rejects a real value at the type
level or keeps a branch for one that no longer exists.
"""

from __future__ import annotations

import re
from enum import Enum

import pytest

from app.db.models import DerivativeKind, JobKind, LaneName
from tests.paths import REPO_ROOT

MODELS_TS = REPO_ROOT / "frontend/src/types/models.ts"


def _union(name: str) -> set[str]:
    source = MODELS_TS.read_text()
    match = re.search(rf"export type {name} =(.*?);", source, re.S)
    assert match is not None, f"{name} is not declared in models.ts"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


@pytest.mark.parametrize("enum", [JobKind, LaneName, DerivativeKind], ids=str)
def test_the_union_lists_exactly_the_enums_values(enum: type[Enum]) -> None:
    assert _union(enum.__name__) == {member.value for member in enum}
