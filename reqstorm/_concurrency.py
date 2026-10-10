"""concurrency="auto": find how many requests at once the servers handle well."""

from __future__ import annotations

import asyncio
import statistics
import time
from typing import Callable, Dict, List, Optional

from ._limits import host_key

__all__ = ["AutoConcurrency"]

_OVERLOAD_STATUSES = frozenset({429, 502, 503, 504})
_SLOW = 2.0  # a response this many times slower than the host's normal counts as slow
_OVERLOADED = 0.05  # share of attempts with an overload signal that triggers a backoff
_MIN_WINDOW = 0.25  # seconds
_HOLD_WINDOWS = 8  # healthy windows just below the ceiling before probing it again


class AutoConcurrency:
    """Limits the attempts on the wire at once, and adjusts the limit from the responses.

    The limit counts attempts being sent or awaiting a response. A request waiting to retry
    does not hold a place, so the limit is what the servers actually see.

    It works like TCP congestion control:

    - **Back off** to three quarters when the servers push back: more than 5 % of recent
      attempts met a 429, 502, 503 or 504 response, a timeout or a dropped connection. This
      does not wait for the end of a window: as soon as a quarter of the limit has been
      rejected, the limit drops, so a server that rejects instantly is not flooded. Each
      backoff starts a new epoch; attempts sent before it are not counted afterwards, so one
      burst is not punished twice.
    - **Ease off** to nine tenths when the median response of a window takes more than twice
      as long as that host's normal (its fastest typical time).
    - **Grow** once per window (at least 0.25 s and three times the recent response time),
      and only when the window used the whole limit: double it until the first backoff
      ("slow start"), then add about 10 %. A limit that is never reached says nothing about
      whether a higher one would work.
    - **Remember the ceiling**, the limit at which the servers last pushed back. Growth stops
      just below it and probes it again only after a few healthy windows (as TCP CUBIC does).
      A probe without pushback lifts the ceiling.
    """

    def __init__(self, maximum: int, start: int = 8, clock: Callable[[], float] = time.monotonic) -> None:
        if maximum < 1:
            raise ValueError("max_concurrency must be at least 1")
        self.maximum = maximum
        self.limit = min(start, maximum)
        self.slow_start = True
        self.lowest = self.highest = self.limit
        self.changes = 0
        self.active = 0
        self.epoch = 0
        self.ceiling: Optional[int] = None
        self._holding = 0
        self._clock = clock
        self._condition: Optional[asyncio.Condition] = None
        self._normal: Dict[str, float] = {}
        self._latencies: List[float] = []
        self._window_started = clock()
        self._attempts = self._overloads = self._peak = 0
        self._ratios: List[float] = []

    # -- the gate ----------------------------------------------------------------------

    async def acquire(self) -> int:
        """Wait for a place; returns the epoch to pass back to ``attempt``."""
        if self._condition is None:
            self._condition = asyncio.Condition()
        async with self._condition:
            while self.active >= self.limit:
                await self._condition.wait()
            self.active += 1
            self._peak = max(self._peak, self.active)
            return self.epoch

    async def release(self) -> None:
        assert self._condition is not None
        async with self._condition:
            self.active -= 1
            self._condition.notify_all()

    # -- learning ----------------------------------------------------------------------

    def _new_window(self) -> None:
        self._window_started = self._clock()
        self._attempts = self._overloads = 0
        self._ratios = []
        self._peak = self.active

    def _window(self) -> float:
        if not self._latencies:
            return _MIN_WINDOW
        return max(_MIN_WINDOW, 3 * statistics.median(self._latencies[-50:]))

    def attempt(
        self, url: str, status: Optional[int], error: Optional[BaseException], elapsed: float, epoch: int
    ) -> Optional[str]:
        """Learn from one finished attempt. Returns a description when the limit changed."""
        host = host_key(url)
        if host is None or epoch < self.epoch:
            return None  # an invalid URL, or sent before the last backoff
        self._attempts += 1
        if status in _OVERLOAD_STATUSES or (error is not None and _is_overload(error)):
            self._overloads += 1
        elif status is not None and elapsed > 0:
            normal = self._normal.get(host)
            if normal is None or elapsed < normal:
                self._normal[host] = elapsed
            else:
                # Let the normal drift up slowly, so a lasting change of the network is accepted
                self._normal[host] = normal * 1.002
            self._ratios.append(elapsed / self._normal[host])
            self._latencies.append(elapsed)
            del self._latencies[:-200]
        if self._overloads >= max(2, self.limit // 4) and self._overloads / self._attempts > _OVERLOADED:
            return self._back_off(
                f"the servers pushed back on {self._overloads} of {self._attempts} attempts"
            )
        if self._clock() - self._window_started < self._window() or self._attempts < min(10, self.limit):
            return None
        return self._end_window()

    def _back_off(self, reason: str, factor: float = 0.75) -> Optional[str]:
        previous = self.limit
        if factor == 0.75:
            self.ceiling, self._holding = self.limit, 0
        self.limit = max(1, int(self.limit * factor))
        self.slow_start = False
        self.epoch += 1
        self._new_window()
        return self._changed(previous, reason)

    def _end_window(self) -> Optional[str]:
        if self._attempts and self._overloads / self._attempts > _OVERLOADED:
            return self._back_off(
                f"the servers pushed back on {self._overloads} of {self._attempts} attempts"
            )
        if len(self._ratios) >= 3 and statistics.median(self._ratios) > _SLOW:
            return self._back_off("responses slowed down", factor=0.9)
        previous = self.limit
        if self._peak >= self.limit:  # the whole limit was used
            if self.ceiling is not None and self.limit >= self.ceiling:
                self.ceiling = None  # a probe at the old ceiling went well
            grown = self.limit * 2 if self.slow_start else self.limit + max(1, self.limit // 10)
            if self.ceiling is not None and grown >= self.ceiling:
                if self.limit < self.ceiling - 1:
                    grown = self.ceiling - 1  # close in, but stay below
                elif self._holding < _HOLD_WINDOWS:
                    self._holding += 1
                    grown = self.limit
                else:
                    self._holding = 0
                    grown = self.ceiling  # probe the ceiling once
            self.limit = min(self.maximum, grown)
        self._new_window()
        if self.limit > previous and self._condition is not None:
            asyncio.ensure_future(self._wake())
        return self._changed(previous, "responses are healthy")

    def _changed(self, previous: int, reason: str) -> Optional[str]:
        if self.limit == previous:
            return None
        self.changes += 1
        self.lowest, self.highest = min(self.lowest, self.limit), max(self.highest, self.limit)
        return f"concurrency {previous} -> {self.limit}: {reason}"

    async def _wake(self) -> None:
        assert self._condition is not None
        async with self._condition:
            self._condition.notify_all()


def _is_overload(error: BaseException) -> bool:
    import aiohttp

    from ._socks import is_proxy_error

    not_load = [aiohttp.InvalidURL, ValueError, aiohttp.ClientSSLError]
    if hasattr(aiohttp, "ClientConnectorDNSError"):
        not_load.append(aiohttp.ClientConnectorDNSError)  # a name that does not resolve
    if isinstance(error, tuple(not_load)):
        return False
    # asyncio.TimeoutError and the built-in TimeoutError are different classes before Python 3.11
    timeouts = (asyncio.TimeoutError, TimeoutError)
    return isinstance(error, (*timeouts, aiohttp.ClientConnectionError)) or is_proxy_error(error)
