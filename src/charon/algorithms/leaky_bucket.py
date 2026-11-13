"""Leaky bucket (meter form).

The queue is modelled as a fill level that drains at a constant rate
``limit / window_ms``. Requests that would overflow the capacity are discarded,
which enforces a smooth output rate rather than a bursty one.
"""

from __future__ import annotations

from typing import Any

from charon.algorithms.base import Algorithm, Outcome
from charon.types import AlgorithmName, RateLimitResult, Rule

__all__ = ["LeakyBucket"]

#: ``(level, last_leak_ms)``
State = tuple[float, int]

LUA = """
-- KEYS[1] bucket hash
-- ARGV: capacity, leak_per_ms, now_ms, cost
local capacity = tonumber(ARGV[1])
local rate     = tonumber(ARGV[2])
local now      = tonumber(ARGV[3])
local cost     = tonumber(ARGV[4])

local bucket = redis.call('HMGET', KEYS[1], 'level', 'ts')
local level = tonumber(bucket[1]) or 0
local ts    = tonumber(bucket[2]) or now

local elapsed = math.max(0, now - ts)
level = math.max(0, level - elapsed * rate)

local allowed = 0
if level + cost <= capacity then
  allowed = 1
  level = level + cost
end

-- Time for the queue to drain completely, and for room to appear for `cost`.
local reset_ms = math.ceil(level / rate)
local retry_ms = 0
if allowed == 0 then
  retry_ms = math.ceil((level + cost - capacity) / rate)
end

redis.call('HSET', KEYS[1], 'level', level, 'ts', now)
redis.call('PEXPIRE', KEYS[1], reset_ms + 1000)

return {allowed, math.floor(capacity - level), reset_ms, retry_ms}
"""


class LeakyBucket(Algorithm):
    name = AlgorithmName.LEAKY_BUCKET
    lua = LUA

    def apply(self, state: Any | None, rule: Rule, now_ms: int, cost: int) -> Outcome:
        capacity = float(rule.capacity)
        rate = rule.limit / rule.window_ms  # units drained per millisecond

        if state is None:
            level, last_ms = 0.0, now_ms
        else:
            level, last_ms = state

        elapsed = max(0, now_ms - last_ms)
        level = max(0.0, level - elapsed * rate)

        allowed = level + cost <= capacity
        if allowed:
            level += cost

        reset_after_ms = level / rate
        next_state: State = (level, now_ms)
        ttl_ms = int(reset_after_ms) + 1000

        if allowed:
            result = RateLimitResult.allow(rule.limit, int(capacity - level), reset_after_ms)
        else:
            retry_after_ms = (level + cost - capacity) / rate
            result = RateLimitResult.deny(rule.limit, reset_after_ms, retry_after_ms)
        return next_state, result, ttl_ms

    def redis_argv(self, rule: Rule, now_ms: int, cost: int) -> list[str]:
        return [
            str(rule.capacity),
            repr(rule.limit / rule.window_ms),
            str(now_ms),
            str(cost),
        ]
