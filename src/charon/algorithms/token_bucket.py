"""Token bucket with lazy refill.

Tokens accrue as a function of elapsed time on each evaluation, so no background
refill thread is needed:

    tokens = min(capacity, tokens + dt * refill_rate)
"""

from __future__ import annotations

from typing import Any

from charon.algorithms.base import Algorithm, Outcome
from charon.types import AlgorithmName, RateLimitResult, Rule

__all__ = ["TokenBucket"]

#: ``(tokens, last_refill_ms)``
State = tuple[float, int]

LUA = """
-- KEYS[1] bucket hash
-- ARGV: capacity, refill_per_ms, now_ms, cost
local capacity = tonumber(ARGV[1])
local rate     = tonumber(ARGV[2])
local now      = tonumber(ARGV[3])
local cost     = tonumber(ARGV[4])

local bucket = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(bucket[1])
local ts     = tonumber(bucket[2])
if tokens == nil then
  tokens = capacity
  ts = now
end

local elapsed = math.max(0, now - ts)
tokens = math.min(capacity, tokens + elapsed * rate)

local allowed = 0
if tokens >= cost then
  allowed = 1
  tokens = tokens - cost
end

-- Time until the bucket is full again, and until `cost` tokens are available.
local reset_ms = math.ceil((capacity - tokens) / rate)
local retry_ms = 0
if allowed == 0 then
  retry_ms = math.ceil((cost - tokens) / rate)
end

redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
redis.call('PEXPIRE', KEYS[1], reset_ms + 1000)

return {allowed, math.floor(tokens), reset_ms, retry_ms}
"""


class TokenBucket(Algorithm):
    name = AlgorithmName.TOKEN_BUCKET
    lua = LUA

    def apply(self, state: Any | None, rule: Rule, now_ms: int, cost: int) -> Outcome:
        capacity = float(rule.capacity)
        rate = rule.limit / rule.window_ms  # tokens per millisecond

        if state is None:
            tokens, last_ms = capacity, now_ms
        else:
            tokens, last_ms = state

        elapsed = max(0, now_ms - last_ms)
        tokens = min(capacity, tokens + elapsed * rate)

        allowed = tokens >= cost
        if allowed:
            tokens -= cost

        reset_after_ms = (capacity - tokens) / rate
        next_state: State = (tokens, now_ms)
        ttl_ms = int(reset_after_ms) + 1000

        if allowed:
            result = RateLimitResult.allow(rule.limit, int(tokens), reset_after_ms)
        else:
            result = RateLimitResult.deny(rule.limit, reset_after_ms, (cost - tokens) / rate)
        return next_state, result, ttl_ms

    def redis_argv(self, rule: Rule, now_ms: int, cost: int) -> list[str]:
        return [
            str(rule.capacity),
            repr(rule.limit / rule.window_ms),
            str(now_ms),
            str(cost),
        ]
