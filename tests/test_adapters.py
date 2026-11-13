from __future__ import annotations

import json
from collections.abc import MutableMapping
from typing import Any

import pytest

from charon.adapters.asgi import RateLimitMiddleware as ASGIMiddleware
from charon.adapters.wsgi import RateLimitMiddleware as WSGIMiddleware
from charon.engine import Limiter
from charon.storage.memory import AsyncMemoryStore, MemoryStore
from charon.types import Rule


@pytest.mark.asyncio
async def test_asgi_middleware_flow() -> None:
    store = AsyncMemoryStore()
    rule = Rule.parse("2/minute")
    limiter = Limiter(storage=store, rule=rule)

    # Simple downstream ASGI app
    async def downstream_app(
        scope: MutableMapping[str, Any],
        receive: Any,
        send: Any,
    ) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            }
        )
        await send({"type": "http.response.body", "body": b"OK", "more_body": False})

    middleware = ASGIMiddleware(downstream_app, limiter=limiter)

    scope: MutableMapping[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": "/test",
        "headers": [(b"host", b"localhost")],
        "client": ("127.0.0.1", 12345),
    }

    # 1. First request -> Allowed (200 OK with RateLimit-* headers)
    sent_messages_1: list[dict[str, Any]] = []

    async def send_1(msg: dict[str, Any]) -> None:
        sent_messages_1.append(msg)

    async def receive() -> dict[str, Any]:
        return {"type": "http.request"}

    await middleware(scope, receive, send_1)
    assert sent_messages_1[0]["status"] == 200
    headers_dict = {k.lower(): v for k, v in sent_messages_1[0]["headers"]}
    assert b"ratelimit-limit" in headers_dict
    assert b"ratelimit-remaining" in headers_dict

    # 2. Second request -> Allowed
    sent_messages_2: list[dict[str, Any]] = []

    async def send_2(msg: dict[str, Any]) -> None:
        sent_messages_2.append(msg)

    await middleware(scope, receive, send_2)
    assert sent_messages_2[0]["status"] == 200

    # 3. Third request -> Throttled (429 Too Many Requests)
    sent_messages_3: list[dict[str, Any]] = []

    async def send_3(msg: dict[str, Any]) -> None:
        sent_messages_3.append(msg)

    await middleware(scope, receive, send_3)
    assert sent_messages_3[0]["status"] == 429
    headers_3 = {k.lower(): v for k, v in sent_messages_3[0]["headers"]}
    assert b"retry-after" in headers_3
    body_data = json.loads(sent_messages_3[1]["body"].decode())
    assert body_data["error"] == "rate_limit_exceeded"


@pytest.mark.asyncio
async def test_asgi_middleware_non_http_and_exempt() -> None:
    store = AsyncMemoryStore()
    rule = Rule.parse("1/minute")
    limiter = Limiter(storage=store, rule=rule)

    called = False

    async def downstream_app(scope: Any, receive: Any, send: Any) -> None:
        nonlocal called
        called = True

    # 1. Non-http scope passes straight through
    middleware = ASGIMiddleware(downstream_app, limiter=limiter)
    await middleware({"type": "websocket"}, None, None)
    assert called is True

    # 2. Exempt request passes through
    called = False
    exempt_middleware = ASGIMiddleware(
        downstream_app,
        limiter=limiter,
        exempt=lambda req: req.path == "/health",
    )
    scope = {"type": "http", "path": "/health", "headers": []}
    await exempt_middleware(scope, None, None)
    assert called is True


def test_wsgi_middleware_flow() -> None:
    store = MemoryStore()
    rule = Rule.parse("1/minute")
    limiter = Limiter(storage=store, rule=rule)

    def downstream_wsgi(environ: dict[str, Any], start_response: Any) -> list[bytes]:
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [b"hello"]

    app = WSGIMiddleware(downstream_wsgi, limiter=limiter)

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/api",
        "REMOTE_ADDR": "192.0.2.1",
        "HTTP_HOST": "localhost",
    }

    # 1. First request -> 200 OK with RateLimit headers
    captured_status = ""
    captured_headers: list[tuple[str, str]] = []

    def start_response(status: str, headers: list[tuple[str, str]], *args: Any) -> None:
        nonlocal captured_status, captured_headers
        captured_status = status
        captured_headers = headers

    body1 = app(environ, start_response)
    assert captured_status == "200 OK"
    assert body1 == [b"hello"]
    header_names = [h[0] for h in captured_headers]
    assert "RateLimit-Limit" in header_names

    # 2. Second request -> 429 Too Many Requests
    body2 = app(environ, start_response)
    assert captured_status == "429 Too Many Requests"
    header_names_2 = [h[0] for h in captured_headers]
    assert "Retry-After" in header_names_2
    parsed_body = json.loads(body2[0].decode())
    assert parsed_body["error"] == "rate_limit_exceeded"
