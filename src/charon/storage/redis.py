"""Redis storage engine.

Every evaluation runs as a single ``EVALSHA`` so the read-modify-write is atomic
across processes and hosts — that is what removes the check-then-set race between
Gunicorn/Uvicorn workers.

Requires the optional dependency: ``pip install "charon-ratelimit[redis]"``.
"""

from __future__ import annotations

from typing import Any

from charon.algorithms import get_algorithm, result_from_reply
from charon.clock import Clock, default_clock
from charon.errors import ConfigurationError, StorageError
from charon.storage.base import compose_key
from charon.types import RateLimitResult, Rule

__all__ = ["AsyncRedisStore", "RedisStore"]


def _require_redis() -> Any:
    try:
        import redis  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise ConfigurationError(
            'redis backend requires the "redis" extra: pip install "charon-ratelimit[redis]"'
        ) from exc
    return redis


class _ScriptCache:
    """Maps algorithm name -> registered script object for one client."""

    def __init__(self) -> None:
        self._scripts: dict[str, Any] = {}

    def get(self, client: Any, algorithm: Any) -> Any:
        key = algorithm.name.value
        script = self._scripts.get(key)
        if script is None:
            # register_script handles EVALSHA with a NOSCRIPT fallback for us.
            script = client.register_script(algorithm.lua)
            self._scripts[key] = script
        return script


class RedisStore:
    """Blocking Redis backend (sync protocol)."""

    def __init__(
        self,
        client: Any | None = None,
        *,
        url: str | None = None,
        namespace: str = "charon",
        clock: Clock = default_clock,
    ) -> None:
        if client is None:
            if url is None:
                raise ConfigurationError("RedisStore requires either a client or a url")
            client = _require_redis().Redis.from_url(url)
        self._client = client
        self._namespace = namespace
        self._clock = clock
        self._cache = _ScriptCache()

    @property
    def client(self) -> Any:
        return self._client

    def evaluate(self, key: str, rule: Rule, cost: int = 1) -> RateLimitResult:
        algorithm = get_algorithm(rule.algorithm)
        composed = compose_key(self._namespace, key, rule)
        now_ms = self._clock.now_ms()
        script = self._cache.get(self._client, algorithm)
        try:
            reply = script(
                keys=algorithm.redis_keys(composed),
                args=algorithm.redis_argv(rule, now_ms, cost),
            )
        except Exception as exc:
            raise StorageError(f"redis evaluation failed for {composed!r}: {exc}") from exc
        return result_from_reply(reply, rule.limit)

    def reset(self, key: str, rule: Rule) -> None:
        composed = compose_key(self._namespace, key, rule)
        try:
            self._client.delete(*get_algorithm(rule.algorithm).redis_keys(composed))
        except Exception as exc:
            raise StorageError(f"redis reset failed for {composed!r}: {exc}") from exc

    def close(self) -> None:
        self._client.close()


class AsyncRedisStore:
    """Non-blocking Redis backend built on ``redis.asyncio``."""

    def __init__(
        self,
        client: Any | None = None,
        *,
        url: str | None = None,
        namespace: str = "charon",
        clock: Clock = default_clock,
    ) -> None:
        if client is None:
            if url is None:
                raise ConfigurationError("AsyncRedisStore requires either a client or a url")
            from redis.asyncio import Redis  # noqa: PLC0415

            _require_redis()
            client = Redis.from_url(url)
        self._client = client
        self._namespace = namespace
        self._clock = clock
        self._cache = _ScriptCache()

    @property
    def client(self) -> Any:
        return self._client

    async def evaluate(self, key: str, rule: Rule, cost: int = 1) -> RateLimitResult:
        algorithm = get_algorithm(rule.algorithm)
        composed = compose_key(self._namespace, key, rule)
        now_ms = self._clock.now_ms()
        script = self._cache.get(self._client, algorithm)
        try:
            reply = await script(
                keys=algorithm.redis_keys(composed),
                args=algorithm.redis_argv(rule, now_ms, cost),
            )
        except Exception as exc:
            raise StorageError(f"redis evaluation failed for {composed!r}: {exc}") from exc
        return result_from_reply(reply, rule.limit)

    async def reset(self, key: str, rule: Rule) -> None:
        composed = compose_key(self._namespace, key, rule)
        try:
            await self._client.delete(*get_algorithm(rule.algorithm).redis_keys(composed))
        except Exception as exc:
            raise StorageError(f"redis reset failed for {composed!r}: {exc}") from exc

    async def close(self) -> None:
        await self._client.aclose()
