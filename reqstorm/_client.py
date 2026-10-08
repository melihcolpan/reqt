"""Concurrent HTTP requests with per-request results."""

from __future__ import annotations

import asyncio
import base64
import json as jsonlib
import ssl as ssllib
import time
from dataclasses import dataclass, field
from typing import (
    Any,
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Union,
    overload,
)

import aiohttp
from multidict import CIMultiDict, CIMultiDictProxy

from ._limits import HostRateLimiter, RateLimit
from ._progress import Progress, ProgressTarget

__all__ = ["Attempt", "HTTPStatusError", "Request", "Result", "Results", "fetch_all", "stream"]

DEFAULT_RETRY_STATUSES = (429, 500, 502, 503, 504)
_MAX_RETRY_AFTER = 60.0


class HTTPStatusError(Exception):
    """Raised by ``Result.raise_for_error`` for a response status of 400 or above."""

    def __init__(self, result: Result) -> None:
        super().__init__(f"{result.status} for {result.request.method or 'GET'} {result.url}")
        self.result = result


@dataclass(frozen=True)
class Request:
    """A single request. Fields left as ``None`` take the defaults given to ``fetch_all``."""

    url: str
    method: Optional[str] = None
    headers: Optional[Mapping[str, str]] = None
    params: Optional[Mapping[str, Any]] = None
    json: Any = None
    data: Any = None


def _describe(error: Optional[BaseException]) -> Optional[str]:
    if error is None:
        return None
    message = str(error)
    return f"{type(error).__name__}: {message}" if message else type(error).__name__


@dataclass(frozen=True)
class Attempt:
    """One try at a request: the status it got or the error it raised."""

    number: int
    status: Optional[int]
    error: Optional[BaseException]
    elapsed: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "attempt": self.number,
            "status": self.status,
            "error": _describe(self.error),
            "elapsed": round(self.elapsed, 3),
        }


@dataclass
class Result:
    """The outcome of one request: a response, or the error that prevented one."""

    request: Request
    index: int
    status: Optional[int] = None
    headers: Mapping[str, str] = field(default_factory=lambda: CIMultiDictProxy(CIMultiDict()))
    body: bytes = b""
    error: Optional[BaseException] = None
    attempts: int = 0
    elapsed: float = 0.0
    final_url: Optional[str] = None
    history: List[Attempt] = field(default_factory=list)

    @property
    def url(self) -> str:
        return self.request.url

    @property
    def method(self) -> str:
        return self.request.method or "GET"

    @property
    def ok(self) -> bool:
        """True when a response arrived and its status is below 400."""
        return self.error is None and self.status is not None and self.status < 400

    def text(self, encoding: Optional[str] = None) -> str:
        return self.body.decode(encoding or _charset(self.headers) or "utf-8", errors="replace")

    def json(self) -> Any:
        return jsonlib.loads(self.body)

    def raise_for_error(self) -> None:
        """Raise the request's error, or ``HTTPStatusError`` for a status of 400 or above."""
        if self.error is not None:
            raise self.error
        if self.status is not None and self.status >= 400:
            raise HTTPStatusError(self)

    def to_dict(self, body: str = "none", include_headers: bool = False) -> Dict[str, Any]:
        """A JSON-serialisable summary, as written by ``fetch_to_file``.

        Args:
            body: ``"none"`` (default), ``"text"`` or ``"base64"``.
            include_headers: Include the response headers.
        """
        record: Dict[str, Any] = {
            "index": self.index,
            "method": self.method,
            "url": self.url,
            "status": self.status,
            "ok": self.ok,
            "error": _describe(self.error),
            "attempts": self.attempts,
            "elapsed": round(self.elapsed, 3),
            "final_url": self.final_url,
            "history": [attempt.to_dict() for attempt in self.history],
        }
        if include_headers:
            record["headers"] = dict(self.headers)
        if body == "text":
            record["body"] = self.text()
        elif body == "base64":
            record["body"] = base64.b64encode(self.body).decode("ascii")
        elif body != "none":
            raise ValueError("body must be 'none', 'text' or 'base64'")
        return record

    def __repr__(self) -> str:
        outcome = f"status={self.status}" if self.error is None else f"error={self.error!r}"
        return f"<Result {self.method} {self.url} {outcome} attempts={self.attempts}>"


