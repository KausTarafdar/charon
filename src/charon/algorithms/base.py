"""Algorithm strategy contract.

Every strategy is implemented twice against one shared contract:

* :meth:`Algorithm.apply` — a pure state transition used by process-local stores.
* :attr:`Algorithm.lua` — the same transition as a Redis script, so distributed
  evaluation stays atomic (see :mod:`charon.storage.redis`).

Both halves speak the same reply shape, ``[allowed, remaining, reset_after_ms,
retry_after_ms]``, which :func:`result_from_reply` turns into a
:class:`~charon.types.RateLimitResult`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from charon.types import AlgorithmName, RateLimitResult

__all__ = ["Algorithm", "Outcome", "result_from_reply"]

#: ``(next_state, result, ttl_ms)``. ``next_state`` of ``None`` deletes the entry;
#: ``ttl_ms`` tells the store how long the state stays relevant.
Outcome = tuple[Any | None, RateLimitResult, int]


class Algorithm:
    """Base class for evaluation strategies."""

    #: Stable identifier, also used to namespace storage keys.
    name: AlgorithmName
    #: Redis script implementing :meth:`apply` atomically.
    lua: str

    def apply(self, state: Any | None, rule: Any, now_ms: int, cost: int) -> Outcome:
        """Fold ``cost`` units into ``state`` and decide whether to admit them."""
        raise NotImplementedError

    def redis_keys(self, key: str) -> list[str]:
        """Redis keys the script touches. Single-key by default, so scripts stay
        cluster-safe (all keys hash to one slot)."""
        return [key]

    def redis_argv(self, rule: Any, now_ms: int, cost: int) -> list[str]:
        """Positional ARGV passed to :attr:`lua`."""
        raise NotImplementedError


def result_from_reply(reply: Sequence[Any], limit: int) -> RateLimitResult:
    """Decode the uniform ``[allowed, remaining, reset_ms, retry_ms]`` reply."""
    allowed = bool(int(reply[0]))
    remaining = int(reply[1])
    reset_after_ms = float(reply[2])
    if allowed:
        return RateLimitResult.allow(limit, remaining, reset_after_ms)
    return RateLimitResult.deny(limit, reset_after_ms, float(reply[3]))
