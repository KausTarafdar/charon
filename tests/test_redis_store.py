from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from charon.errors import ConfigurationError, StorageError
from charon.storage.redis import AsyncRedisStore, RedisStore, _ScriptCache
from charon.types import Rule


def test_redis_store_missing_client_and_url() -> None:
    with pytest.raises(ConfigurationError, match="requires either a client or a url"):
        RedisStore()

    with pytest.raises(ConfigurationError, match="requires either a client or a url"):
        AsyncRedisStore()


def test_redis_store_evaluation_with_mock_client() -> None:
    mock_client = MagicMock()
    mock_script = MagicMock()
    # Mock EVAL reply: [allowed=1, remaining=4, reset_ms=1000, retry_ms=0]
    mock_script.return_value = [1, 4, 1000, 0]
    mock_client.register_script.return_value = mock_script

    store = RedisStore(client=mock_client)
    rule = Rule.parse("5/minute")

    res = store.evaluate("user123", rule)
    assert res.is_allowed is True
    assert res.remaining == 4
    assert res.limit == 5

    # Check script registered and executed with keys and args
    mock_client.register_script.assert_called_once()
    mock_script.assert_called_once()


def test_redis_store_error_normalization() -> None:
    mock_client = MagicMock()
    mock_script = MagicMock()
    mock_script.side_effect = RuntimeError("Redis connection broken")
    mock_client.register_script.return_value = mock_script

    store = RedisStore(client=mock_client)
    rule = Rule.parse("5/minute")

    with pytest.raises(StorageError, match="redis evaluation failed"):
        store.evaluate("user123", rule)


def test_redis_store_reset() -> None:
    mock_client = MagicMock()
    store = RedisStore(client=mock_client)
    rule = Rule.parse("5/minute")

    store.reset("user123", rule)
    mock_client.delete.assert_called_once()


def test_script_cache() -> None:
    cache = _ScriptCache()
    mock_client = MagicMock()
    mock_client.register_script.return_value = "script_obj"

    class DummyAlgo:
        class DummyName:
            value = "test_algo"

        name = DummyName()
        lua = "-- lua code"

    s1 = cache.get(mock_client, DummyAlgo())
    s2 = cache.get(mock_client, DummyAlgo())
    assert s1 == "script_obj"
    assert s2 == "script_obj"
    mock_client.register_script.assert_called_once()
