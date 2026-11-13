"""Exception hierarchy for the SDK."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from charon.types import RateLimitResult

__all__ = [
    "CharonError",
    "ConfigurationError",
    "RateLimitExceeded",
    "StorageError",
]


class CharonError(Exception):
    """Base class for everything this SDK raises."""


class ConfigurationError(CharonError, ValueError):
    """Invalid limiter, rule, or adapter configuration."""


class StorageError(CharonError):
    """The storage backend failed, timed out, or returned an unusable reply.

    The limiter catches this and applies the configured
    :class:`~charon.types.FailureMode`.
    """


class RateLimitExceeded(CharonError):
    """Raised by the imperative API when a request is throttled."""

    def __init__(self, result: RateLimitResult, key: str | None = None) -> None:
        self.result = result
        self.key = key
        retry = result.retry_after_seconds
        super().__init__(
            f"rate limit exceeded for {key or 'request'}: "
            f"{result.limit} allowed, retry after {retry}s"
        )
