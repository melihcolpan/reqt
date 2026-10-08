"""Estimate how long a batch will take before sending it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ._limits import RateLimit, parse_rate
from ._progress import format_duration


@dataclass(frozen=True)
class Estimate:
    """How long ``requests`` requests should take, and which setting limits them."""

    requests: int
    seconds: float
    limited_by: str

    def __str__(self) -> str:
        return (
            f"{self.requests} requests: about {format_duration(self.seconds)} (limited by {self.limited_by})"
        )


def estimate(
    requests: int,
    *,
    rate_limit: Optional[RateLimit] = None,
    hosts: int = 1,
    concurrency: int = 100,
    concurrency_per_host: int = 0,
    latency: float = 0.5,
) -> Estimate:
    """Estimate the duration of a batch, without sending anything.

        >>> print(reqstorm.estimate(7000, rate_limit="100/min"))
        7000 requests: about 1h 10m (limited by rate_limit)

    Args:
        requests: Number of requests.
        rate_limit: The per-host rate limit you plan to use (same formats as ``fetch_all``).
        hosts: How many different hosts the requests are spread evenly over.
        concurrency, concurrency_per_host: The limits you plan to use.
        latency: Typical seconds per request (response time); 0.5 by default.

    Retries are not included; they add time for requests that fail.
    """
    if requests < 0 or hosts < 1 or concurrency < 1 or latency < 0:
        raise ValueError("requests must be >= 0, hosts and concurrency >= 1, latency >= 0")
    in_flight = concurrency
    if concurrency_per_host:
        in_flight = min(in_flight, concurrency_per_host * hosts)
    candidates = {"concurrency": requests * latency / in_flight}
    if rate_limit is not None:
        per_host = requests / hosts
        candidates["rate_limit"] = max(per_host - 1, 0) / parse_rate(rate_limit) + latency
    limited_by = max(candidates, key=lambda name: candidates[name])
    return Estimate(requests=requests, seconds=candidates[limited_by], limited_by=limited_by)
