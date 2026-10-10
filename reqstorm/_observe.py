"""What a run is doing while it runs: counters, log messages and the progress line."""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Deque, Dict, Optional, TextIO, Union, cast
from urllib.parse import urlsplit, urlunsplit

from ._progress import format_duration

if TYPE_CHECKING:
    from ._client import Request, Result

__all__ = ["ProgressInfo"]

LOGGER_NAME = "reqstorm"
LogLevel = Union[None, int, str]
LogTarget = Union[None, str, "os.PathLike[str]", TextIO]
ProgressTarget = Union[bool, TextIO, Callable[["ProgressInfo"], Any], None]

_RATE_WINDOW = 60.0  # seconds of recent completions used for the rate and the ETA
TERMINAL_INTERVAL = 0.5  # seconds between progress lines on a terminal
LOG_INTERVAL = 10.0  # ... in a log file or CI output
CALLBACK_INTERVAL = 2.0  # ... for a progress function


@dataclass(frozen=True)
class ProgressInfo:
    """A snapshot of a running batch, passed to ``progress=`` when it is a function.

    The function is called every few seconds while the batch runs, even when no request
    finishes, and once more at the end with ``finished=True``.
    """

    total: Optional[int]
    """Number of requests, or None when the input is a generator of unknown length."""
    done: int
    """Requests with a final result (after all their retries)."""
    ok: int
    failed: int
    in_flight: int
    """Requests being sent, waiting for a response, or waiting to retry right now."""
    retries: int
    """Retries so far, of all requests."""
    rate: float
    """Finished requests per second over the last minute."""
    elapsed: float
    eta: Optional[float]
    """Estimated seconds left, or None when unknown."""
    round: int = 0
    """Retry round being sent (0 for the first pass)."""
    rounds: int = 0
    paused: Dict[str, float] = field(default_factory=dict)
    """Hosts the server asked to wait (``rate_limit="auto"``), with seconds left."""
    concurrency: Optional[int] = None
    """With ``concurrency="auto"``: the number of requests currently allowed in flight."""
    finished: bool = False

    def __str__(self) -> str:
        if self.total:
            count = f"{self.done}/{self.total} ({self.done * 100 // self.total}%)"
        else:
            count = str(self.done)
        parts = [f"reqstorm: {count}", f"ok {self.ok}", f"failed {self.failed}"]
        if not self.finished:
            active = f"active {self.in_flight}"
            if self.concurrency is not None:
                active += f"/{self.concurrency}"  # with concurrency="auto": in flight / allowed
            parts.append(active)
        if self.retries:
            parts.append(f"retries {self.retries}")
        parts.append(f"{self.rate:.1f} req/s")
        if self.round:
            parts.append(f"round {self.round}/{self.rounds}")
        if self.paused:
            host, seconds = max(self.paused.items(), key=lambda item: item[1])
            more = f" +{len(self.paused) - 1}" if len(self.paused) > 1 else ""
            parts.append(f"paused {host} {format_duration(seconds)}{more}")
        if self.finished:
            parts.append(f"in {format_duration(self.elapsed)}")
        elif self.eta is not None:
            parts.append(f"ETA {format_duration(self.eta)}")
        return "  ".join(parts)


def redact(url: Optional[str]) -> str:
    """A URL without its password, for log messages."""
    if not url:
        return ""
    parts = urlsplit(url)
    if parts.password is None:
        return url
    netloc = f"{parts.username}:***@{parts.hostname}" + (f":{parts.port}" if parts.port else "")
    return urlunsplit(parts._replace(netloc=netloc))


class _JsonFormatter(logging.Formatter):
    """One JSON object per line: time, level, message, and the event's fields."""

    _STANDARD = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}

    def format(self, record: logging.LogRecord) -> str:
        document: Dict[str, Any] = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}",
            "level": record.levelname,
            "message": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in self._STANDARD and not key.startswith("_"):
                document[key] = value
        return json.dumps(document, ensure_ascii=False, default=str)


