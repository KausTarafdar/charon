"""WSGI middleware (Flask, Django, Pyramid, Bottle, …).

Pure PEP 3333 with no framework imports. Pair it with a sync storage backend —
:class:`~charon.storage.memory.MemoryStore` or
:class:`~charon.storage.redis.RedisStore`.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import Any

from charon import headers as header_utils
from charon.engine import Limiter
from charon.keys import RequestInfo
from charon.types import RateLimitResult, Rule

__all__ = ["RateLimitMiddleware", "request_info_from_environ"]

Environ = dict[str, Any]
StartResponse = Callable[[str, list[tuple[str, str]]], Any]
WSGIApp = Callable[[Environ, StartResponse], Iterable[bytes]]

#: ``(environ, result) -> (status, headers, body)`` override for throttled requests.
Responder = Callable[[Environ, RateLimitResult], tuple[str, list[tuple[str, str]], bytes]]


def request_info_from_environ(environ: Environ) -> RequestInfo:
    """Normalise a WSGI environ into a :class:`RequestInfo`."""
    collected: dict[str, str] = {}
    for name, value in environ.items():
        if name.startswith("HTTP_"):
            collected[name[5:].replace("_", "-").lower()] = value
    for name in ("CONTENT_TYPE", "CONTENT_LENGTH"):
        if name in environ:
            collected[name.replace("_", "-").lower()] = environ[name]

    state = {}
    for key in ("charon.user", "charon.auth", "web3.user", "REMOTE_USER"):
        if environ.get(key):
            state["user"] = environ[key]
            break

    return RequestInfo(
        method=environ.get("REQUEST_METHOD", "GET"),
        path=environ.get("PATH_INFO", "/"),
        headers=collected,
        peer_ip=environ.get("REMOTE_ADDR"),
        state=state,
    )


class RateLimitMiddleware:
    """Throttles HTTP requests and annotates responses with ``RateLimit-*``."""

    def __init__(
        self,
        app: WSGIApp,
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

    def __call__(self, environ: Environ, start_response: StartResponse) -> Iterable[bytes]:
        request = request_info_from_environ(environ)
        if self.exempt is not None and self.exempt(request):
            return self.app(environ, start_response)

        result = self.limiter.check_request(request, self.rule, cost=self.cost)

        if not result.is_allowed:
            status, response_headers, body = self.on_limited(environ, result)
            response_headers = list(response_headers)
            response_headers.extend(header_utils.as_list(result))
            response_headers.append(("Content-Length", str(len(body))))
            start_response(status, response_headers)
            return [body]

        def annotating_start_response(
            status: str, response_headers: list[tuple[str, str]], *args: Any
        ) -> Any:
            merged = list(response_headers) + header_utils.as_list(result)
            return start_response(status, merged, *args)

        return self.app(environ, annotating_start_response)


def _default_limited_response(
    environ: Environ, result: RateLimitResult
) -> tuple[str, list[tuple[str, str]], bytes]:
    body = json.dumps(
        {
            "error": "rate_limit_exceeded",
            "detail": "Too Many Requests",
            "retry_after": result.retry_after_seconds,
        }
    ).encode("utf-8")
    return "429 Too Many Requests", [("Content-Type", "application/json")], body
