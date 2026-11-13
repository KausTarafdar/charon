"""Core data interfaces shared by every layer of the SDK."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import Enum

from charon.errors import ConfigurationError

__all__ = [
    "AlgorithmName",
    "FailureMode",
    "RateLimitResult",
    "Rule",
]


class AlgorithmName(str, Enum):
    """Identifiers for the bundled evaluation strategies."""

    TOKEN_BUCKET = "token_bucket"
    SLIDING_WINDOW = "sliding_window"
    LEAKY_BUCKET = "leaky_bucket"


class FailureMode(str, Enum):
    """Behaviour when the storage backend is unreachable or times out."""
    
    FAIL_OPEN = "fail_open"
    FAIL_CLOSED = "fail_closed"


@dataclass(frozen=True)
class RateLimitResult:
    """Outcome of a single evaluation.

    ``reset_after_seconds`` and ``retry_after_seconds`` are rounded up so that a
    client honouring them never returns before the limiter has recovered.
    """

    is_allowed: bool
    limit: int
    remaining: int
    reset_after_seconds: int
    retry_after_seconds: int | None = None

    @classmethod
    def allow(cls, limit: int, remaining: int, reset_after_ms: float) -> RateLimitResult:
        return cls(
            is_allowed=True,
            limit=limit,
            remaining=max(0, int(remaining)),
            reset_after_seconds=_ceil_seconds(reset_after_ms),
        )

    @classmethod
    def deny(
        cls, limit: int, reset_after_ms: float, retry_after_ms: float | None = None
    ) -> RateLimitResult:
        retry_ms = reset_after_ms if retry_after_ms is None else retry_after_ms
        return cls(
            is_allowed=False,
            limit=limit,
            remaining=0,
            reset_after_seconds=_ceil_seconds(reset_after_ms),
            retry_after_seconds=_ceil_seconds(retry_ms),
        )


def _ceil_seconds(milliseconds: float) -> int:
    """Round milliseconds up to whole seconds, never below zero."""
    if milliseconds <= 0:
        return 0
    return math.ceil(milliseconds / 1000.0)


_PERIODS = {
    "s": 1,
    "sec": 1,
    "second": 1,
    "m": 60,
    "min": 60,
    "minute": 60,
    "h": 3600,
    "hour": 3600,
    "d": 86400,
    "day": 86400,
}
_RULE_RE = re.compile(
    r"^\s*(?P<limit>\d+)\s*/\s*(?P<count>\d*)\s*(?P<period>[a-z]+)\s*$", re.IGNORECASE
)


@dataclass(frozen=True)
class Rule:
    """A limit of ``limit`` units per ``window_ms`` milliseconds.

    ``algorithm`` selects the evaluation strategy; ``burst`` only applies to the
    token bucket, where it overrides the bucket capacity (defaults to ``limit``).
    """

    limit: int
    window_ms: int
    algorithm: AlgorithmName = AlgorithmName.SLIDING_WINDOW
    burst: int | None = None
    #: Optional label used in metrics and in composed storage keys.
    name: str = field(default="default")

    def __post_init__(self) -> None:
        if self.limit <= 0:
            raise ConfigurationError("limit must be positive")
        if self.window_ms <= 0:
            raise ConfigurationError("window_ms must be positive")
        if self.burst is not None and self.burst <= 0:
            raise ConfigurationError("burst must be positive when set")

    @property
    def capacity(self) -> int:
        """Bucket capacity: ``burst`` when provided, otherwise ``limit``."""
        return self.burst if self.burst is not None else self.limit

    @classmethod
    def parse(
        cls,
        expression: str,
        *,
        algorithm: AlgorithmName = AlgorithmName.SLIDING_WINDOW,
        burst: int | None = None,
        name: str = "default",
    ) -> Rule:
        """Build a rule from a shorthand such as ``"100/minute"`` or ``"5/15min"``."""
        match = _RULE_RE.match(expression)
        if match is None:
            raise ConfigurationError(f"cannot parse rule expression: {expression!r}")
        period = _PERIODS.get(match.group("period").lower())
        if period is None:
            raise ConfigurationError(f"unknown period in rule expression: {expression!r}")
        multiplier = int(match.group("count") or 1)
        if multiplier <= 0:
            raise ConfigurationError(f"period multiplier must be positive: {expression!r}")
        return cls(
            limit=int(match.group("limit")),
            window_ms=period * multiplier * 1000,
            algorithm=algorithm,
            burst=burst,
            name=name,
        )
