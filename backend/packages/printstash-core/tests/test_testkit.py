from __future__ import annotations

from printstash_core_testkit import (
    COMPLETE,
    PAUSED,
    PRINTING,
    PrintSim,
    Received,
    Recorder,
)


def test_print_sim_uses_injected_clock() -> None:
    now = [10.0]
    sim = PrintSim(
        total_mm=100.0,
        total_seconds=20.0,
        print_seconds=10.0,
        monotonic=lambda: now[0],
    )
    sim.start("part.gcode")
    now[0] = 12.5
    assert sim.state == PRINTING
    assert sim.progress() == 0.25
    sim.pause()
    assert sim.state == PAUSED
    now[0] = 20.0
    assert sim.progress() == 0.25
    sim.resume()
    now[0] = 27.5
    assert sim.progress() == 1.0
    assert sim.state == COMPLETE


def test_recorder_returns_copies_and_counts_calls() -> None:
    recorder = Recorder()
    received = Received("webhook", "POST", "/event", {})
    recorder.record(received)
    items = recorder.all()
    items.clear()
    assert recorder.for_target("webhook") == [received]
    assert recorder.bump("retry") == 1
    assert recorder.bump("retry") == 2
