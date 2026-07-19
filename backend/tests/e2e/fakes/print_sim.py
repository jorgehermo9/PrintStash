"""Wall-clock print simulation shared by the mock printer emulators.

State is a pure function of elapsed monotonic time scaled by a speed factor —
no background task, no lifespan hook — so it runs unchanged under uvicorn's
``lifespan="off"`` ``start_server`` helper. Each emulator wraps this in its
own protocol-shaped status payload.
"""

from __future__ import annotations

import time
from typing import Optional

# Terminal/active states a real printer transport reports.
STANDBY = "standby"
PRINTING = "printing"
PAUSED = "paused"
COMPLETE = "complete"
CANCELLED = "cancelled"
ERROR = "error"


class PrintSim:
    def __init__(self, *, total_mm: float, total_seconds: float, print_seconds: float) -> None:
        self.total_mm = total_mm
        self.total_seconds = total_seconds  # reported print_duration estimate
        # Sim "printer seconds" advance this many times faster than wall time so a
        # print reporting ``total_seconds`` completes in ``print_seconds`` real time.
        self.speed = total_seconds / max(print_seconds, 1e-3)

        self.state = STANDBY
        self.filename = ""
        self.message = ""
        self._started: Optional[float] = None  # monotonic when last (re)started
        self._accumulated = 0.0  # sim-seconds accrued before a pause

    def elapsed(self) -> float:
        if self.state == PRINTING and self._started is not None:
            return self._accumulated + (time.monotonic() - self._started) * self.speed
        return self._accumulated

    def progress(self) -> float:
        """Fraction complete, latching PRINTING -> COMPLETE once it reaches 1.0.

        This is the only place the terminal transition happens, so it fires
        from whatever polls/pushes status.
        """
        elapsed = self.elapsed()
        if self.state == PRINTING and self.total_seconds and elapsed / self.total_seconds >= 1.0:
            self.state = COMPLETE
            self._accumulated = self.total_seconds
            self._started = None
        if self.state == COMPLETE:
            return 1.0
        return min(elapsed / self.total_seconds, 1.0) if self.total_seconds else 0.0

    def filament_used(self) -> float:
        return round(self.total_mm * self.progress(), 4)

    def is_active(self) -> bool:
        return self.state in (PRINTING, PAUSED)

    def start(self, filename: str) -> None:
        self.filename = filename
        self.message = ""
        self.state = PRINTING
        self._started = time.monotonic()
        self._accumulated = 0.0

    def pause(self) -> None:
        if self.state == PRINTING:
            self._accumulated = self.elapsed()
            self._started = None
            self.state = PAUSED

    def resume(self) -> None:
        if self.state == PAUSED:
            self._started = time.monotonic()
            self.state = PRINTING

    def cancel(self) -> None:
        if self.state in (PRINTING, PAUSED):
            self._accumulated = self.elapsed()
            self._started = None
            self.state = CANCELLED

    def fail(self, message: str = "simulated failure") -> None:
        """Force an error/disconnect mid-print (network drop, provider fault)."""
        self._accumulated = self.elapsed()
        self._started = None
        self.state = ERROR
        self.message = message