class Results(List[Result]):
    """The list returned by ``fetch_all``, in input order, with helpers for reporting."""

    @property
    def succeeded(self) -> List[Result]:
        return [result for result in self if result.ok]

    @property
    def failed(self) -> List[Result]:
        return [result for result in self if not result.ok]

    def errors(self) -> List[Dict[str, Any]]:
        """Every failed request as a dict: index, method, url, status, error, attempts and history."""
        return [result.to_dict() for result in self.failed]

    def summary(self) -> Dict[str, Any]:
        """Counts of the batch: total, ok, failed, and failures grouped by status or error type."""
        reasons: Dict[str, int] = {}
        for result in self.failed:
            reason = type(result.error).__name__ if result.error is not None else f"HTTP {result.status}"
            reasons[reason] = reasons.get(reason, 0) + 1
        ok = sum(1 for result in self if result.ok)
        return {"total": len(self), "ok": ok, "failed": len(self) - ok, "failures": reasons}

    def to_dicts(self, body: str = "none", include_headers: bool = False) -> List[Dict[str, Any]]:
        return [result.to_dict(body=body, include_headers=include_headers) for result in self]


Callback = Callable[[Result], Union[None, Awaitable[None]]]


def _charset(headers: Mapping[str, str]) -> Optional[str]:
    content_type = headers.get("Content-Type", "")
    for part in content_type.split(";")[1:]:
        key, _, value = part.strip().partition("=")
        if key.lower() == "charset" and value:
            return value.strip('"')
    return None


@dataclass(frozen=True)
class _Options:
    method: str
    headers: Optional[Mapping[str, str]]
    params: Optional[Mapping[str, Any]]
    json: Any
    data: Any
    timeout: aiohttp.ClientTimeout
    retries: int
    backoff: float
    retry_statuses: frozenset
    ssl: Union[bool, ssllib.SSLContext]
    rate_limiter: Optional[HostRateLimiter]


def _resolve(request: Union[str, Request], options: _Options) -> Request:
    if isinstance(request, str):
        request = Request(request)
    headers = dict(options.headers or {})
    headers.update(request.headers or {})
    return Request(
        url=request.url,
        method=(request.method or options.method).upper(),
        headers=headers or None,
        params=request.params if request.params is not None else options.params,
        json=request.json if request.json is not None else options.json,
        data=request.data if request.data is not None else options.data,
    )


def _is_retryable_error(error: BaseException) -> bool:
    if isinstance(error, (aiohttp.InvalidURL, ValueError)):
        return False
    return isinstance(error, (aiohttp.ClientError, asyncio.TimeoutError))


def _should_retry(result: Result, retry_statuses: frozenset) -> bool:
    if result.error is not None:
        return _is_retryable_error(result.error)
    return result.status in retry_statuses


def _retry_after(headers: Mapping[str, str]) -> Optional[float]:
    value = headers.get("Retry-After")
    if value is None:
        return None
    try:
        return min(max(float(value), 0.0), _MAX_RETRY_AFTER)
    except ValueError:
        return None


async def _send(session: aiohttp.ClientSession, request: Request, index: int, options: _Options) -> Result:
    result = Result(request=request, index=index)
    started = time.monotonic()
    for attempt in range(1, options.retries + 2):
        result.attempts = attempt
        delay = options.backoff * (2 ** (attempt - 1))
        if options.rate_limiter is not None:
            await options.rate_limiter.wait(request.url)
        attempt_started = time.monotonic()
        try:
            async with session.request(
                request.method or "GET",
                request.url,
                headers=request.headers,
                params=request.params,
                json=request.json,
                data=request.data,
                timeout=options.timeout,
                ssl=options.ssl,
            ) as response:
                body = await response.read()
                result.status = response.status
                result.headers = response.headers
                result.body = body
                result.final_url = str(response.url)
                result.error = None
            result.history.append(Attempt(attempt, response.status, None, time.monotonic() - attempt_started))
            if response.status not in options.retry_statuses or attempt > options.retries:
                break
            delay = _retry_after(response.headers) or delay
        except asyncio.CancelledError:
            raise
        except Exception as error:  # one failing request must not stop the others
            result.error = error
            result.status = None
            result.history.append(Attempt(attempt, None, error, time.monotonic() - attempt_started))
            if not _is_retryable_error(error) or attempt > options.retries:
                break
        await asyncio.sleep(delay)
    result.elapsed = time.monotonic() - started
    return result


