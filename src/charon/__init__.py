"""Charon — a framework-agnostic rate limiter SDK for Python.

Typical use::

    from charon import Limiter, MemoryStore, Rule

    limiter = Limiter(MemoryStore(), rule=Rule.parse("100/minute"))
    result = limiter.check("user:42")
    if not result.is_allowed:
        ...  # 429, Retry-After: result.retry_after_seconds

Transport adapters and the Redis backends are resolved lazily through
:func:`__getattr__`, so importing this package never pulls in a web framework or
the optional ``redis`` dependency.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from charon.algorithms import (
    Algorithm,
    LeakyBucket,
    SlidingWindowCounter,
    TokenBucket,
    get_algorithm,
    register_algorithm,
)
from charon.clock import Clock, ManualClock, MonotonicClock
from charon.engine import Limiter
from charon.errors import (
    CharonError,
    ConfigurationError,
    RateLimitExceeded,
    StorageError,
)
from charon.keys import (
    ApiKey,
    AuthenticatedUser,
    ClientIP,
    FirstOf,
    KeyExtractor,
    RequestInfo,
    Static,
    default_extractor,
)
from charon.storage import (
    AsyncMemoryStore,
    AsyncStorageBackend,
    MemoryStore,
    SyncStorageBackend,
)
from charon.types import AlgorithmName, FailureMode, RateLimitResult, Rule

if TYPE_CHECKING:  # import-time cost avoided at runtime, names still resolve for type checkers
    from charon.adapters.asgi import RateLimitMiddleware as ASGIRateLimitMiddleware
    from charon.adapters.wsgi import RateLimitMiddleware as WSGIRateLimitMiddleware
    from charon.storage.redis import AsyncRedisStore, RedisStore

__version__ = "0.1.0"

__all__ = [
    # engine
    "Limiter",
    # data interfaces
    "AlgorithmName",
    "FailureMode",
    "RateLimitResult",
    "Rule",
    # algorithms
    "Algorithm",
    "LeakyBucket",
    "SlidingWindowCounter",
    "TokenBucket",
    "get_algorithm",
    "register_algorithm",
    # storage
    "AsyncMemoryStore",
    "AsyncRedisStore",
    "AsyncStorageBackend",
    "MemoryStore",
    "RedisStore",
    "SyncStorageBackend",
    # key extraction
    "ApiKey",
    "AuthenticatedUser",
    "ClientIP",
    "FirstOf",
    "KeyExtractor",
    "RequestInfo",
    "Static",
    "default_extractor",
    # adapters
    "ASGIRateLimitMiddleware",
    "WSGIRateLimitMiddleware",
    # clocks
    "Clock",
    "ManualClock",
    "MonotonicClock",
    # errors
    "CharonError",
    "ConfigurationError",
    "RateLimitExceeded",
    "StorageError",
    # meta
    "__version__",
]

_LAZY = {
    "RedisStore": ("charon.storage.redis", "RedisStore"),
    "AsyncRedisStore": ("charon.storage.redis", "AsyncRedisStore"),
    "ASGIRateLimitMiddleware": ("charon.adapters.asgi", "RateLimitMiddleware"),
    "WSGIRateLimitMiddleware": ("charon.adapters.wsgi", "RateLimitMiddleware"),
}


def __getattr__(name: str) -> Any:
    """Resolve optional backends and adapters on first access (PEP 562)."""
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module  # noqa: PLC0415

    return getattr(import_module(target[0]), target[1])


def __dir__() -> list[str]:
    return sorted(__all__)
