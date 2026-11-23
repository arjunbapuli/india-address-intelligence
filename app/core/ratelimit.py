"""In-process token-bucket rate limiter.

Good enough for a single instance. For multiple instances, swap this for a
Redis-backed limiter (same interface) — see docs/deployment.md.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from app.core.config import get_settings
from app.core.errors import ApiError


@dataclass
class _Bucket:
    tokens: float
    updated: float


class TokenBucketLimiter:
    def __init__(self, rate_per_minute: int, burst: int):
        self.rate_per_sec = rate_per_minute / 60.0
        self.capacity = float(max(burst, 1))
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def try_acquire(self, client_id: str, cost: float = 1.0) -> tuple[bool, float]:
        """Returns (allowed, retry_after_seconds)."""
        now = time.monotonic()
        with self._lock:
            b = self._buckets.get(client_id)
            if b is None:
                b = _Bucket(tokens=self.capacity, updated=now)
                self._buckets[client_id] = b
            b.tokens = min(self.capacity, b.tokens + (now - b.updated) * self.rate_per_sec)
            b.updated = now
            # A single request larger than the bucket (big batch) is allowed if the bucket is full.
            effective_cost = min(cost, self.capacity)
            if b.tokens >= effective_cost:
                b.tokens -= effective_cost
                return True, 0.0
            deficit = effective_cost - b.tokens
            return False, deficit / self.rate_per_sec if self.rate_per_sec > 0 else 60.0

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


_limiter: TokenBucketLimiter | None = None


def get_limiter() -> TokenBucketLimiter:
    global _limiter
    if _limiter is None:
        s = get_settings()
        _limiter = TokenBucketLimiter(s.rate_limit_per_minute, s.rate_limit_burst)
    return _limiter


def reset_limiter() -> None:
    global _limiter
    _limiter = None


def enforce_rate_limit(client_id: str, cost: float = 1.0) -> None:
    allowed, retry_after = get_limiter().try_acquire(client_id, cost)
    if not allowed:
        raise ApiError(429, "rate_limited", "Rate limit exceeded. Retry later.",
                       details={"retry_after_seconds": round(retry_after, 1)},
                       headers={"Retry-After": str(max(1, int(retry_after + 0.999)))})
