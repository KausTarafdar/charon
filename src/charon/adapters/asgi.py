"""ASGI middleware (FastAPI, Starlette, Litestar, Quart, …).

Pure ASGI with no framework imports, so it mounts on anything speaking the spec.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from charon import headers as header_utils
from charon.engine import Limiter
from charon.keys import RequestInfo
from charon.types import RateLimitResult, Rule

__all__ = ["RateLimitMiddleware", "request_info_from_scope"]

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]

#: ``(scope, result) -> ASGI response`` override for throttled requests.
Responder = Callable[[Scope, RateLimitResult], dict[str, Any]]


def request_info_from_scope(scope: Scope) -> RequestInfo:
    """Normalise an ASGI HTTP scope into a :class:`RequestInfo`."""
    raw_headers = scope.get("headers") or []
    collected: dict[str, str] = {}
    for raw_name, raw_value in raw_headers:
        name = raw_name.decode("latin-1").lower()
        value = raw_value.decode("latin-1")
        # Repeated headers join with ", " per RFC 9110.
        collected[name] = f"{collected[name]}, {value}" if name in collected else value

    client = scope.get("client")
    peer_ip = client[0] if client else None

    # Anything the app's auth middleware attached is fair game as identity.
    state = dict(scope.get("state") or {})
    for key in ("user", "auth", "principal"):
        if key in scope:
            state.setdefault(key, scope[key])

    return RequestInfo(
        method=scope.get("method", "GET"),
        path=scope.get("path", "/"),
        headers=collected,
        peer_ip=peer_ip,
        state=state,
    )


class RateLimitMiddleware:
    """Throttles HTTP requests and annotates responses with ``RateLimit-*``.

    Non-HTTP scopes (lifespan, websocket) pass through untouched.
    """

    def __init__(
        self,
        app: Callable[[Scope, Receive, Send], Awaitable[None]],
        limiter: Limiter,
        *,
        rule: Rule | str | None = None,
        cost: int = 1,
        exempt: Callable[[RequestInfo], bool] | None = None,
        on_limited: Responder | None = None,
    ) -> None:
        self.app = app
        self.limiter = limiter
        self.rule = rule
        self.cost = cost
        self.exempt = exempt
        self.on_limited = on_limited or _default_limited_response

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request = request_info_from_scope(scope)
        if self.exempt is not None and self.exempt(request):
            await self.app(scope, receive, send)
            return

        result = await self.limiter.acheck_request(request, self.rule, cost=self.cost)

        if not result.is_allowed:
            await _send_response(send, self.on_limited(scope, result), result)
            return

        await self.app(scope, receive, _annotating_send(send, result))


def _annotating_send(send: Send, result: RateLimitResult) -> Send:
    """Wrap ``send`` so the outgoing response start carries the limit headers."""

    async def wrapped(message: Message) -> None:
        if message["type"] == "http.response.start":
            existing = list(message.get("headers") or [])
            existing.extend(header_utils.as_raw(result))
            message = {**message, "headers": existing}
        await send(message)

    return wrapped


def _default_limited_response(scope: Scope, result: RateLimitResult) -> dict[str, Any]:
    body = json.dumps(
        {
            "error": "rate_limit_exceeded",
            "detail": "Too Many Requests",
            "retry_after": result.retry_after_seconds,
        }
    ).encode("utf-8")
    return {
        "status": 429,
        "headers": [(b"content-type", b"application/json")],
        "body": body,
    }


async def _send_response(send: Send, response: dict[str, Any], result: RateLimitResult) -> None:
    body: bytes = response.get("body", b"")
    response_headers = list(response.get("headers") or [])
    response_headers.extend(header_utils.as_raw(result))
    response_headers.append((b"content-length", str(len(body)).encode("latin-1")))

    await send(
        {
            "type": "http.response.start",
            "status": response.get("status", 429),
            "headers": response_headers,
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})
