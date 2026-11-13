from __future__ import annotations

import pytest

from charon.algorithms import (
    LeakyBucket,
    SlidingWindowCounter,
    TokenBucket,
    get_algorithm,
    result_from_reply,
)
from charon.errors import ConfigurationError
from charon.types import AlgorithmName, Rule


def test_registry() -> None:
    tb = get_algorithm(AlgorithmName.TOKEN_BUCKET)
    assert isinstance(tb, TokenBucket)

    sw = get_algorithm(AlgorithmName.SLIDING_WINDOW)
    assert isinstance(sw, SlidingWindowCounter)

    lb = get_algorithm(AlgorithmName.LEAKY_BUCKET)
    assert isinstance(lb, LeakyBucket)

    with pytest.raises(ConfigurationError, match="unknown algorithm"):
        get_algorithm("nonexistent")  # type: ignore[arg-type]


def test_result_from_reply() -> None:
    # Allowed reply
    res_allow = result_from_reply([1, 4, 1200, 0], limit=5)
    assert res_allow.is_allowed is True
    assert res_allow.limit == 5
    assert res_allow.remaining == 4
    assert res_allow.reset_after_seconds == 2
    assert res_allow.retry_after_seconds is None

    # Denied reply
    res_deny = result_from_reply([0, 0, 5000, 2500], limit=5)
    assert res_deny.is_allowed is False
    assert res_deny.remaining == 0
    assert res_deny.reset_after_seconds == 5
    assert res_deny.retry_after_seconds == 3


def test_token_bucket_apply() -> None:
    tb = TokenBucket()
    rule = Rule(limit=10, window_ms=1000, burst=15)  # 10 tokens/sec, capacity 15

    # 1. First request at t=0, cost=5
    state, res, ttl = tb.apply(None, rule, now_ms=0, cost=5)
    assert res.is_allowed is True
    assert res.remaining == 10  # 15 - 5
    assert ttl > 0
    tokens, last_ms = state
    assert tokens == 10.0
    assert last_ms == 0

    # 2. Advance 500ms -> should refill 0.5 * 10 = 5 tokens -> tokens = 15
    state, res, ttl = tb.apply(state, rule, now_ms=500, cost=12)
    assert res.is_allowed is True
    assert res.remaining == 3  # 15 - 12
    tokens, _ = state
    assert tokens == 3.0

    # 3. Request cost 5 at t=500 (only 3 available) -> Denied
    state, res, ttl = tb.apply(state, rule, now_ms=500, cost=5)
    assert res.is_allowed is False
    assert res.remaining == 0
    assert res.retry_after_seconds is not None
    # 2 tokens needed, rate is 10/1000 ms -> 2 / 0.01 = 200ms -> ceil(200ms) = 1s
    assert res.retry_after_seconds == 1


def test_token_bucket_redis_contract() -> None:
    tb = TokenBucket()
    rule = Rule(limit=10, window_ms=1000, burst=15)
    assert tb.redis_keys("test:key") == ["test:key"]
    argv = tb.redis_argv(rule, now_ms=1000, cost=2)
    assert argv[0] == "15"  # capacity
    assert argv[2] == "1000"  # now_ms
    assert argv[3] == "2"  # cost


def test_sliding_window_counter_apply() -> None:
    sw = SlidingWindowCounter()
    rule = Rule(limit=10, window_ms=1000)

    # Slot at t=1200 is 1000..2000
    state, res, _ttl = sw.apply(None, rule, now_ms=1200, cost=6)
    assert res.is_allowed is True
    assert res.remaining == 4  # 10 - 6
    slot, cur, prev = state
    assert slot == 1000
    assert cur == 6.0
    assert prev == 0.0

    # Same slot at t=1500, cost=3 -> allowed (6 + 3 = 9 <= 10)
    state, res, _ttl = sw.apply(state, rule, now_ms=1500, cost=3)
    assert res.is_allowed is True
    assert res.remaining == 1

    # Same slot, cost 2 -> exceeds limit 10 -> denied
    state, res, _ttl = sw.apply(state, rule, now_ms=1500, cost=2)
    assert res.is_allowed is False

    # Next window slot: t=2500 (slot 2000..3000), elapsed fraction = (2500 - 2000)/1000 = 0.5
    # Previous count was 9. Estimate = 0 + 9 * (1 - 0.5) = 4.5
    # Request cost 4 -> estimate + cost = 4.5 + 4 = 8.5 <= 10 -> allowed
    state, res, _ttl = sw.apply(state, rule, now_ms=2500, cost=4)
    assert res.is_allowed is True

    # Far future slot: t=5000 -> prev count becomes 0
    state, res, _ttl = sw.apply(state, rule, now_ms=5000, cost=1)
    assert res.is_allowed is True
    slot, cur, prev = state
    assert slot == 5000
    assert cur == 1.0
    assert prev == 0.0


def test_sliding_window_counter_redis_contract() -> None:
    sw = SlidingWindowCounter()
    rule = Rule(limit=10, window_ms=1000)
    assert sw.redis_keys("test:key") == ["test:key"]
    argv = sw.redis_argv(rule, now_ms=1000, cost=1)
    assert argv == ["10", "1000", "1000", "1"]


def test_leaky_bucket_apply() -> None:
    lb = LeakyBucket()
    rule = Rule(limit=10, window_ms=1000)  # capacity 10, drains 1 unit per 100ms

    # 1. Initial request cost 4 at t=0 -> level becomes 4
    state, res, _ttl = lb.apply(None, rule, now_ms=0, cost=4)
    assert res.is_allowed is True
    assert res.remaining == 6  # 10 - 4
    level, last_ms = state
    assert level == 4.0
    assert last_ms == 0

    # 2. Advance 200ms -> drained 2 units -> level was 2 -> add cost 7 -> level = 9 <= 10
    state, res, _ttl = lb.apply(state, rule, now_ms=200, cost=7)
    assert res.is_allowed is True
    assert res.remaining == 1
    level, _ = state
    assert level == 9.0

    # 3. Same time t=200, cost 2 -> level + cost = 11 > 10 -> denied
    state, res, _ttl = lb.apply(state, rule, now_ms=200, cost=2)
    assert res.is_allowed is False
    assert res.retry_after_seconds is not None
    assert res.retry_after_seconds >= 1


def test_leaky_bucket_redis_contract() -> None:
    lb = LeakyBucket()
    rule = Rule(limit=10, window_ms=1000)
    assert lb.redis_keys("test:key") == ["test:key"]
    argv = lb.redis_argv(rule, now_ms=1000, cost=1)
    assert argv[0] == "10"
    assert argv[2] == "1000"
    assert argv[3] == "1"
