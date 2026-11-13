# Charon : Python Agnostic Rate Limiter SDK

Author: Kaustav Tarafdar
Created on: 07-08-26 
Version: v0.1

## 1. Executive Summary

A lightweight, framework-agnostic Python Rate Limiter SDK designed to protect applications from traffic spikes and API abuse. It features an isolated core engine decoupled from transport protocols, pluggable storage engines, atomic concurrency controls via Redis Lua scripts, and native ASGI/WSGI adapters.

---

## 2. Architecture & System Topology

The engine isolates evaluation logic from transport layers, allowing identical limit rules across HTTP servers, background task queues, and RPC handlers.

```
┌─────────────────────────────────────────────────────────────────┐
│                   Transport / Application Layer                 │
│  ┌──────────────────┐    ┌──────────────────┐    ┌───────────┐  │
│  │  ASGI Middleware │    │  WSGI Middleware │    │  Celery / │  │
│  │ (FastAPI/Litestar)│   │  (Flask/Django)  │    │  gRPC /   │  │
│  └────────┬─────────┘    └────────┬─────────┘    │  CLI Tools│  │
└───────────┼───────────────────────┼──────────────┴─────┬─────┘  │
            │                       │                    │        │
            └───────────────────────┼────────────────────┘        │
                                    ▼                             │
┌─────────────────────────────────────────────────────────────────┤
│                        Core SDK Architecture                    │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                   Limiter Engine Core                    │   │
│  └────────────┬─────────────────────────────┬───────────────┘   │
│               │                             │                   │
│               ▼                             ▼                   │
│   ┌───────────────────────┐     ┌───────────────────────┐       │
│   │   Algorithm Strategy  │     │   Key Extraction      │       │
│   │ (Token/Sliding/Leaky) │     │   (IP/JWT/Header)     │       │
│   └───────────┬───────────┘     └───────────────────────┘       │
│               │                                                 │
│               ▼                                                 │
│   ┌─────────────────────────────────────────────────┐           │
│   │               Storage Engine Interface           │           │
│   │      ┌───────────────────┬───────────────────┐  │           │
│   │      │   InMemoryStore   │   RedisStore      │  │           │
│   │      │ (Thread-Safe Lock)│ (Lua Scripting)   │  │           │
│   │      └───────────────────┴───────────────────┘  │           │
│   └─────────────────────────────────────────────────┘           │
└─────────────────────────────────────────────────────────────────┘

```

---

## 3. Core Algorithms

* **Token Bucket (Lazy Refill):** Tokens refill on evaluation without background threads.

$$\text{Tokens}_{\text{new}} = \min\left(\text{Capacity}, \text{Tokens}_{\text{old}} + \Delta t \times \text{RefillRate}\right)$$


* **Sliding Window Counter:** Approximates real-time rate using previous and current window counts.

$$\text{Count} = N_{\text{current}} + N_{\text{previous}} \times (1 - \text{PercentageElapsed})$$


* **Leaky Bucket:** Enforces smooth output rate $R_{\text{out}}$ by buffering requests into a FIFO queue and discarding overflows.

---

## 4. Storage & Concurrency Control

### Atomic Redis Lua Execution

To eliminate check-then-set race conditions across distributed Python workers (e.g., Gunicorn/Uvicorn), evaluation runs inside a single Redis atomic transaction using sorted sets (`ZADD`, `ZREMRANGEBYSCORE`, `ZCARD`, `EXPIRE`).

### Fail-Over Behaviors

* **Fail-Open (Default):** On backend storage failure, log error telemetry and allow traffic through.
* **Fail-Closed:** On storage timeout/failure, immediately return HTTP `429` / drop request.

---

## 5. Web Protocols & Middleware Integration

### Header Standards (IETF Alignment)

Evaluated requests automatically return standard headers:

| Header | Value Description | Example |
| --- | --- | --- |
| `RateLimit-Limit` | Max requests allowed within window | `100` |
| `RateLimit-Remaining` | Remaining allowed requests in window | `0` |
| `RateLimit-Reset` | Time remaining until reset (in seconds) | `14` |
| `Retry-After` | Recommended wait time after throttling | `14` |

### Key Extraction Hierarchy

1. **Authenticated Context:** User ID extracted from validated JWT payload or session state.
2. **Client Token:** API Key extracted from standard headers (`X-API-Key`, `Authorization`).
3. **Network Identity:** Primary IP address (evaluated from `X-Forwarded-For` trusted proxies or direct socket addresses).

---

## 6. Data Interface Specifications

```python
from dataclasses import dataclass
from typing import Optional, Protocol

@dataclass(frozen=True)
class RateLimitResult:
    is_allowed: bool
    limit: int
    remaining: int
    reset_after_seconds: int
    retry_after_seconds: Optional[int] = None

class StorageBackend(Protocol):
    async def evaluate(self, key: str, limit: int, window_ms: int) -> RateLimitResult:
        ...

```

---

## 7. Advanced Features Roadmap

* **Dynamic Tier Rates:** Dynamically inject rate thresholds based on user metadata (e.g., `Free: 10/min`, `Enterprise: 10000/min`).
* **Adaptive Load Throttling:** Automatically reduce user rate limits when target server CPU/Memory metrics cross critical operational thresholds ($>85\%$).