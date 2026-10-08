"""A per-host rate limiter that learns the rate from the server's responses."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Dict, Mapping, Optional, Tuple

from ._limits import host_key

__all__ = ["AdaptiveRateLimiter", "seconds_until_reset"]

# Header names, most specific first. "RateLimit-*" is the IETF draft; "X-RateLimit-*" is the
# common convention (GitHub, Twitter/X, many others).
_REMAINING = ("RateLimit-Remaining", "X-RateLimit-Remaining", "X-Rate-Limit-Remaining")
_RESET = ("RateLimit-Reset", "X-RateLimit-Reset", "X-Rate-Limit-Reset")
_MAX_PAUSE = 300.0


def _header(headers: Mapping[str, str], names: tuple) -> Optional[str]:
    for name in names:
        value = headers.get(name)
        if value is not None:
            return value
    return None


def seconds_until_reset(headers: Mapping[str, str], now: Optional[float] = None) -> Optional[float]:
    """Seconds until the server's rate-limit window resets, from ``*-Reset`` or ``Retry-After``.

    Reset values above 10^9 are Unix timestamps (GitHub style); smaller ones are seconds
    (IETF draft style). ``Retry-After`` may also be an HTTP date.
    """
    now = time.time() if now is None else now
    for value in (_header(headers, _RESET), headers.get("Retry-After")):
        if value is None:
            continue
        value = value.strip()
        try:
            number = float(value)
        except ValueError:
            try:
                return max(parsedate_to_datetime(value).timestamp() - now, 0.0)
            except (TypeError, ValueError, IndexError, OverflowError):
                continue
        seconds = number - now if number > 1e9 else number
        return min(max(seconds, 0.0), _MAX_PAUSE)
    return None


@dataclass
class _HostState:
    interval: float = 0.0  # seconds between requests; 0 means no spacing
    next_slot: float = 0.0
    paused_until: float = 0.0


class AdaptiveRateLimiter:
    """``rate_limit="auto"``: no limit until the server signals one, then follow it.

    - A 429 response doubles the spacing between requests to that host and pauses it for
      ``Retry-After`` (or until the rate-limit window resets).
    - ``*-RateLimit-Remaining`` / ``*-RateLimit-Reset`` headers spread the remaining requests
      evenly over the rest of the window, and pause the host when none are left.
    - Every successful response without such headers shortens the spacing by 10 %, so the
      rate recovers once the server stops pushing back.
    """

    adaptive = True

    def __init__(self) -> None:
        self._hosts: Dict[str, _HostState] = {}

    def _state(self, url: str) -> Optional[_HostState]:
        host = host_key(url)
        if host is None:
            return None
        return self._hosts.setdefault(host, _HostState())

    def interval(self, url: str) -> float:
        state = self._state(url)
        return state.interval if state else 0.0

    async def wait(self, url: str) -> None:
        state = self._state(url)
        if state is None:
            return
        now = time.monotonic()
        slot = max(now, state.next_slot, state.paused_until)
        state.next_slot = slot + state.interval
        if slot > now:
            await asyncio.sleep(slot - now)

    def paused(self) -> Dict[str, float]:
        """Hosts that are paused right now, with the seconds left."""
        now = time.monotonic()
        return {
            host: state.paused_until - now for host, state in self._hosts.items() if state.paused_until > now
        }

    def record(
        self, url: str, status: Optional[int], headers: Mapping[str, str]
    ) -> Optional[Tuple[float, str]]:
        """Learn from a response. Returns ``(seconds, reason)`` when the host is paused."""
        state = self._state(url)
        if state is None or status is None:
            return None
        now = time.monotonic()
        reset = seconds_until_reset(headers)
        if status == 429:
            state.interval = min(max(state.interval * 2, 0.1), 60.0)
            pause = reset if reset is not None else max(state.interval, 1.0)
            state.paused_until = max(state.paused_until, now + pause)
            source = "Retry-After" if reset is not None else "no Retry-After"
            return pause, f"429 Too Many Requests ({source}), spacing requests {state.interval:.2f}s apart"
        remaining_text = _header(headers, _REMAINING)
        if remaining_text is not None and reset is not None:
            try:
                remaining = float(remaining_text)
            except ValueError:
                remaining = None
            if remaining is not None:
                if remaining <= 0:
                    state.paused_until = max(state.paused_until, now + reset)
                    return (reset, "rate limit used up until the window resets") if reset > 0 else None
                state.interval = min(reset / remaining, 60.0)
                return None
        if 200 <= status < 400 and state.interval:
            state.interval = state.interval * 0.9 if state.interval > 0.01 else 0.0
        return None
