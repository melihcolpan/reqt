"""Per-host request rate limiting."""

from __future__ import annotations

import asyncio
import re
import time
from typing import Dict, Optional, Tuple, Union

from yarl import URL


def host_key(url: str) -> Optional[str]:
    """``host:port`` of a URL, or None when it has no host (the request will fail anyway)."""
    try:
        parsed = URL(url)
    except (TypeError, ValueError):
        return None
    if not parsed.host:
        return None
    return f"{parsed.host}:{parsed.port}"


RateLimit = Union[float, int, str, Tuple[float, float]]

_UNITS = {
    "s": 1.0, "sec": 1.0, "second": 1.0, "seconds": 1.0,
    "m": 60.0, "min": 60.0, "minute": 60.0, "minutes": 60.0,
    "h": 3600.0, "hr": 3600.0, "hour": 3600.0, "hours": 3600.0,
    "d": 86400.0, "day": 86400.0, "days": 86400.0,
}  # fmt: skip


def parse_rate(rate: RateLimit) -> float:
    """Requests per second from ``5``, ``"100/min"``, ``"30/5min"``, ``"1000/h"`` or ``(100, 60)``."""
    if isinstance(rate, bool):
        raise TypeError("rate_limit must be a number, a string such as '100/min', or (count, seconds)")
    if isinstance(rate, (int, float)):
        per_second = float(rate)
    elif isinstance(rate, tuple):
        count, seconds = rate
        per_second = float(count) / float(seconds)
    elif isinstance(rate, str):
        count_text, _, period = rate.replace(" ", "").partition("/")
        match = re.fullmatch(r"(\d*\.?\d*)([a-z]+)", period.lower())
        if not count_text or not match or match.group(2) not in _UNITS:
            raise ValueError(f"invalid rate_limit {rate!r}; use for example '10/s', '100/min' or '1000/h'")
        multiplier = float(match.group(1)) if match.group(1) else 1.0
        per_second = float(count_text) / (multiplier * _UNITS[match.group(2)])
    else:
        raise TypeError("rate_limit must be a number, a string such as '100/min', or (count, seconds)")
    if not per_second > 0:
        raise ValueError("rate_limit must be greater than 0")
    return per_second


class HostRateLimiter:
    """Spaces out requests evenly so that each host gets at most the given rate."""

    def __init__(self, rate: RateLimit) -> None:
        self._interval = 1.0 / parse_rate(rate)
        self._next_slot: Dict[str, float] = {}

    async def wait(self, url: str) -> None:
        host = host_key(url)
        if host is None:
            return
        now = time.monotonic()
        # Reserve the next free slot synchronously, so concurrent callers never share one.
        slot = max(now, self._next_slot.get(host, now))
        self._next_slot[host] = slot + self._interval
        if slot > now:
            await asyncio.sleep(slot - now)
