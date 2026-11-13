from __future__ import annotations

import time

from charon.clock import ManualClock, MonotonicClock


def test_monotonic_clock() -> None:
    clock = MonotonicClock()
    t1 = clock.now_ms()
    time.sleep(0.01)
    t2 = clock.now_ms()
    assert t2 >= t1 + 8  # allow slight timing variance


def test_manual_clock() -> None:
    clock = ManualClock(start_ms=1000)
    assert clock.now_ms() == 1000

    clock.advance(500)
    assert clock.now_ms() == 1500

    clock.set(3000)
    assert clock.now_ms() == 3000
