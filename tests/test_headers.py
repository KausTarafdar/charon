from __future__ import annotations

from charon import headers
from charon.types import RateLimitResult


def test_headers_build_allowed() -> None:
    res = RateLimitResult.allow(limit=100, remaining=42, reset_after_ms=3500)
    hdrs = headers.build(res)

    assert hdrs[headers.LIMIT] == "100"
    assert hdrs[headers.REMAINING] == "42"
    assert hdrs[headers.RESET] == "4"  # ceil(3500/1000)
    assert headers.RETRY_AFTER not in hdrs


def test_headers_build_denied() -> None:
    res = RateLimitResult.deny(limit=100, reset_after_ms=5000, retry_after_ms=2100)
    hdrs = headers.build(res)

    assert hdrs[headers.LIMIT] == "100"
    assert hdrs[headers.REMAINING] == "0"
    assert hdrs[headers.RESET] == "5"
    assert hdrs[headers.RETRY_AFTER] == "3"  # ceil(2100/1000)


def test_headers_as_list() -> None:
    res = RateLimitResult.allow(limit=50, remaining=10, reset_after_ms=1000)
    hdr_list = headers.as_list(res)
    assert isinstance(hdr_list, list)
    assert (headers.LIMIT, "50") in hdr_list
    assert (headers.REMAINING, "10") in hdr_list
    assert (headers.RESET, "1") in hdr_list


def test_headers_as_raw() -> None:
    res = RateLimitResult.deny(limit=50, reset_after_ms=2000, retry_after_ms=1000)
    raw_list = headers.as_raw(res)
    assert (b"ratelimit-limit", b"50") in [(k.lower(), v) for k, v in raw_list]
    assert (b"retry-after", b"1") in [(k.lower(), v) for k, v in raw_list]
