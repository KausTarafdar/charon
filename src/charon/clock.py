"""Time source indirection so evaluations stay deterministic under test."""

from __future__ import annotations

import time
from typing import Protocol

__all__ = ["Clock", "MonotonicClock", "ManualClock", "default_clock"]


class Clock(Protocol):
    """Supplies the current time in milliseconds."""

    def now_ms(self) -> int: ...


class MonotonicClock:
    """Wall-clock milliseconds.

    Redis-backed evaluations use the wall clock so that every worker agrees on
    "now"; process-local monotonic time would diverge between workers.
    """

    __slots__ = ()

    def now_ms(self) -> int:
        return int(time.time() * 1000)


class ManualClock:
    """Test double whose time only moves when told to."""

    __slots__ = ("_now_ms",)

    def __init__(self, start_ms: int = 0) -> None:
        self._now_ms = start_ms

    def now_ms(self) -> int:
        return self._now_ms

    def advance(self, milliseconds: int) -> int:
        self._now_ms += milliseconds
        return self._now_ms

    def set(self, milliseconds: int) -> None:
        self._now_ms = milliseconds


default_clock: Clock = MonotonicClock()