async def stream(
    urls: Iterable[Union[str, Request]],
    method: str = "GET",
    *,
    headers: Optional[Mapping[str, str]] = None,
    params: Optional[Mapping[str, Any]] = None,
    json: Any = None,
    data: Any = None,
    concurrency: int = 100,
    timeout: Optional[float] = 30.0,
    retries: int = 0,
    backoff: float = 0.5,
    retry_statuses: Sequence[int] = DEFAULT_RETRY_STATUSES,
    verify_ssl: bool = True,
    ssl: Optional[ssllib.SSLContext] = None,
    rate_limit: Optional[RateLimit] = None,
    concurrency_per_host: int = 0,
    session: Optional[aiohttp.ClientSession] = None,
) -> AsyncGenerator[Result, None]:
    """Send the requests concurrently and yield each ``Result`` as soon as it completes.

    ``urls`` may be any iterable, including a generator; it is consumed lazily, so
    millions of requests can be streamed without building them all in memory.
    See ``fetch_all`` for the parameters. Retry rounds are not available here;
    use ``fetch_all`` or ``fetch_to_file`` for those.
    """
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    if retries < 0:
        raise ValueError("retries must not be negative")
    if concurrency_per_host < 0:
        raise ValueError("concurrency_per_host must not be negative")
    options = _Options(
        method=method,
        headers=headers,
        params=params,
        json=json,
        data=data,
        timeout=aiohttp.ClientTimeout(total=timeout),
        retries=retries,
        backoff=backoff,
        retry_statuses=frozenset(retry_statuses),
        ssl=ssl if ssl is not None else verify_ssl,
        rate_limiter=HostRateLimiter(rate_limit) if rate_limit is not None else None,
    )

    owns_session = session is None
    if session is None:
        session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=concurrency, limit_per_host=concurrency_per_host)
        )

    pending = enumerate(urls)
    queue: asyncio.Queue = asyncio.Queue(maxsize=concurrency * 2)
    finished = object()

    async def worker() -> None:
        for index, request in pending:
            await queue.put(await _send(session, _resolve(request, options), index, options))

    async def supervise() -> None:
        try:
            await asyncio.gather(*(worker() for _ in range(concurrency)))
        except BaseException as error:  # surfaces a failure while reading `urls`
            await queue.put(error)
        else:
            await queue.put(finished)

    supervisor = asyncio.ensure_future(supervise())
    try:
        while True:
            item = await queue.get()
            if item is finished:
                break
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        supervisor.cancel()
        try:
            await supervisor
        except (asyncio.CancelledError, Exception):
            pass
        if owns_session:
            await session.close()


async def _execute(
    urls: Iterable[Union[str, Request]],
    retry_rounds: int,
    retry_round_delay: float,
    stream_options: Dict[str, Any],
) -> AsyncIterator[Result]:
    """``stream`` plus retry rounds: yields each request's final result exactly once.

    Results that failed for a retryable reason are held back and sent again in up to
    ``retry_rounds`` further rounds, after everything else has finished.
    """
    if retry_rounds < 0:
        raise ValueError("retry_rounds must not be negative")
    retry_statuses = frozenset(stream_options.get("retry_statuses", DEFAULT_RETRY_STATUSES))
    deferred: List[Result] = []
    async for result in stream(urls, **stream_options):
        if retry_rounds and _should_retry(result, retry_statuses):
            deferred.append(result)
        else:
            yield result

    for round_number in range(1, retry_rounds + 1):
        if not deferred:
            break
        await asyncio.sleep(retry_round_delay)
        previous = deferred
        deferred = []
        async for retried in stream([earlier.request for earlier in previous], **stream_options):
            earlier = previous[retried.index]
            retried.index = earlier.index
            retried.history = earlier.history + [
                Attempt(earlier.attempts + attempt.number, attempt.status, attempt.error, attempt.elapsed)
                for attempt in retried.history
            ]
            retried.attempts += earlier.attempts
            retried.elapsed += earlier.elapsed
            if round_number < retry_rounds and _should_retry(retried, retry_statuses):
                deferred.append(retried)
            else:
                yield retried


@overload
async def fetch_all(
    urls: Iterable[Union[str, Request]],
    method: str = ...,
    *,
    headers: Optional[Mapping[str, str]] = ...,
    params: Optional[Mapping[str, Any]] = ...,
    json: Any = ...,
    data: Any = ...,
    concurrency: int = ...,
    timeout: Optional[float] = ...,
    retries: int = ...,
    backoff: float = ...,
    retry_statuses: Sequence[int] = ...,
    retry_rounds: int = ...,
    retry_round_delay: float = ...,
    verify_ssl: bool = ...,
    ssl: Optional[ssllib.SSLContext] = ...,
    rate_limit: Optional[RateLimit] = ...,
    concurrency_per_host: int = ...,
    callback: Optional[Callback] = ...,
    progress: ProgressTarget = ...,
    session: Optional[aiohttp.ClientSession] = ...,
) -> Results: ...


