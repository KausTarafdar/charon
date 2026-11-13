"""Limiter engine core.

The engine is transport-agnostic: it takes a key (or a normalised
:class:`~charon.keys.RequestInfo`), picks the rule, delegates the atomic state
transition to the storage backend, and applies the failure-mode policy when that
backend is unavailable. HTTP middleware, Celery tasks, and CLI tools all funnel
through the same two methods.
"""

from __future__ import annotations

import functools
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar, cast

from charon.errors import ConfigurationError, RateLimitExceeded, StorageError
from charon.keys import KeyExtractor, RequestInfo, default_extractor
from charon.types import FailureMode, RateLimitResult, Rule

__all__ = ["Limiter"]

logger = logging.getLogger("charon")

F = TypeVar("F", bound=Callable[..., Any])


class Limiter:
    """Evaluates rules against a storage backend.

    ``storage`` may be a sync backend, an async backend, or one that implements
    both; :meth:`check` and :meth:`acheck` each require the matching flavour.
    """

    def __init__(
        self,
        storage: Any,
        rule: Rule | str | None = None,
        *,
        key_extractor: KeyExtractor | None = None,
        failure_mode: FailureMode = FailureMode.FAIL_OPEN,
        enabled: bool = True,
    ) -> None:
        self.storage = storage
        self.rule = Rule.parse(rule) if isinstance(rule, str) else rule
        self.key_extractor = key_extractor or default_extractor()
        self.failure_mode = failure_mode
        self.enabled = enabled

    # -- evaluation ---------------------------------------------------------

    def check(
        self, key: str, rule: Rule | str | None = None, *, cost: int = 1
    ) -> RateLimitResult:
        """Evaluate ``key`` against a sync backend."""
        resolved = self._resolve_rule(rule)
        if not self.enabled:
            return _bypass(resolved)
        try:
            result = self.storage.evaluate(key, resolved, cost)
        except StorageError as exc:
            return self._on_storage_error(key, resolved, exc)
        if inspect.isawaitable(result):
            # Close the orphaned coroutine so it does not warn at GC time.
            close = getattr(result, "close", None)
            if close is not None:
                close()
            raise ConfigurationError(
                "storage backend is async; use `await limiter.acheck(...)` instead"
            )
        return cast(RateLimitResult, result)

    async def acheck(
        self, key: str, rule: Rule | str | None = None, *, cost: int = 1
    ) -> RateLimitResult:
        """Evaluate ``key`` against an async backend (a sync one also works)."""
        resolved = self._resolve_rule(rule)
        if not self.enabled:
            return _bypass(resolved)
        try:
            result = self.storage.evaluate(key, resolved, cost)
            if inspect.isawaitable(result):
                result = await cast(Awaitable[RateLimitResult], result)
        except StorageError as exc:
            return self._on_storage_error(key, resolved, exc)
        return cast(RateLimitResult, result)

    def check_request(
        self, request: RequestInfo, rule: Rule | str | None = None, *, cost: int = 1
    ) -> RateLimitResult:
        return self.check(self.key_for(request), rule, cost=cost)

    async def acheck_request(
        self, request: RequestInfo, rule: Rule | str | None = None, *, cost: int = 1
    ) -> RateLimitResult:
        return await self.acheck(self.key_for(request), rule, cost=cost)

    def key_for(self, request: RequestInfo) -> str:
        return self.key_extractor(request) or "anonymous"

    # -- imperative helpers -------------------------------------------------

    def acquire(
        self, key: str, rule: Rule | str | None = None, *, cost: int = 1
    ) -> RateLimitResult:
        """Like :meth:`check` but raises :class:`RateLimitExceeded` when throttled."""
        result = self.check(key, rule, cost=cost)
        if not result.is_allowed:
            raise RateLimitExceeded(result, key)
        return result

    async def aacquire(
        self, key: str, rule: Rule | str | None = None, *, cost: int = 1
    ) -> RateLimitResult:
        result = await self.acheck(key, rule, cost=cost)
        if not result.is_allowed:
            raise RateLimitExceeded(result, key)
        return result

    def limit(
        self,
        rule: Rule | str | None = None,
        *,
        key: str | Callable[..., str] | None = None,
        cost: int = 1,
    ) -> Callable[[F], F]:
        """Decorator for non-HTTP call sites (Celery tasks, gRPC handlers, CLI).

        ``key`` may be a literal, or a callable receiving the wrapped function's
        arguments. Raises :class:`RateLimitExceeded` when throttled.
        """

        def decorator(func: F) -> F:
            def resolve_key(args: Any, kwargs: Any) -> str:
                if callable(key):
                    return str(key(*args, **kwargs))
                if key is not None:
                    return str(key)
                return f"{func.__module__}.{func.__qualname__}"

            if inspect.iscoroutinefunction(func):

                @functools.wraps(func)
                async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                    await self.aacquire(resolve_key(args, kwargs), rule, cost=cost)
                    return await func(*args, **kwargs)

                return cast(F, async_wrapper)

            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                self.acquire(resolve_key(args, kwargs), rule, cost=cost)
                return func(*args, **kwargs)

            return cast(F, sync_wrapper)

        return decorator

    # -- maintenance --------------------------------------------------------

    def reset(self, key: str, rule: Rule | str | None = None) -> None:
        self.storage.reset(key, self._resolve_rule(rule))

    async def areset(self, key: str, rule: Rule | str | None = None) -> None:
        result = self.storage.reset(key, self._resolve_rule(rule))
        if inspect.isawaitable(result):
            await result

    # -- internals ----------------------------------------------------------

    def _resolve_rule(self, rule: Rule | str | None) -> Rule:
        if isinstance(rule, str):
            return Rule.parse(rule)
        if rule is not None:
            return rule
        if self.rule is None:
            raise ConfigurationError("no rule supplied and the limiter has no default rule")
        return self.rule

    def _on_storage_error(self, key: str, rule: Rule, exc: StorageError) -> RateLimitResult:
        """Apply the configured failure mode and emit telemetry."""
        logger.warning(
            "charon storage failure (%s); applying %s for key=%s",
            exc,
            self.failure_mode.value,
            key,
            exc_info=exc,
        )
        if self.failure_mode is FailureMode.FAIL_CLOSED:
            return RateLimitResult.deny(rule.limit, float(rule.window_ms))
        return RateLimitResult.allow(rule.limit, rule.limit, float(rule.window_ms))


def _bypass(rule: Rule) -> RateLimitResult:
    """Result used when the limiter is disabled — allow, and report full headroom."""
    return RateLimitResult.allow(rule.limit, rule.limit, float(rule.window_ms))
