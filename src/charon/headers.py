"""IETF-aligned response headers.

Follows draft-ietf-httpapi-ratelimit-headers: ``RateLimit-*`` on every evaluated
response, plus ``Retry-After`` only when the request was throttled.
"""

from __future__ import annotations

from charon.types import RateLimitResult

__all__ = ["LIMIT", "REMAINING", "RESET", "RETRY_AFTER", "as_list", "build"]

LIMIT = "RateLimit-Limit"
REMAINING = "RateLimit-Remaining"
RESET = "RateLimit-Reset"
RETRY_AFTER = "Retry-After"


def build(result: RateLimitResult) -> dict[str, str]:
    """Header name -> value for one evaluation."""
    headers = {
        LIMIT: str(result.limit),
        REMAINING: str(result.remaining),
        RESET: str(result.reset_after_seconds),
    }
    if not result.is_allowed and result.retry_after_seconds is not None:
        headers[RETRY_AFTER] = str(result.retry_after_seconds)
    return headers


def as_list(result: RateLimitResult) -> list[tuple[str, str]]:
    """Same headers as an ordered list, for WSGI's ``start_response``."""
    return list(build(result).items())


def as_raw(result: RateLimitResult) -> list[tuple[bytes, bytes]]:
    """Same headers encoded latin-1, for ASGI message dicts."""
    return [(k.encode("latin-1"), v.encode("latin-1")) for k, v in build(result).items()]
