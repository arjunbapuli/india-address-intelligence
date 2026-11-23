"""Basic in-memory usage metrics (per-process).

Exposed at GET /metrics. Resets on restart. For production observability,
export these to Prometheus/OpenTelemetry (roadmap).
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.started_at = time.time()
        self.requests: dict[str, int] = defaultdict(int)
        self.status_codes: dict[str, int] = defaultdict(int)
        self.latency_ms_sum: dict[str, float] = defaultdict(float)
        self.latency_ms_max: dict[str, float] = defaultdict(float)
        self.resolution_status: dict[str, int] = defaultdict(int)
        self.per_client: dict[str, int] = defaultdict(int)
        self.addresses_processed = 0

    def record_request(self, route: str, status: int, latency_ms: float, client_id: str | None) -> None:
        with self._lock:
            self.requests[route] += 1
            self.status_codes[str(status)] += 1
            self.latency_ms_sum[route] += latency_ms
            self.latency_ms_max[route] = max(self.latency_ms_max[route], latency_ms)
            if client_id:
                self.per_client[client_id] += 1

    def record_resolution(self, status: str) -> None:
        with self._lock:
            self.resolution_status[status] += 1
            self.addresses_processed += 1

    def snapshot(self) -> dict:
        with self._lock:
            routes = {
                r: {
                    "count": n,
                    "avg_latency_ms": round(self.latency_ms_sum[r] / n, 2) if n else 0.0,
                    "max_latency_ms": round(self.latency_ms_max[r], 2),
                }
                for r, n in self.requests.items()
            }
            return {
                "uptime_seconds": round(time.time() - self.started_at, 1),
                "routes": routes,
                "status_codes": dict(self.status_codes),
                "resolution_status": dict(self.resolution_status),
                "addresses_processed": self.addresses_processed,
                "requests_per_client": dict(self.per_client),
            }

    def reset(self) -> None:
        self.__init__()


metrics = Metrics()
