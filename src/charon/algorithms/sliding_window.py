"""Sliding window counter.

Two fixed buckets approximate a true sliding window without storing per-request
timestamps:

    count = current + previous * (1 - elapsed_fraction)
"""

from __future__ import annotations

from typing import Any

from charon.algorithms.base import Algorithm, Outcome
from charon.types import AlgorithmName, RateLimitResult, Rule

__all__ = ["SlidingWindowCounter"]

#: ``(window_start_ms, current_count, previous_count)``
State = tuple[int, float, float]

LUA = """
-- KEYS[1] counter hash
-- ARGV: limit, window_ms, now_ms, cost
local limit  = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local now    = tonumber(ARGV[3])
local cost   = tonumber(ARGV[4])

local slot = math.floor(now / window) * window

local stored = redis.call('HMGET', KEYS[1], 'slot', 'cur', 'prev')
local prev_slot = tonumber(stored[1])
local cur  = tonumber(stored[2]) or 0
local prev = tonumber(stored[3]) or 0

if prev_slot == nil then
  cur, prev = 0, 0
elseif slot - prev_slot == window then
  prev, cur = cur, 0
elseif slot ~= prev_slot then
  prev, cur = 0, 0
end

local elapsed = (now - slot) / window
local estimate = cur + prev * (1 - elapsed)

local allowed = 0
if estimate + cost <= limit then
  allowed = 1
  cur = cur + cost
  estimate = estimate + cost
end

local reset_ms = math.ceil(slot + window - now)
local retry_ms = 0
if allowed == 0 then
  retry_ms = reset_ms
end

redis.call('HSET', KEYS[1], 'slot', slot, 'cur', cur, 'prev', prev)
redis.call('PEXPIRE', KEYS[1], window * 2 + 1000)

return {allowed, math.floor(math.max(0, limit - estimate)), reset_ms, retry_ms}
"""


class SlidingWindowCounter(Algorithm):
    name = AlgorithmName.SLIDING_WINDOW
    lua = LUA

    def apply(self, state: Any | None, rule: Rule, now_ms: int, cost: int) -> Outcome:
        window = rule.window_ms
        slot = (now_ms // window) * window

        if state is None:
            current, previous = 0.0, 0.0
        else:
            prev_slot, current, previous = state
            if slot - prev_slot == window:
                previous, current = current, 0.0
            elif slot != prev_slot:
                previous, current = 0.0, 0.0

        elapsed_fraction = (now_ms - slot) / window
        estimate = current + previous * (1 - elapsed_fraction)

        allowed = estimate + cost <= rule.limit
        if allowed:
            current += cost
            estimate += cost

        reset_after_ms = float(slot + window - now_ms)
        next_state: State = (slot, current, previous)
        ttl_ms = window * 2 + 1000

        if allowed:
            remaining = int(max(0.0, rule.limit - estimate))
            result = RateLimitResult.allow(rule.limit, remaining, reset_after_ms)
        else:
            result = RateLimitResult.deny(rule.limit, reset_after_ms)
        return next_state, result, ttl_ms

    def redis_argv(self, rule: Rule, now_ms: int, cost: int) -> list[str]:
        return [str(rule.limit), str(rule.window_ms), str(now_ms), str(cost)]
