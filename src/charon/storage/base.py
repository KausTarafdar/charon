"""Storage engine interface.

A backend owns *where* the counter state lives and *how* the read-modify-write is
made atomic. It delegates *what* the transition is to the algorithm strategy.

Backends may implement the sync protocol, the async protocol, or both;
:class:`~charon.engine.Limiter` picks the right one per call site.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from charon.types import RateLimitResult, Rule

__all__ = ["AsyncStorageBackend", "StorageBackend", "SyncStorageBackend", "compose_key"]


@runtime_checkable
class SyncStorageBackend(Protocol):
    """Blocking storage engine."""

    def evaluate(self, key: str, rule: Rule, cost: int = 1) -> RateLimitResult:
        """Atomically fold ``cost`` into ``key``'s state and return the verdict."""
        ...

    def reset(self, key: str, rule: Rule) -> None:
        """Drop all state for ``key`` under ``rule``."""
        ...

    def close(self) -> None: ...


@runtime_checkable
class AsyncStorageBackend(Protocol):
    """Non-blocking storage engine, matching the spec's ``StorageBackend``."""

    async def evaluate(self, key: str, rule: Rule, cost: int = 1) -> RateLimitResult: ...

    async def reset(self, key: str, rule: Rule) -> None: ...

    async def close(self) -> None: ...


#: Alias kept for readability at call sites that accept either flavour.
StorageBackend = object


def compose_key(namespace: str, key: str, rule: Rule) -> str:
    """Namespace a caller-supplied key by rule, so distinct rules never collide.

    Kept in one place because the Redis and in-memory backends must agree on it.
    """
    return f"{namespace}:{rule.name}:{rule.algorithm.value}:{key}"
