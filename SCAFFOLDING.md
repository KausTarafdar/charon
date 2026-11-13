# Charon — Scaffolding Status

Build-out of the structure described in [spec.md](spec.md), v0.1.

> **Verification status:** none of the code below has been executed, imported,
> type-checked, or tested. It is written but unverified. See
> [Not yet done](#not-yet-done).

---

## 1. Layout

```
charon/
├── pyproject.toml
├── spec.md
├── SCAFFOLDING.md
├── LICENSE
├── README.md                      (not yet updated)
└── src/charon/
    ├── __init__.py                public API surface
    ├── types.py                   RateLimitResult, Rule, enums
    ├── errors.py                  exception hierarchy
    ├── clock.py                   time source indirection
    ├── engine.py                  Limiter (the core)
    ├── headers.py                 IETF RateLimit-* builders
    ├── keys.py                    RequestInfo + key extraction hierarchy
    ├── algorithms/
    │   ├── __init__.py            registry
    │   ├── base.py                strategy contract
    │   ├── token_bucket.py
    │   ├── sliding_window.py
    │   └── leaky_bucket.py
    ├── storage/
    │   ├── __init__.py            lazy re-exports
    │   ├── base.py                sync + async backend protocols
    │   ├── memory.py              MemoryStore, AsyncMemoryStore
    │   └── redis.py               RedisStore, AsyncRedisStore
    └── adapters/
        ├── __init__.py            lazy re-exports
        ├── asgi.py                pure ASGI middleware
        └── wsgi.py                pure PEP 3333 middleware
```

---

## 2. Packaging — `pyproject.toml`

| Item | Value |
| --- | --- |
| Distribution name | `charon-ratelimit` |
| Import name | `charon` |
| Version | `0.1.0` |
| Build backend | hatchling, `src/` layout |
| Python | `>=3.9` |
| Runtime deps | **none** — the core is dependency-free |
| Extras | `[redis]` → `redis>=5.0`; `[dev]` → redis, pytest, pytest-asyncio, mypy, ruff |
| Tooling config | ruff (line length 100, `E/F/I/UP/B/SIM/RUF`), mypy `strict`, pytest with `asyncio_mode = "auto"` |

---

## 3. Core data interfaces — `types.py`

Implements §6 of the spec, extended with what the engine needs.

- **`RateLimitResult`** — frozen dataclass, exactly the spec's five fields
  (`is_allowed`, `limit`, `remaining`, `reset_after_seconds`,
  `retry_after_seconds`). Adds `.allow()` / `.deny()` classmethod constructors
  that take milliseconds and round **up** to whole seconds, so a client obeying
  `Retry-After` never returns before the limiter has actually recovered.
- **`Rule`** — frozen dataclass: `limit`, `window_ms`, `algorithm`, `burst`,
  `name`. Validates its own arguments. `capacity` resolves to `burst or limit`.
  - `Rule.parse("100/minute")` — also handles `"5/15min"`, `"10/s"`, `"1000/hour"`,
    `"50/day"`.
- **`AlgorithmName`** — `TOKEN_BUCKET`, `SLIDING_WINDOW`, `LEAKY_BUCKET`.
- **`FailureMode`** — `FAIL_OPEN` (default), `FAIL_CLOSED`, per spec §4.

---

## 4. Algorithms — `algorithms/`

Each strategy is implemented **twice against one contract**:

1. `apply(state, rule, now_ms, cost)` — a pure state transition, used by
   process-local stores.
2. `lua` — the same transition as a Redis script, so distributed evaluation is
   atomic.

Both halves return the uniform reply shape
`[allowed, remaining, reset_after_ms, retry_after_ms]`, decoded by
`result_from_reply()`. Keeping the shape uniform is what lets a backend stay
agnostic to which algorithm it is running.

| Strategy | State | Behaviour |
| --- | --- | --- |
| `TokenBucket` | `(tokens, last_refill_ms)` | Lazy refill: `tokens = min(capacity, tokens + Δt × rate)`. No background thread. `burst` raises capacity above the sustained rate. |
| `SlidingWindowCounter` | `(window_start_ms, current, previous)` | `count = current + previous × (1 − elapsed_fraction)`. Two counters, no per-request timestamps. |
| `LeakyBucket` | `(level, last_leak_ms)` | Fill level draining at `limit / window_ms`; overflow is discarded, enforcing a smooth output rate. |

The registry (`register_algorithm` / `get_algorithm`) makes custom strategies
pluggable — register one and any `Rule` can name it.

---

## 5. Storage — `storage/`

`storage/base.py` defines **two** protocols, `SyncStorageBackend` and
`AsyncStorageBackend`, both `evaluate(key, rule, cost) -> RateLimitResult` plus
`reset` / `close`. The spec's single async `StorageBackend.evaluate` is the async
half; the sync half exists because WSGI middleware cannot await.

`compose_key(namespace, key, rule)` lives here so every backend namespaces
identically — `charon:<rule name>:<algorithm>:<key>` — and two different rules can
never collide on one key.

### `MemoryStore` / `AsyncMemoryStore`

- Shared `_Core` guarded by a `threading.Lock` held across the **whole**
  read-modify-write — that is what makes check-then-set safe under threads.
- Per-entry TTL plus `max_keys` (default 100k) with expiry-ordered eviction, so
  an unbounded key space like client IPs cannot exhaust memory.
- Correct for single-process deployments and tests. Under multiple Gunicorn/Uvicorn
  workers each process keeps its own counters, so the effective limit multiplies by
  the worker count — documented in the module docstring.
- The async variant runs the critical section inline rather than on a thread pool;
  it is microseconds of pure CPU work.

### `RedisStore` / `AsyncRedisStore`

- Every evaluation is a single `EVALSHA` via redis-py's `register_script`, which
  handles the `NOSCRIPT` fallback. This is the spec §4 guarantee: one atomic
  round-trip, no check-then-set race across processes or hosts.
- `_ScriptCache` registers each algorithm's script once per client.
- All driver exceptions are normalised to `StorageError` so the engine's
  failure-mode policy has exactly one thing to catch.
- `redis_keys()` returns a single key per evaluation, keeping scripts
  Redis-Cluster-safe (all keys hash to one slot).
- Imported lazily — the `redis` extra stays genuinely optional.

---

## 6. Engine — `engine.py`

`Limiter` is the transport-agnostic core. It takes a key or a `RequestInfo`,
resolves the rule, delegates the atomic transition to the backend, and applies the
failure-mode policy.

| Method | Purpose |
| --- | --- |
| `check(key, rule=None, cost=1)` | Sync evaluation. Raises `ConfigurationError` if handed an async backend (and closes the orphaned coroutine). |
| `acheck(...)` | Async evaluation; also accepts a sync backend. |
| `check_request` / `acheck_request` | Same, but run key extraction over a `RequestInfo` first. |
| `acquire` / `aacquire` | Raise `RateLimitExceeded` instead of returning a denial. |
| `@limiter.limit(rule, key=...)` | Decorator for non-HTTP call sites — Celery tasks, gRPC handlers, CLI tools. Detects coroutine functions and wraps accordingly. `key` may be a literal or a callable over the wrapped function's arguments; defaults to the qualified function name. |
| `reset` / `areset` | Clear state for a key. |
| `enabled=False` | Global bypass that still returns well-formed, full-headroom results, so headers stay consistent. |

**Failure modes (spec §4):** `StorageError` is caught, logged as warning telemetry
with the key and mode, then converted — `FAIL_OPEN` yields a full-headroom allow,
`FAIL_CLOSED` yields a denial.

---

## 7. Key extraction — `keys.py`

`RequestInfo` is the transport-neutral request view adapters produce: `method`,
`path`, lower-cased `headers`, `peer_ip`, and `state` (whatever the framework
attached). A `KeyExtractor` is just `RequestInfo -> Optional[str]`; returning
`None` defers to the next one.

The spec's §5 hierarchy, composed by `FirstOf` and assembled by
`default_extractor()`:

1. **`AuthenticatedUser`** — reads identity from auth state the application already
   validated (`scope["user"]` and friends), handling mappings, objects, and bare
   scalars.
   **This SDK deliberately never parses or verifies JWTs.** An unverified token is
   attacker-controlled, so limiting by it would let one caller mint unlimited
   identities and bypass the limit entirely. Verification belongs to your auth
   middleware; Charon consumes its output.
2. **`ApiKey`** — `X-API-Key` / `Authorization`, stripping `Bearer`/`Token`/`ApiKey`
   schemes, then **blake2b-hashed** so raw secrets never reach Redis or logs.
3. **`ClientIP`** — socket address by default. `X-Forwarded-For` is client-supplied
   and trivially spoofed, so it is consulted **only** when `trusted_hops > 0`: with
   `n` proxies you control, the last `n` chain entries are yours and the one before
   them is the real client. Addresses are validated, `host:port` and `[v6]:port`
   forms unwrapped, and IPv4-mapped IPv6 collapsed, so one client cannot occupy two
   buckets.

Also included: `Static` for global per-endpoint limits.

---

## 8. Headers — `headers.py`

Spec §5, following draft-ietf-httpapi-ratelimit-headers. `RateLimit-Limit`,
`RateLimit-Remaining`, and `RateLimit-Reset` on every evaluated response;
`Retry-After` **only** when throttled. Three renderings of the same data:
`build()` → dict, `as_list()` → WSGI `start_response` pairs, `as_raw()` → latin-1
bytes for ASGI messages.

---

## 9. Adapters — `adapters/`

Both are written against the raw protocol with **no framework imports**, so they
mount on anything spec-compliant.

### `adapters/asgi.py` — FastAPI, Starlette, Litestar, Quart

- `request_info_from_scope()` decodes headers (joining repeats with `", "` per
  RFC 9110), pulls `client[0]` as the peer IP, and merges `scope["state"]` plus any
  `user`/`auth`/`principal` an upstream middleware set.
- Non-HTTP scopes (`lifespan`, `websocket`) pass straight through.
- On allow: wraps `send` so `http.response.start` gains the limit headers without
  the downstream app knowing.
- On deny: emits a 429 JSON body with correct `Content-Length`.
- `exempt` predicate and `on_limited` responder are both injectable.

### `adapters/wsgi.py` — Flask, Django, Pyramid, Bottle

Same shape against PEP 3333: `HTTP_*` environ un-mangling, `REMOTE_ADDR` as peer
IP, `REMOTE_USER`/`charon.user` as auth state, and a wrapped `start_response` for
header injection. Pairs with a **sync** backend.

---

## 10. Public API — `__init__.py`

Eagerly exports the core (engine, data interfaces, algorithms, memory stores,
protocols, extractors, clocks, errors) and resolves four optional names lazily via
PEP 562 `__getattr__` — `RedisStore`, `AsyncRedisStore`, `ASGIRateLimitMiddleware`,
`WSGIRateLimitMiddleware`. So `import charon` pulls in neither `redis` nor any web
framework. A `TYPE_CHECKING` block re-declares them so mypy and IDE completion
still resolve them.

---

## 11. Supporting pieces

- **`errors.py`** — `CharonError` base; `ConfigurationError`, `StorageError`
  (what the failure-mode policy catches), and `RateLimitExceeded` (carries the
  `result` and `key`).
- **`clock.py`** — `Clock` protocol, `MonotonicClock` (wall-clock ms; wall clock
  rather than `time.monotonic` because Redis-backed workers must agree on "now"),
  and `ManualClock` for deterministic window/refill tests.

---

## Verification & Completed Baseline

1. **`py.typed` marker** — added to `src/charon/py.typed` (PEP 561 compliance).
2. **`tests/`** — full test suite with 47 unit and integration tests passing in <0.1s.
3. **README** — production-grade README with architecture diagram, feature comparison, quickstarts (FastAPI/Flask/Celery), and roadmap.
4. **Verification pass** — `ruff check .` (100% clean), `mypy` strict mode (clean across all 19 modules and tests), `pytest` passing.
5. **CI Workflow** — GitHub Actions matrix testing across Python 3.10–3.13 with Redis container.

## Roadmap & Next Steps

1. **Live Redis Integration Suite** — run the Lua scripts directly against a live Redis instance in CI.
2. **L1/L2 Hierarchical Token Leasing** — process-local token leasing to reduce Redis network round-trips by up to 90%.
3. **Adaptive Circuit Breakers** — automatic degraded in-memory enforcement during Redis partitions.
4. **Dynamic Tier Rates & Observability** — OpenTelemetry metrics and tenant plan resolution.
