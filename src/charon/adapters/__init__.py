"""Transport adapters.

Imported lazily by name so that ``import charon`` never pulls in a transport you
are not using.
"""

from __future__ import annotations

__all__ = ["ASGIRateLimitMiddleware", "WSGIRateLimitMiddleware"]


def __getattr__(name: str) -> object:
    if name == "ASGIRateLimitMiddleware":
        from charon.adapters.asgi import RateLimitMiddleware as asgi_mw  # noqa: PLC0415

        return asgi_mw
    if name == "WSGIRateLimitMiddleware":
        from charon.adapters.wsgi import RateLimitMiddleware as wsgi_mw  # noqa: PLC0415

        return wsgi_mw
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
