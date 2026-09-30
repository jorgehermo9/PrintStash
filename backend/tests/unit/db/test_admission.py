"""The gate on opening application sessions while database names switch."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.core.errors import ErrorKind, OperationError
from app.db import admission


@pytest.fixture(autouse=True)
def _open_gate() -> Iterator[None]:
    admission.release()
    yield
    admission.release()


class TestAdmission:
    def test_admits_while_the_gate_is_open(self) -> None:
        admission.require()

    def test_refuses_as_busy_while_fenced(self) -> None:
        admission.fence()

        with pytest.raises(OperationError) as error:
            admission.require()

        assert (error.value.detail, error.value.kind) == (
            "database_activation_in_progress",
            ErrorKind.BUSY,
        )

    def test_admits_again_once_released(self) -> None:
        admission.fence()

        admission.release()

        admission.require()
