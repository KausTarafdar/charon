"""Key extraction.

Adapters normalise a transport request into :class:`RequestInfo`; extractors turn
that into the string the limiter counts against. The default hierarchy follows
the spec: authenticated identity, then client token, then network identity.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "AuthenticatedUser",
    "ApiKey",
    "ClientIP",
    "FirstOf",
    "KeyExtractor",
    "RequestInfo",
    "Static",
    "default_extractor",
]


@dataclass(frozen=True)
class RequestInfo:
    """Transport-neutral view of an inbound request.

    ``headers`` keys are lower-cased by the adapter. ``state`` carries whatever
    the framework attached to the request (e.g. ``scope["user"]``), which is how
    authenticated identity reaches the limiter without this SDK parsing tokens.
    """

    method: str = "GET"
    path: str = "/"
    headers: Mapping[str, str] = field(default_factory=dict)
    peer_ip: str | None = None
    state: Mapping[str, Any] = field(default_factory=dict)

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())


#: Returns the identity for a request, or ``None`` to defer to the next extractor.
KeyExtractor = Callable[[RequestInfo], str | None]


@dataclass(frozen=True)
class Static:
    """Constant key — useful for a global limit on an endpoint."""

    value: str

    def __call__(self, request: RequestInfo) -> str | None:
        return self.value


@dataclass(frozen=True)
class AuthenticatedUser:
    """Identity from validated auth state that the application already set.

    This SDK deliberately does not parse or verify JWTs: an unverified token is
    attacker-controlled, so limiting by it would let a caller mint unlimited
    identities. Point ``state_keys`` at your auth middleware's output instead.
    """

    state_keys: Sequence[str] = ("user", "auth", "principal")
    attributes: Sequence[str] = ("id", "sub", "user_id", "username")
    prefix: str = "user"

    def __call__(self, request: RequestInfo) -> str | None:
        for state_key in self.state_keys:
            principal = request.state.get(state_key)
            if principal is None:
                continue
            identity = self._identify(principal)
            if identity is not None:
                return f"{self.prefix}:{identity}"
        return None

    def _identify(self, principal: Any) -> str | None:
        if isinstance(principal, (str, int)):
            return str(principal)
        if isinstance(principal, Mapping):
            for attribute in self.attributes:
                value = principal.get(attribute)
                if value is not None:
                    return str(value)
            return None
        for attribute in self.attributes:
            value = getattr(principal, attribute, None)
            if value is not None:
                return str(value)
        return None


@dataclass(frozen=True)
class ApiKey:
    """Client token from a header, hashed so raw secrets never reach storage."""

    headers: Sequence[str] = ("x-api-key", "authorization")
    prefix: str = "key"
    #: Bearer/Basic scheme prefixes stripped before hashing.
    schemes: Sequence[str] = ("bearer ", "token ", "apikey ")

    def __call__(self, request: RequestInfo) -> str | None:
        for header in self.headers:
            raw = request.header(header)
            if not raw:
                continue
            token = raw.strip()
            lowered = token.lower()
            for scheme in self.schemes:
                if lowered.startswith(scheme):
                    token = token[len(scheme) :].strip()
                    break
            if token:
                return f"{self.prefix}:{_digest(token)}"
        return None


@dataclass(frozen=True)
class ClientIP:
    """Network identity, resolved through trusted proxies.

    ``X-Forwarded-For`` is client-supplied and trivially spoofed, so it is only
    consulted when ``trusted_hops`` is set: with ``n`` reverse proxies in front of
    the app, the last ``n`` entries were appended by infrastructure you control,
    and the entry just before them is the real client. ``trusted_hops=0`` (the
    default) uses the socket address only.
    """

    trusted_hops: int = 0
    forwarded_headers: Sequence[str] = ("x-forwarded-for",)
    real_ip_header: str | None = None
    prefix: str = "ip"

    def __call__(self, request: RequestInfo) -> str | None:
        address = self._resolve(request)
        return f"{self.prefix}:{address}" if address else None

    def _resolve(self, request: RequestInfo) -> str | None:
        if self.trusted_hops > 0:
            if self.real_ip_header:
                candidate = _normalise(request.header(self.real_ip_header))
                if candidate:
                    return candidate
            for header in self.forwarded_headers:
                raw = request.header(header)
                if not raw:
                    continue
                chain = [part.strip() for part in raw.split(",") if part.strip()]
                # Drop the hops our own infrastructure appended, then take the
                # right-most remaining entry.
                trusted_index = len(chain) - self.trusted_hops - 1
                if 0 <= trusted_index < len(chain):
                    candidate = _normalise(chain[trusted_index])
                    if candidate:
                        return candidate
        return _normalise(request.peer_ip)


@dataclass(frozen=True)
class FirstOf:
    """Try each extractor in order and use the first that yields a key.

    This is the spec's key-extraction hierarchy: authenticated context, then
    client token, then network identity.
    """

    extractors: tuple[KeyExtractor, ...]
    fallback: str | None = "anonymous"

    def __init__(self, *extractors: KeyExtractor, fallback: str | None = "anonymous") -> None:
        object.__setattr__(self, "extractors", tuple(extractors))
        object.__setattr__(self, "fallback", fallback)

    def __call__(self, request: RequestInfo) -> str | None:
        for extractor in self.extractors:
            key = extractor(request)
            if key:
                return key
        return self.fallback


def default_extractor(trusted_hops: int = 0) -> KeyExtractor:
    """The spec's three-tier hierarchy with sensible defaults."""
    return FirstOf(AuthenticatedUser(), ApiKey(), ClientIP(trusted_hops=trusted_hops))


def _normalise(address: str | None) -> str | None:
    """Validate and canonicalise an address, collapsing IPv4-mapped IPv6 forms."""
    if not address:
        return None
    candidate = address.strip()
    if candidate.startswith("[") and "]" in candidate:  # [::1]:443
        candidate = candidate[1 : candidate.index("]")]
    elif candidate.count(":") == 1:  # 1.2.3.4:5678
        candidate = candidate.split(":", 1)[0]
    try:
        parsed = ipaddress.ip_address(candidate)
    except ValueError:
        return None
    mapped = getattr(parsed, "ipv4_mapped", None)
    return str(mapped or parsed)


def _digest(token: str) -> str:
    from hashlib import blake2b  # noqa: PLC0415

    return blake2b(token.encode("utf-8"), digest_size=16).hexdigest()
