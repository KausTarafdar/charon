"""Pluggable storage engines."""

from __future__ import annotations

from charon.storage.base import (
    AsyncStorageBackend,
    StorageBackend,
    SyncStorageBackend,
    compose_key,
)
from charon.storage.memory import AsyncMemoryStore, MemoryStore

__all__ = [
    "AsyncMemoryStore",
    "AsyncRedisStore",
    "AsyncStorageBackend",
    "MemoryStore",
    "RedisStore",
    "StorageBackend",
    "SyncStorageBackend",
    "compose_key",
]


def __getattr__(name: str) -> object:
    """Import the Redis backends lazily so the extra stays optional."""
    if name in ("RedisStore", "AsyncRedisStore"):
        from charon.storage import redis as _redis  # noqa: PLC0415

        return getattr(_redis, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
