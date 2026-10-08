"""A small progress line for long batches, with no extra dependency."""

from __future__ import annotations

import sys
import time
from typing import TYPE_CHECKING, Optional, TextIO, Union

if TYPE_CHECKING:
    from ._client import Result

ProgressTarget = Union[bool, TextIO]


def format_duration(seconds: float) -> str:
    """``42s``, ``3m 05s``, ``1h 10m``."""
    seconds = max(int(round(seconds)), 0)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


class Progress:
    """Prints ``done/total``, successes, failures and the request rate.

    On a terminal the line is rewritten in place several times a second; when the
    output is redirected (a log file, CI) a new line is written every few seconds.
    """

    def __init__(self, stream: TextIO, total: Optional[int]) -> None:
        self._stream = stream
        self._total = total
        self._interactive = bool(getattr(stream, "isatty", lambda: False)())
        self._interval = 0.2 if self._interactive else 5.0
        self._started = time.monotonic()
        self._last_print = 0.0
        self.done = 0
        self.ok = 0
        self.failed = 0

    @classmethod
    def create(cls, target: ProgressTarget, total: Optional[int]) -> Optional[Progress]:
        if target is False or target is None:
            return None
        return cls(sys.stderr if target is True else target, total)

    def update(self, result: Result) -> None:
        self.done += 1
        if result.ok:
            self.ok += 1
        else:
            self.failed += 1
        now = time.monotonic()
        if now - self._last_print >= self._interval:
            self._last_print = now
            self._write(final=False)

    def close(self) -> None:
        self._write(final=True)

    def _line(self) -> str:
        elapsed = max(time.monotonic() - self._started, 1e-9)
        rate = self.done / elapsed
        if self._total:
            count = f"{self.done}/{self._total} ({self.done * 100 // self._total}%)"
        else:
            count = str(self.done)
        line = f"reqstorm: {count}  ok {self.ok}  failed {self.failed}  {rate:.1f} req/s"
        if self._total and rate > 0 and self.done < self._total:
            line += f"  ETA {format_duration((self._total - self.done) / rate)}"
        return line

    def _write(self, final: bool) -> None:
        if self._interactive:
            self._stream.write("\r" + self._line() + ("\n" if final else ""))
        else:
            self._stream.write(self._line() + "\n")
        self._stream.flush()
