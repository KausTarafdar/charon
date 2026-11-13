from __future__ import annotations

import threading

import pytest

from charon.clock import ManualClock
from charon.storage.memory import AsyncMemoryStore, MemoryStore
from charon.types import AlgorithmName, Rule


def test_memory_store_sync_evaluation() -> None:
    clock = ManualClock(start_ms=1000)
    store = MemoryStore(clock=clock)
    rule = Rule.parse("2/s", algorithm=AlgorithmName.TOKEN_BUCKET)

    # 1. First request
    res1 = store.evaluate("user1", rule, cost=1)
    assert res1.is_allowed is True
    assert res1.remaining == 1

    # 2. Second request
    res2 = store.evaluate("user1", rule, cost=1)
    assert res2.is_allowed is True
    assert res2.remaining == 0

    # 3. Third request -> exceeded
    res3 = store.evaluate("user1", rule, cost=1)
    assert res3.is_allowed is False

    # 4. Advance clock 1s -> refilled
    clock.advance(1000)
    res4 = store.evaluate("user1", rule, cost=1)
    assert res4.is_allowed is True

    # 5. Reset
    store.reset("user1", rule)
    res5 = store.evaluate("user1", rule, cost=1)
    assert res5.is_allowed is True
    assert res5.remaining == 1

    # 6. Clear and close
    store.clear()
    assert len(store) == 0
    store.close()


def test_memory_store_eviction() -> None:
    clock = ManualClock(start_ms=1000)
    # Store with max 2 keys
    store = MemoryStore(clock=clock, max_keys=2)
    rule = Rule.parse("5/s")

    store.evaluate("key1", rule)
    store.evaluate("key2", rule)
    assert len(store) == 2

    # Advance clock past TTL and evaluate key3
    clock.advance(5000)
    store.evaluate("key3", rule)
    # Expired keys should be dropped
    assert len(store) <= 2

    # Test overflow eviction when not expired
    store.clear()
    store.evaluate("a", rule)
    store.evaluate("b", rule)
    store.evaluate("c", rule)  # exceeds max_keys=2 -> forces eviction
    assert len(store) <= 2


def test_memory_store_concurrency() -> None:
    clock = ManualClock(start_ms=0)
    store = MemoryStore(clock=clock)
    # 50 tokens available
    rule = Rule(limit=50, window_ms=100_000, algorithm=AlgorithmName.TOKEN_BUCKET)

    allowed_count = 0
    lock = threading.Lock()

    def worker() -> None:
        nonlocal allowed_count
        res = store.evaluate("shared_key", rule, cost=1)
        if res.is_allowed:
            with lock:
                allowed_count += 1

    threads = [threading.Thread(target=worker) for _ in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Exactly 50 out of 100 requests must be allowed
    assert allowed_count == 50


@pytest.mark.asyncio
async def test_async_memory_store() -> None:
    clock = ManualClock(start_ms=1000)
    store = AsyncMemoryStore(clock=clock)
    rule = Rule.parse("3/s")

    res1 = await store.evaluate("async_user", rule)
    assert res1.is_allowed is True
    assert res1.remaining == 2

    await store.reset("async_user", rule)
    res2 = await store.evaluate("async_user", rule)
    assert res2.remaining == 2

    await store.clear()
    assert len(store) == 0
    await store.close()