@overload
async def fetch_all(
    urls: Iterable[str],
    method: Callable[..., Any],
    *,
    headers: Optional[Mapping[str, str]] = ...,
    verify_ssl: bool = ...,
    request_type: str = ...,
    semaphore_limit: int = ...,
) -> None: ...


async def fetch_all(
    urls: Iterable[Union[str, Request]],
    method: Union[str, Callable[..., Any]] = "GET",
    *,
    headers: Optional[Mapping[str, str]] = None,
    params: Optional[Mapping[str, Any]] = None,
    json: Any = None,
    data: Any = None,
    concurrency: int = 100,
    timeout: Optional[float] = 30.0,
    retries: int = 0,
    backoff: float = 0.5,
    retry_statuses: Sequence[int] = DEFAULT_RETRY_STATUSES,
    retry_rounds: int = 0,
    retry_round_delay: float = 5.0,
    verify_ssl: bool = True,
    ssl: Optional[ssllib.SSLContext] = None,
    rate_limit: Optional[RateLimit] = None,
    concurrency_per_host: int = 0,
    callback: Optional[Callback] = None,
    progress: ProgressTarget = False,
    session: Optional[aiohttp.ClientSession] = None,
    **legacy: Any,
) -> Optional[Results]:
    """Send all requests concurrently and return their results in the order given.

    Args:
        urls: URLs, or ``Request`` objects for per-request method, headers, body or params.
        method: HTTP method for plain URLs (default ``"GET"``).
        headers, params, json, data: Defaults applied to every request; a ``Request``'s
            own headers are merged on top, its other fields replace the default.
        concurrency: Maximum number of requests in flight at once.
        timeout: Seconds allowed per attempt, including reading the body. ``None`` disables it.
        retries: How many times to retry a request right away after a connection error,
            a timeout or a status in ``retry_statuses``. Invalid URLs are not retried.
        backoff: Delay before the first retry in seconds, doubled for each further retry.
            A ``Retry-After`` header (in seconds, up to 60) takes precedence.
        retry_rounds: After all requests have finished, send the ones that still failed
            for a retryable reason again, up to this many more rounds.
        retry_round_delay: Seconds to wait before each retry round.
        verify_ssl: Verify TLS certificates (default). Only disable this for hosts you control.
        ssl: A custom ``ssl.SSLContext``, for example to trust a private certificate authority.
        rate_limit: Maximum request rate per host (``host:port``), retries included:
            requests per second as a number, or a string such as ``"100/min"``,
            ``"30/5min"`` or ``"1000/h"``, or ``(count, seconds)``.
        concurrency_per_host: Maximum requests in flight to each host; 0 means no per-host limit.
        callback: Called with each final ``Result`` as soon as it is known. May be a coroutine function.
        progress: ``True`` to print progress to stderr, or a text stream to print it to.
        session: An existing ``aiohttp.ClientSession`` to use instead of creating one.

    Returns a ``Results`` list; ``results.errors()`` and ``results.summary()`` report the failures.
    A failing request never stops the others: its ``Result.error`` holds the exception and
    ``Result.history`` lists every attempt.
    """
    if callable(method) or legacy:
        from ._legacy import legacy_fetch_all

        await legacy_fetch_all(urls, method, headers=headers, verify_ssl=verify_ssl, **legacy)  # type: ignore[arg-type]
        return None

    results = Results()
    tracker = Progress.create(progress, total=_length(urls))
    stream_options: Dict[str, Any] = dict(
        method=method,
        headers=headers,
        params=params,
        json=json,
        data=data,
        concurrency=concurrency,
        timeout=timeout,
        retries=retries,
        backoff=backoff,
        retry_statuses=retry_statuses,
        verify_ssl=verify_ssl,
        ssl=ssl,
        rate_limit=rate_limit,
        concurrency_per_host=concurrency_per_host,
        session=session,
    )
    async for result in _execute(urls, retry_rounds, retry_round_delay, stream_options):
        results.append(result)
        if tracker is not None:
            tracker.update(result)
        if callback is not None:
            outcome = callback(result)
            if asyncio.iscoroutine(outcome):
                await outcome
    if tracker is not None:
        tracker.close()
    results.sort(key=lambda r: r.index)
    return results


def _length(urls: Iterable[Any]) -> Optional[int]:
    try:
        return len(urls)  # type: ignore[arg-type]
    except TypeError:
        return None
