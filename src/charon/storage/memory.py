"""Process-local storage engine.

Correct for single-process deployments and for tests. Under Gunicorn/Uvicorn with
multiple workers each process keeps its own counters, so the effective limit is
multiplied by the worker count — use :mod:`charon.storage.redis` there.

Atomicity comes from a re-entrant lock held across the whole read-modify-write,
which is what makes the check-then-set sequence safe under threads.
"""

from __future__ import annotations

import threading
from typing import Any

from charon.algorithms import get_algorithm
from charon.clock import Clock, default_clock
from charon.storage.base import compose_key
from charon.types import RateLimitResult, Rule

__all__ = ["AsyncMemoryStore", "MemoryStore"]

#: ``(state, expires_at_ms)``
_Entry = tuple[Any, int]


class _Core:
    """Shared, lock-guarded state map behind both store flavours."""

    def __init__(self, *, namespace: str, clock: Clock, max_keys: int | None) -> None:
        self._namespace = namespace
        self._clock = clock
        self._max_keys = max_keys
        self._lock = threading.Lock()
        self._entries: dict[str, _Entry] = {}

    def evaluate(self, key: str, rule: Rule, cost: int) -> RateLimitResult:
        algorithm = get_algorithm(rule.algorithm)
        composed = compose_key(self._namespace, key, rule)
        now_ms = self._clock.now_ms()

        with self._lock:
            entry = self._entries.get(composed)
            state = entry[0] if entry is not None and entry[1] > now_ms else None

            next_state, result, ttl_ms = algorithm.apply(state, rule, now_ms, cost)

            if next_state is None:
                self._entries.pop(composed, None)
            else:
                self._entries[composed] = (next_state, now_ms + ttl_ms)
                if self._max_keys is not None and len(self._entries) > self._max_keys:
                    self._evict(now_ms)
        return result

    def reset(self, key: str, rule: Rule) -> None:
        with self._lock:
            self._entries.pop(compose_key(self._namespace, key, rule), None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def _evict(self, now_ms: int) -> None:
        """Drop expired entries; if that is not enough, drop the soonest to expire.

        Caller must hold the lock.
        """
        expired = [k for k, (_, expires_at) in self._entries.items() if expires_at <= now_ms]
        for key in expired:
            del self._entries[key]

        assert self._max_keys is not None
        overflow = len(self._entries) - self._max_keys
        if overflow <= 0:
            return
        by_expiry = sorted(self._entries.items(), key=lambda item: item[1][1])
        for key, _ in by_expiry[:overflow]:
            del self._entries[key]

    def __len__(self) -> int:
        return len(self._entries)


class MemoryStore:
    """Thread-safe in-memory backend (sync protocol).

    ``max_keys`` bounds memory for unbounded key spaces such as client IPs.
    """

    def __init__(
        self,
        *,
        namespace: str = "charon",
        clock: Clock = default_clock,
        max_keys: int | None = 100_000,
    ) -> None:
        self._core = _Core(namespace=namespace, clock=clock, max_keys=max_keys)

    def evaluate(self, key: str, rule: Rule, cost: int = 1) -> RateLimitResult:
        return self._core.evaluate(key, rule, cost)

    def reset(self, key: str, rule: Rule) -> None:
        self._core.reset(key, rule)

    def clear(self) -> None:
        self._core.clear()

    def close(self) -> None:
        self._core.clear()

    def __len__(self) -> int:
        return len(self._core)


class AsyncMemoryStore:
    """Async facade over :class:`MemoryStore`.

    The critical section is pure CPU work measured in microseconds, so it runs
    inline rather than on a thread pool.
    """

    def __init__(
        self,
        *,
        namespace: str = "charon",
        clock: Clock = default_clock,
        max_keys: int | None = 100_000,
    ) -> None:
        self._core = _Core(namespace=namespace, clock=clock, max_keys=max_keys)

    async def evaluate(self, key: str, rule: Rule, cost: int = 1) -> RateLimitResult:
        return self._core.evaluate(key, rule, cost)

    async def reset(self, key: str, rule: Rule) -> None:
        self._core.reset(key, rule)

    async def clear(self) -> None:
        self._core.clear()

    async def close(self) -> None:
        self._core.clear()

    def __len__(self) -> int:
        return len(self._core)
