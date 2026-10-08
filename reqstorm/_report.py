"""Statistics of a batch: response times, statuses and per-host figures."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import TYPE_CHECKING, Any, Dict, List

from ._limits import host_key

if TYPE_CHECKING:
    from ._client import Result

__all__ = ["Report"]


def _percentile(sorted_values: List[float], fraction: float) -> float:
    if not sorted_values:
        return 0.0
    index = max(math.ceil(fraction * len(sorted_values)) - 1, 0)
    return sorted_values[index]


class Report:
    """Accumulates results and summarises them; memory grows with the number of results' timings only."""

    def __init__(self) -> None:
        self.total = 0
        self.ok = 0
        self.attempts = 0
        self.from_cache = 0
        self.statuses: Counter = Counter()
        self.errors: Counter = Counter()
        self.times: List[float] = []
        self.hosts: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"requests": 0, "ok": 0, "failed": 0, "times": []}
        )

    def add(self, result: Result) -> None:
        self.total += 1
        self.attempts += result.attempts
        self.from_cache += 1 if result.from_cache else 0
        if result.ok:
            self.ok += 1
        if result.error is not None:
            self.errors[type(result.error).__name__] += 1
        elif result.status is not None:
            self.statuses[result.status] += 1
        host = self.hosts[host_key(result.url) or "invalid"]
        host["requests"] += 1
        host["ok" if result.ok else "failed"] += 1
        if not result.from_cache:
            self.times.append(result.elapsed)
            host["times"].append(result.elapsed)

    @staticmethod
    def _latency(times: List[float]) -> Dict[str, float]:
        ordered = sorted(times)
        return {
            "min": round(ordered[0], 4) if ordered else 0.0,
            "p50": round(_percentile(ordered, 0.50), 4),
            "p90": round(_percentile(ordered, 0.90), 4),
            "p95": round(_percentile(ordered, 0.95), 4),
            "p99": round(_percentile(ordered, 0.99), 4),
            "max": round(ordered[-1], 4) if ordered else 0.0,
            "mean": round(sum(ordered) / len(ordered), 4) if ordered else 0.0,
        }

    def as_dict(self) -> Dict[str, Any]:
        """``total``, ``ok``, ``failed``, ``attempts``, ``retries``, ``from_cache``, ``latency``
        (seconds per request, including retries), ``statuses``, ``errors`` and ``hosts``."""
        return {
            "total": self.total,
            "ok": self.ok,
            "failed": self.total - self.ok,
            "attempts": self.attempts,
            "retries": max(self.attempts - (self.total - self.from_cache), 0),
            "from_cache": self.from_cache,
            "latency": self._latency(self.times),
            "statuses": dict(sorted(self.statuses.items())),
            "errors": dict(self.errors.most_common()),
            "hosts": {
                host: {
                    "requests": data["requests"],
                    "ok": data["ok"],
                    "failed": data["failed"],
                    "latency": self._latency(data["times"]),
                }
                for host, data in sorted(self.hosts.items(), key=lambda item: -item[1]["requests"])
            },  # fmt: skip
        }
