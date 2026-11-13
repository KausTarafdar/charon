from __future__ import annotations

from charon.keys import (
    ApiKey,
    AuthenticatedUser,
    ClientIP,
    FirstOf,
    RequestInfo,
    Static,
    default_extractor,
)


def test_request_info_header_lookup() -> None:
    req = RequestInfo(headers={"content-type": "application/json", "x-custom": "val"})
    assert req.header("Content-Type") == "application/json"
    assert req.header("X-CUSTOM") == "val"
    assert req.header("missing") is None


def test_static_extractor() -> None:
    extractor = Static("global_rate")
    req = RequestInfo()
    assert extractor(req) == "global_rate"


def test_authenticated_user_extractor() -> None:
    extractor = AuthenticatedUser()

    # 1. State with dict user
    req_dict = RequestInfo(state={"user": {"id": "usr_123"}})
    assert extractor(req_dict) == "user:usr_123"

    # 2. State with principal object
    class UserObj:
        user_id = 456

    req_obj = RequestInfo(state={"principal": UserObj()})
    assert extractor(req_obj) == "user:456"

    # 3. State with raw string/int
    req_raw = RequestInfo(state={"auth": 789})
    assert extractor(req_raw) == "user:789"

    # 4. No matching user state
    req_empty = RequestInfo(state={})
    assert extractor(req_empty) is None


def test_api_key_extractor() -> None:
    extractor = ApiKey()

    # Raw secret must never be in output
    raw_secret = "secret-token-xyz-12345678"
    req_hdr = RequestInfo(headers={"x-api-key": raw_secret})
    extracted = extractor(req_hdr)
    assert extracted is not None
    assert extracted.startswith("key:")
    assert raw_secret not in extracted

    # Authorization Bearer
    req_bearer = RequestInfo(headers={"authorization": "Bearer my-jwt-token"})
    extracted_bearer = extractor(req_bearer)
    assert extracted_bearer is not None
    assert extracted_bearer.startswith("key:")
    assert "my-jwt-token" not in extracted_bearer

    # No key present
    req_none = RequestInfo()
    assert extractor(req_none) is None


def test_client_ip_extractor() -> None:
    # 1. Direct peer IP (default trusted_hops=0 ignores X-Forwarded-For)
    extractor = ClientIP(trusted_hops=0)
    req_direct = RequestInfo(
        peer_ip="192.0.2.1",
        headers={"x-forwarded-for": "10.0.0.1, 10.0.0.2"},
    )
    assert extractor(req_direct) == "ip:192.0.2.1"

    # 2. Trusted hops = 1 -> client is second from last (or last hop before proxy)
    extractor_hop1 = ClientIP(trusted_hops=1)
    req_xff1 = RequestInfo(
        peer_ip="127.0.0.1",
        headers={"x-forwarded-for": "203.0.113.195, 70.41.3.18"},
    )
    # chain has 2 entries; hop 1 (last hop) is 70.41.3.18, real client is 203.0.113.195
    assert extractor_hop1(req_xff1) == "ip:203.0.113.195"

    # 3. IPv4-mapped IPv6 normalization
    req_v6_mapped = RequestInfo(peer_ip="::ffff:192.0.2.100")
    assert extractor(req_v6_mapped) == "ip:192.0.2.100"

    # 4. Port stripping
    req_port = RequestInfo(peer_ip="192.0.2.1:54321")
    assert extractor(req_port) == "ip:192.0.2.1"

    req_v6_port = RequestInfo(peer_ip="[2001:db8::1]:8080")
    assert extractor(req_v6_port) == "ip:2001:db8::1"

    # 5. Invalid IP falls back to None
    req_invalid = RequestInfo(peer_ip="not-an-ip")
    assert extractor(req_invalid) is None


def test_first_of_and_default_extractor_hierarchy() -> None:
    extractor = default_extractor()

    # User identity takes highest priority
    req_all = RequestInfo(
        state={"user": {"id": "alice"}},
        headers={"x-api-key": "secret123"},
        peer_ip="192.0.2.1",
    )
    assert extractor(req_all) == "user:alice"

    # API key takes second priority
    req_key_ip = RequestInfo(
        headers={"x-api-key": "secret123"},
        peer_ip="192.0.2.1",
    )
    assert extractor(req_key_ip) is not None
    assert extractor(req_key_ip).startswith("key:")

    # IP takes third priority
    req_ip_only = RequestInfo(peer_ip="192.0.2.1")
    assert extractor(req_ip_only) == "ip:192.0.2.1"

    # Returns "anonymous" fallback if nothing present in default_extractor
    req_empty = RequestInfo()
    assert extractor(req_empty) == "anonymous"

    # FirstOf with fallback=None returns None
    no_fallback_extractor = FirstOf(AuthenticatedUser(), ApiKey(), ClientIP(), fallback=None)
    assert no_fallback_extractor(req_empty) is None
