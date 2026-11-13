from __future__ import annotations

import pytest

from charon.errors import ConfigurationError
from charon.types import AlgorithmName, FailureMode, RateLimitResult, Rule, _ceil_seconds


def test_ceil_seconds() -> None:
    assert _ceil_seconds(0) == 0
    assert _ceil_seconds(-100) == 0
    assert _ceil_seconds(1) == 1
    assert _ceil_seconds(999) == 1
    assert _ceil_seconds(1000) == 1
    assert _ceil_seconds(1001) == 2
    assert _ceil_seconds(2500.5) == 3


def test_rate_limit_result_allow() -> None:
    res = RateLimitResult.allow(limit=100, remaining=95, reset_after_ms=1500)
    assert res.is_allowed is True
    assert res.limit == 100
    assert res.remaining == 95
    assert res.reset_after_seconds == 2
    assert res.retry_after_seconds is None


def test_rate_limit_result_deny() -> None:
    res = RateLimitResult.deny(limit=10, reset_after_ms=3200, retry_after_ms=1200)
    assert res.is_allowed is False
    assert res.limit == 10
    assert res.remaining == 0
    assert res.reset_after_seconds == 4
    assert res.retry_after_seconds == 2

    # retry_after_ms defaults to reset_after_ms if not specified
    res_default = RateLimitResult.deny(limit=10, reset_after_ms=2500)
    assert res_default.retry_after_seconds == 3


def test_rule_validation() -> None:
    with pytest.raises(ConfigurationError, match="limit must be positive"):
        Rule(limit=0, window_ms=1000)

    with pytest.raises(ConfigurationError, match="window_ms must be positive"):
        Rule(limit=10, window_ms=0)

    with pytest.raises(ConfigurationError, match="burst must be positive"):
        Rule(limit=10, window_ms=1000, burst=0)

    rule = Rule(limit=10, window_ms=1000, burst=15)
    assert rule.capacity == 15

    rule_no_burst = Rule(limit=10, window_ms=1000)
    assert rule_no_burst.capacity == 10


def test_rule_parse() -> None:
    r1 = Rule.parse("100/minute")
    assert r1.limit == 100
    assert r1.window_ms == 60_000
    assert r1.algorithm == AlgorithmName.SLIDING_WINDOW

    r2 = Rule.parse("10/s", algorithm=AlgorithmName.TOKEN_BUCKET, burst=20, name="api")
    assert r2.limit == 10
    assert r2.window_ms == 1_000
    assert r2.algorithm == AlgorithmName.TOKEN_BUCKET
    assert r2.capacity == 20
    assert r2.name == "api"

    r3 = Rule.parse("5/15min")
    assert r3.limit == 5
    assert r3.window_ms == 15 * 60 * 1000

    r4 = Rule.parse("1000/hour")
    assert r4.limit == 1000
    assert r4.window_ms == 3600 * 1000

    r5 = Rule.parse("50/day")
    assert r5.limit == 50
    assert r5.window_ms == 86400 * 1000

    with pytest.raises(ConfigurationError, match="cannot parse rule expression"):
        Rule.parse("invalid-rule")

    with pytest.raises(ConfigurationError, match="unknown period in rule expression"):
        Rule.parse("10/fortnight")


def test_enums() -> None:
    assert AlgorithmName.TOKEN_BUCKET.value == "token_bucket"
    assert AlgorithmName.SLIDING_WINDOW.value == "sliding_window"
    assert AlgorithmName.LEAKY_BUCKET.value == "leaky_bucket"

    assert FailureMode.FAIL_OPEN.value == "fail_open"
    assert FailureMode.FAIL_CLOSED.value == "fail_closed"