class _LineAwareHandler(logging.StreamHandler):
    """Clears the progress line before a log line, so the two do not mix on a terminal."""

    def __init__(self, stream: TextIO, run: Run) -> None:
        super().__init__(stream)
        self._run = run

    def emit(self, record: logging.LogRecord) -> None:
        display = self._run.display
        if display is not None and display.interactive and display.stream is self.stream:
            self.stream.write("\r\x1b[K")
        super().emit(record)
        if display is not None and display.interactive and display.stream is self.stream:
            display.redraw()


def log_path(target: str | os.PathLike[str], now: Optional[float] = None) -> str:
    """Where a run's log goes, without ever reusing an existing file.

    - A directory (existing, or a path ending in a slash) gets
      ``reqstorm-2026-10-09_14-32-05.123.log`` inside it.
    - ``{time}`` in the name is replaced by the same time stamp.
    - When the resulting file already exists, ``-2``, ``-3``, ... is added before the
      extension: ``run.log`` becomes ``run-2.log``.
    """
    stamp_time = time.time() if now is None else now
    stamp = (
        time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime(stamp_time))
        + f".{int(stamp_time * 1000) % 1000:03d}"
    )
    text = os.fspath(target)
    if text.endswith(("/", os.sep)) or os.path.isdir(text):
        os.makedirs(text, exist_ok=True)
        text = os.path.join(text, "reqstorm-{time}.log")
    text = text.replace("{time}", stamp)
    directory = os.path.dirname(text)
    if directory:
        os.makedirs(directory, exist_ok=True)
    return text


def _open_new(path: str) -> tuple[str, TextIO]:
    """Open ``path``, or ``path-2``, ``path-3``, ... for writing; never an existing file."""
    stem, extension = os.path.splitext(path)
    candidate, number = path, 1
    while True:
        try:
            # "x" fails if the file exists, so two runs starting at once cannot share a file
            return candidate, open(candidate, "x", encoding="utf-8")  # noqa: SIM115
        except FileExistsError:
            number += 1
            candidate = f"{stem}-{number}{extension}"


def _level(level: LogLevel) -> int:
    if isinstance(level, int):
        return level
    number = logging.getLevelName(str(level).upper())
    if not isinstance(number, int):
        raise ValueError(f"unknown log_level {level!r}; use DEBUG, INFO, WARNING, ERROR or CRITICAL")
    return number


class _Display:
    """The progress line on a stream: rewritten in place on a terminal, a line every few
    seconds otherwise."""

    def __init__(self, stream: TextIO, run: Run) -> None:
        self.stream = stream
        self.run = run
        self.interactive = bool(getattr(stream, "isatty", lambda: False)())
        self.interval = TERMINAL_INTERVAL if self.interactive else LOG_INTERVAL

    def redraw(self) -> None:
        self.show(self.run.snapshot())

    def show(self, info: ProgressInfo) -> None:
        if self.interactive:
            self.stream.write("\r\x1b[K" + str(info) + ("\n" if info.finished else ""))
        else:
            self.stream.write(str(info) + "\n")
        self.stream.flush()


