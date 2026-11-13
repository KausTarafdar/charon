from __future__ import annotations

import pytest

from charon.clock import ManualClock
from charon.engine import Limiter
from charon.errors import ConfigurationError, RateLimitExceeded, StorageError
from charon.keys import RequestInfo
from charon.storage.memory import AsyncMemoryStore, MemoryStore
from charon.types import FailureMode, RateLimitResult, Rule


def test_limiter_sync_check_and_acquire() -> None:
    clock = ManualClock(start_ms=1000)
    store = MemoryStore(clock=clock)
    rule = Rule.parse("2/minute")
    limiter = Limiter(storage=store, rule=rule)

    # 1. check() returns RateLimitResult
    r1 = limiter.check("k1")
    assert r1.is_allowed is True
    assert r1.remaining == 1

    r2 = limiter.check("k1")
    assert r2.is_allowed is True
    assert r2.remaining == 0

    r3 = limiter.check("k1")
    assert r3.is_allowed is False

    # 2. acquire() succeeds or raises RateLimitExceeded
    limiter.reset("k2")
    res_acq = limiter.acquire("k2")
    assert res_acq.is_allowed is True

    limiter.acquire("k2")
    with pytest.raises(RateLimitExceeded) as exc_info:
        limiter.acquire("k2")
    assert exc_info.value.key == "k2"
    assert exc_info.value.result.is_allowed is False


@pytest.mark.asyncio
async def test_limiter_async_acheck_and_aacquire() -> None:
    clock = ManualClock(start_ms=1000)
    store = AsyncMemoryStore(clock=clock)
    rule = Rule.parse("1/minute")
    limiter = Limiter(storage=store, rule=rule)

    r1 = await limiter.acheck("k1")
    assert r1.is_allowed is True

    r2 = await limiter.acheck("k1")
    assert r2.is_allowed is False

    await limiter.areset("k2")
    res_acq = await limiter.aacquire("k2")
    assert res_acq.is_allowed is True

    with pytest.raises(RateLimitExceeded):
        await limiter.aacquire("k2")


def test_limiter_request_info_check() -> None:
    store = MemoryStore()
    rule = Rule.parse("5/minute")
    limiter = Limiter(storage=store, rule=rule)

    req = RequestInfo(state={"user": {"id": "bob"}})
    res = limiter.check_request(req)
    assert res.is_allowed is True
    assert limiter.key_for(req) == "user:bob"


def test_limiter_failure_mode_fail_open() -> None:
    class FailingStore:
        def evaluate(self, key: str, rule: Rule, cost: int) -> RateLimitResult:
            raise StorageError("redis down")

    rule = Rule.parse("10/minute")
    limiter = Limiter(
        storage=FailingStore(),
        rule=rule,
        failure_mode=FailureMode.FAIL_OPEN,
    )

    res = limiter.check("any_key")
    assert res.is_allowed is True
    assert res.remaining == 10  # Full headroom on fail-open


def test_limiter_failure_mode_fail_closed() -> None:
    class FailingStore:
        def evaluate(self, key: str, rule: Rule, cost: int) -> RateLimitResult:
            raise StorageError("redis down")

    rule = Rule.parse("10/minute")
    limiter = Limiter(
        storage=FailingStore(),
        rule=rule,
        failure_mode=FailureMode.FAIL_CLOSED,
    )

    res = limiter.check("any_key")
    assert res.is_allowed is False
    assert res.remaining == 0


def test_limiter_disabled_bypass() -> None:
    store = MemoryStore()
    rule = Rule.parse("1/minute")
    limiter = Limiter(storage=store, rule=rule, enabled=False)

    for _ in range(10):
        res = limiter.check("user")
        assert res.is_allowed is True
        assert res.remaining == 1


def test_limiter_sync_check_with_async_backend_raises() -> None:
    store = AsyncMemoryStore()
    limiter = Limiter(storage=store, rule="5/minute")

    with pytest.raises(ConfigurationError, match="storage backend is async"):
        limiter.check("key")


def test_limiter_decorator_sync_and_async() -> None:
    store = MemoryStore()
    rule = Rule.parse("1/minute")
    limiter = Limiter(storage=store, rule=rule)

    @limiter.limit(rule, key=lambda user_id: f"user:{user_id}")
    def process_task(user_id: int) -> str:
        return f"done {user_id}"

    assert process_task(1) == "done 1"
    with pytest.raises(RateLimitExceeded):
        process_task(1)

    # Different key is allowed
    assert process_task(2) == "done 2"


@pytest.mark.asyncio
async def test_limiter_decorator_async() -> None:
    store = AsyncMemoryStore()
    rule = Rule.parse("1/minute")
    limiter = Limiter(storage=store, rule=rule)

    @limiter.limit(rule, key=lambda user_id: f"user:{user_id}")
    async def async_process_task(user_id: int) -> str:
        return f"done {user_id}"

    assert await async_process_task(1) == "done 1"
    with pytest.raises(RateLimitExceeded):
        await async_process_task(1)