class Run:
    """Counters, logging and progress for one call of fetch_all / stream / fetch_to_*."""

    def __init__(
        self,
        *,
        total: Optional[int],
        progress: ProgressTarget = False,
        log_level: LogLevel = None,
        log_file: LogTarget = None,
        log_format: str = "text",
    ) -> None:
        if log_format not in ("text", "json"):
            raise ValueError("log_format must be 'text' or 'json'")
        self.total = total
        self.started = time.monotonic()
        self.done = self.ok = self.failed = self.in_flight = self.retries = self.attempts = 0
        self.round = self.rounds = 0
        self.limiters: list = []
        self.controller: Any = None  # AutoConcurrency with concurrency="auto"
        self._recent: Deque[float] = collections.deque()
        self._task: Optional[asyncio.Task] = None
        self._closers: list = []

        self.display: Optional[_Display] = None
        self.callback: Optional[Callable[[ProgressInfo], Any]] = None
        if callable(progress) and not hasattr(progress, "write"):
            self.callback = progress
        elif progress is True:
            self.display = _Display(sys.stderr, self)
        elif progress:
            self.display = _Display(progress, self)  # type: ignore[arg-type]

        self.log_file: Optional[str] = None
        """The file this run logs to, when log_file= named a file or directory."""
        self.logger = self._make_logger(log_level, log_file, log_format)

    # -- logging ---------------------------------------------------------------------

    def _make_logger(self, level: LogLevel, target: LogTarget, log_format: str) -> logging.Logger:
        if level is None:
            if target is not None:
                raise ValueError("log_file needs log_level")
            return logging.getLogger(LOGGER_NAME)  # the application's logging configuration applies
        # A logger of our own, not registered with logging: the application's configuration
        # does not change it, and its messages do not reach the application's handlers.
        logger = logging.Logger(LOGGER_NAME, _level(level))
        handler: logging.Handler
        if target is None or hasattr(target, "write"):
            handler = _LineAwareHandler(cast(TextIO, target) if target is not None else sys.stderr, self)
        else:
            self.log_file, stream = _open_new(log_path(cast("str | os.PathLike[str]", target)))
            handler = logging.StreamHandler(stream)
            self._closers.extend([handler.close, stream.close])
        handler.setFormatter(
            _JsonFormatter()
            if log_format == "json"
            else logging.Formatter("%(asctime)s %(levelname)-7s reqstorm: %(message)s", "%H:%M:%S")
        )
        logger.addHandler(handler)
        return logger

    def log(self, level: int, message: str, *args: Any, **fields: Any) -> None:
        if self.logger.isEnabledFor(level):
            self.logger.log(level, message, *args, extra={"event": fields.pop("event", None), **fields})

    def debug_enabled(self) -> bool:
        return self.logger.isEnabledFor(logging.DEBUG)

    # -- events from the request loop ------------------------------------------------

    def started_request(self) -> None:
        self.in_flight += 1

    def finished_request(self) -> None:
        self.in_flight -= 1

    def attempted(self, request: Request, status: Optional[int], error: Optional[BaseException],
                  elapsed: float, attempt: int, proxy: Optional[str]) -> None:  # fmt: skip
        self.attempts += 1
        if self.debug_enabled():
            outcome = f"HTTP {status}" if error is None else f"{type(error).__name__}: {error}".rstrip(": ")
            via = f" via {redact(proxy)}" if proxy else ""
            self.log(logging.DEBUG, "%s %s -> %s in %.3fs (attempt %d%s)", request.method or "GET",
                     request.url, outcome, elapsed, attempt, via, event="attempt", url=request.url,
                     status=status, error=type(error).__name__ if error else None, attempt=attempt,
                     elapsed=round(elapsed, 4))  # fmt: skip

    def retrying(self, request: Request, reason: str, attempt: int, attempts: int, delay: float,
                 proxy: Optional[str]) -> None:  # fmt: skip
        self.retries += 1
        via = f" via {redact(proxy)}" if proxy else ""
        self.log(logging.WARNING, "%s %s failed (%s) on attempt %d of %d%s; retrying in %.1fs",
                 request.method or "GET", request.url, reason, attempt, attempts, via, delay,
                 event="retry", url=request.url, reason=reason, attempt=attempt,
                 delay=round(delay, 3))  # fmt: skip

    def throttled(self, host: str, pause: float, why: str) -> None:
        self.log(logging.WARNING, "%s: %s, pausing for %s", host, why, format_duration(pause),
                 event="pause", host=host, pause=round(pause, 3), reason=why)  # fmt: skip

    def token_refreshed(self, refreshes: int) -> None:
        self.log(logging.INFO, "token refreshed after a 401 (refresh %d)", refreshes,
                 event="token_refresh", refreshes=refreshes)  # fmt: skip

    def cached(self, request: Request, revalidated: bool) -> None:
        if self.debug_enabled():
            how = "revalidated (304)" if revalidated else "fresh"
            self.log(logging.DEBUG, "%s %s served from cache, %s", request.method or "GET", request.url, how,
                     event="cache", url=request.url, revalidated=revalidated)  # fmt: skip

    def completed(self, result: Result) -> None:
        """A request's final result, after all its retries and rounds."""
        self.done += 1
        if result.ok:
            self.ok += 1
        else:
            self.failed += 1
            reason = (f"{type(result.error).__name__}: {result.error}".rstrip(": ")
                      if result.error is not None else f"HTTP {result.status}")  # fmt: skip
            self.log(logging.ERROR, "%s %s failed after %d attempt%s: %s", result.method, result.url,
                     result.attempts, "" if result.attempts == 1 else "s", reason, event="failed",
                     url=result.url, status=result.status, attempts=result.attempts,
                     reason=reason)  # fmt: skip
        now = time.monotonic()
        self._recent.append(now)
        while self._recent and self._recent[0] < now - _RATE_WINDOW:
            self._recent.popleft()

    def rejected(self, url: str, reasons: list) -> None:
        self.log(logging.WARNING, "record from %s rejected: %s", url, "; ".join(reasons), event="rejected",
                 url=url, reasons=reasons)  # fmt: skip

    def retry_round(self, number: int, rounds: int, count: int, delay: float) -> None:
        self.round, self.rounds = number, rounds
        self.log(logging.INFO, "retry round %d of %d: sending %d failed request%s again in %s", number,
                 rounds, count, "" if count == 1 else "s", format_duration(delay), event="retry_round",
                 round=number, rounds=rounds, requests=count)  # fmt: skip

    # -- progress --------------------------------------------------------------------

    def _rate(self, elapsed: float) -> float:
        if not self._recent:
            return 0.0
        window = min(_RATE_WINDOW, elapsed)
        if len(self._recent) < 5 or window <= 0:
            return self.done / max(elapsed, 1e-9)  # too few recent results: use the average
        return len(self._recent) / window

    def snapshot(self, finished: bool = False) -> ProgressInfo:
        now = time.monotonic()
        elapsed = now - self.started
        rate = self._rate(elapsed) if not finished else self.done / max(elapsed, 1e-9)
        paused: Dict[str, float] = {}
        for limiter in self.limiters:
            paused.update(getattr(limiter, "paused", lambda: {})())
        eta = None
        if self.total and not finished:
            remaining = max(self.total - self.done, 0)
            eta = remaining / rate if rate > 0 else None
            if eta is not None and paused:
                eta += max(paused.values())  # nothing to that host finishes while it waits
        return ProgressInfo(
            total=self.total, done=self.done, ok=self.ok, failed=self.failed, in_flight=self.in_flight,
            retries=self.retries, rate=rate, elapsed=elapsed, eta=eta, round=self.round,
            rounds=self.rounds, paused=paused, finished=finished,
            concurrency=self.controller.limit if self.controller is not None else None,
        )  # fmt: skip

    def _report(self, info: ProgressInfo) -> None:
        if self.display is not None:
            self.display.show(info)
        if self.callback is not None:
            self.callback(info)

    async def _heartbeat(self) -> None:
        interval = self.display.interval if self.display is not None else CALLBACK_INTERVAL
        while True:
            await asyncio.sleep(interval)
            self._report(self.snapshot())

    def start(self, description: str = "") -> None:
        if self.display is not None or self.callback is not None:
            self._task = asyncio.ensure_future(self._heartbeat())
        total = f"{self.total} requests" if self.total is not None else "requests"
        self.log(logging.INFO, "starting %s%s", total, f" ({description})" if description else "",
                 event="start", total=self.total, log_file=self.log_file)  # fmt: skip

    async def finish(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        info = self.snapshot(finished=True)
        if self.display is not None or self.callback is not None:
            self._report(info)
        self.log(logging.INFO, "finished %d requests in %s: %d ok, %d failed, %d retries", self.done,
                 format_duration(info.elapsed), self.ok, self.failed, self.retries, event="finish",
                 total=self.done, ok=self.ok, failed=self.failed, retries=self.retries,
                 elapsed=round(info.elapsed, 3))  # fmt: skip
        if self.controller is not None:
            c = self.controller
            self.log(logging.INFO, "concurrency auto: ended at %d, ranged from %d to %d over %d changes",
                     c.limit, c.lowest, c.highest, c.changes, event="concurrency_summary",
                     concurrency=c.limit, lowest=c.lowest, highest=c.highest)  # fmt: skip
        for close in self._closers:
            close()
        self._closers.clear()


def describe(**settings: Any) -> str:
    """``concurrency 100, rate_limit 100/min, retries 2`` for the start message."""
    return ", ".join(f"{name} {value}" for name, value in settings.items() if value not in (None, 0, False))
