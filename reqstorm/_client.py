"""Concurrent HTTP requests with per-request results."""

from __future__ import annotations

import asyncio
import base64
import collections
import itertools
import json as jsonlib
import random
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
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Union,
    overload,
)

import aiohttp
from multidict import CIMultiDict, CIMultiDictProxy

from ._adaptive import AdaptiveRateLimiter
from ._limits import HostRateLimiter, RateLimit, host_key
from ._observe import LogLevel, LogTarget, ProgressTarget, Run, describe
from ._socks import SocksSessions, check_available, is_socks

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
    proxy: Optional[str] = None


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
    from_cache: bool = False
    """True when the response came from the cache (fresh, or revalidated with a 304)."""
    page: int = 0
    """With ``paginate``: 0 for the starting URL, 1 for the next page, and so on."""
    seed_index: Optional[int] = None
    """Position in the input of the starting URL this result belongs to."""

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
            "from_cache": self.from_cache,
            "page": self.page,
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
        """Counts of the batch: total, ok, failed, failures grouped by status or error type,
        response times (``latency``: min, p50, p90, p95, p99, max, mean in seconds),
        ``statuses`` and per-host figures (``hosts``). See ``report()`` for the full set."""
        reasons: Dict[str, int] = {}
        for result in self.failed:
            reason = type(result.error).__name__ if result.error is not None else f"HTTP {result.status}"
            reasons[reason] = reasons.get(reason, 0) + 1
        report = self.report()
        return {
            "total": report["total"],
            "ok": report["ok"],
            "failed": report["failed"],
            "failures": reasons,
            "latency": report["latency"],
            "statuses": report["statuses"],
            "hosts": report["hosts"],
        }

    def to_dicts(self, body: str = "none", include_headers: bool = False) -> List[Dict[str, Any]]:
        return [result.to_dict(body=body, include_headers=include_headers) for result in self]

    def report(self) -> Dict[str, Any]:
        """Response times (p50, p90, p95, p99), statuses, errors and per-host figures."""
        from ._report import Report

        report = Report()
        for result in self:
            report.add(result)
        return report.as_dict()


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
    rate_limiter: Any  # HostRateLimiter, AdaptiveRateLimiter or None
    auth: Any = None
    cache: Any = None
    proxies: Optional[Iterator[str]] = None
    socks: Optional[SocksSessions] = None
    max_backoff: float = 30.0
    jitter: bool = True
    run: Optional[Run] = None

    def backoff_delay(self, retry: int) -> float:
        """Seconds to wait before retry number ``retry`` (1 for the first)."""
        delay = min(self.backoff * (2 ** (retry - 1)), self.max_backoff)
        if self.jitter:
            # "Equal jitter": somewhere between half the delay and the full delay, so
            # requests that failed together do not all retry at the same moment
            delay = delay / 2 + random.uniform(0, delay / 2)
        return delay

    def next_proxy(self) -> Optional[str]:
        return next(self.proxies) if self.proxies is not None else None


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
        proxy=request.proxy,
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


MAX_THROTTLED_RETRIES = 10  # with rate_limit="auto": 429 retries, not counted in `retries`


async def _send(session: aiohttp.ClientSession, request: Request, index: int, options: _Options) -> Result:
    result = Result(request=request, index=index, seed_index=index)
    started = time.monotonic()
    cache = options.cache
    cache_key = None
    if cache is not None:
        cache_key = cache.key(request.method or "GET", request.url, request.params, request.headers)
    cached = cache.get(cache_key) if cache_key else None
    run = options.run
    if cached is not None and cache.is_fresh(cached):
        cache.hits += 1
        if run is not None:
            run.cached(request, revalidated=False)
        result.status, result.headers, result.body = cached.status, cached.headers, cached.body
        result.final_url, result.from_cache = cached.final_url, True
        result.elapsed = time.monotonic() - started
        return result

    limiter = options.rate_limiter
    adaptive = getattr(limiter, "adaptive", False)
    attempt = retries_used = throttled = 0
    refreshed = False
    while True:
        attempt += 1
        result.attempts = attempt
        delay = 0.0
        attempt_started = time.monotonic()
        used_proxy: Optional[str] = None
        try:
            headers: Dict[str, str] = dict(request.headers or {})
            generation = None
            if options.auth is not None:
                generation = options.auth.generation
                headers.update(await options.auth.headers())
            if cached is not None:
                for name, value in cached.validators.items():
                    headers.setdefault(name, value)
            if limiter is not None:
                await limiter.wait(request.url)
            attempt_started = time.monotonic()
            proxy = used_proxy = request.proxy or options.next_proxy()
            sender = session
            if proxy is not None and is_socks(proxy):
                if options.socks is None:
                    raise ValueError("a SOCKS proxy needs a session created by reqstorm, not session=")
                sender, proxy = options.socks.session(proxy), None
            async with sender.request(
                request.method or "GET",
                request.url,
                headers=headers or None,
                params=request.params,
                json=request.json,
                data=request.data,
                timeout=options.timeout,
                ssl=options.ssl,
                proxy=proxy,
            ) as response:
                body = await response.read()
                status, response_headers, final_url = response.status, response.headers, str(response.url)
            took = time.monotonic() - attempt_started
            result.history.append(Attempt(attempt, status, None, took))
            if run is not None:
                run.attempted(request, status, None, took, attempt, used_proxy)
            if adaptive:
                paused = limiter.record(request.url, status, response_headers)
                if paused is not None and run is not None:
                    run.throttled(host_key(request.url) or request.url, *paused)
            if status == 304 and cached is not None:
                cache.touch(cache_key)
                cache.revalidated += 1
                if run is not None:
                    run.cached(request, revalidated=True)
                result.status, result.headers, result.body = cached.status, cached.headers, cached.body
                result.final_url, result.from_cache, result.error = cached.final_url, True, None
                break
            result.status, result.headers, result.body = status, response_headers, body
            result.final_url, result.error = final_url, None
            if status == 401 and options.auth is not None and options.auth.can_refresh and not refreshed:
                refreshed = True
                before = options.auth.refreshes
                await options.auth.refresh(generation)
                if run is not None and options.auth.refreshes != before:
                    run.token_refreshed(options.auth.refreshes)
                continue
            if status == 429 and adaptive and throttled < MAX_THROTTLED_RETRIES:
                throttled += 1  # the limiter now pauses this host; these retries are not counted
                continue
            if status not in options.retry_statuses or retries_used >= options.retries:
                break
            retries_used += 1
            delay = _retry_after(response_headers) or options.backoff_delay(retries_used)
            if run is not None:
                run.retrying(request, f"HTTP {status}", attempt, options.retries + 1, delay, used_proxy)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # one failing request must not stop the others
            result.error = error
            result.status = None
            took = time.monotonic() - attempt_started
            result.history.append(Attempt(attempt, None, error, took))
            if run is not None:
                run.attempted(request, None, error, took, attempt, used_proxy)
            if not _is_retryable_error(error) or retries_used >= options.retries:
                break
            retries_used += 1
            delay = options.backoff_delay(retries_used)
            if run is not None:
                reason = f"{type(error).__name__}: {error}".rstrip(": ")
                run.retrying(request, reason, attempt, options.retries + 1, delay, used_proxy)
        if delay:
            await asyncio.sleep(delay)
    if cache_key and result.ok and result.status == 200 and not result.from_cache:
        cache.put(cache_key, result.status, result.headers, result.body, result.final_url)
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
    max_backoff: float = 30.0,
    jitter: bool = True,
    retry_statuses: Sequence[int] = DEFAULT_RETRY_STATUSES,
    verify_ssl: bool = True,
    ssl: Optional[ssllib.SSLContext] = None,
    rate_limit: Union[RateLimit, str, None] = None,
    concurrency_per_host: int = 0,
    auth: Any = None,
    cache: Any = None,
    proxy: Union[str, Sequence[str], None] = None,
    paginate: Any = None,
    progress: ProgressTarget = False,
    total: Optional[int] = None,
    log_level: LogLevel = None,
    log_file: LogTarget = None,
    log_format: str = "text",
    session: Optional[aiohttp.ClientSession] = None,
    _run: Optional[Run] = None,
) -> AsyncGenerator[Result, None]:
    """Send the requests concurrently and yield each ``Result`` as soon as it completes.

    ``urls`` may be any iterable, including a generator; it is consumed lazily, so
    millions of requests can be streamed without building them all in memory.
    See ``fetch_all`` for the parameters. Retry rounds are not available here;
    use ``fetch_all`` or ``fetch_to_file`` for those.
    """
    owns_run = _run is None
    run = _run or Run(total=_total(urls, total, paginate), progress=progress, log_level=log_level,
                      log_file=log_file, log_format=log_format)  # fmt: skip
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    if retries < 0:
        raise ValueError("retries must not be negative")
    if concurrency_per_host < 0:
        raise ValueError("concurrency_per_host must not be negative")
    if backoff < 0 or max_backoff < 0:
        raise ValueError("backoff and max_backoff must not be negative")
    if rate_limit == "auto":
        limiter: Any = AdaptiveRateLimiter()
    elif rate_limit is not None:
        limiter = HostRateLimiter(rate_limit)
    else:
        limiter = None
    proxies = [proxy] if isinstance(proxy, str) else list(proxy or [])
    if any(is_socks(item) for item in proxies):
        check_available()  # fail before sending anything, not on every request
        if session is not None:
            raise ValueError("SOCKS proxies cannot be combined with session=; reqstorm opens their sessions")
    options = _Options(
        method=method,
        headers=headers,
        params=params,
        json=json,
        data=data,
        timeout=aiohttp.ClientTimeout(total=timeout),
        retries=retries,
        backoff=backoff,
        max_backoff=max_backoff,
        jitter=jitter,
        retry_statuses=frozenset(retry_statuses),
        ssl=ssl if ssl is not None else verify_ssl,
        rate_limiter=limiter,
        auth=auth,
        cache=cache,
        proxies=itertools.cycle(proxies) if proxies else None,
        socks=SocksSessions(concurrency, concurrency_per_host) if session is None else None,
        run=run,
    )
    if limiter is not None:
        run.limiters.append(limiter)
    if owns_run:
        run.start(describe(concurrency=concurrency, rate_limit=rate_limit, retries=retries))

    owns_session = session is None
    if session is None:
        session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=concurrency, limit_per_host=concurrency_per_host)
        )

    pending = enumerate(urls)
    queue: asyncio.Queue = asyncio.Queue(maxsize=concurrency * 2)
    finished = object()
    # Pages found while paginating are sent before new input, so a long input does not
    # leave pages waiting. Workers stop when the input is used up and nothing is in flight.
    follow_ups: collections.deque[Tuple[Request, int, int]] = collections.deque()
    state = {"in_flight": 0, "input_done": False, "next_index": 0}
    changed = asyncio.Condition()
    max_pages = getattr(paginate, "max_pages", 0)

    async def take() -> Optional[Tuple[Request, int, int, int]]:
        async with changed:
            while True:
                if follow_ups:
                    request, seed, page = follow_ups.popleft()
                    break
                if not state["input_done"]:
                    try:
                        seed, raw = next(pending)
                    except StopIteration:
                        state["input_done"] = True
                        changed.notify_all()
                        continue
                    request, page = _resolve(raw, options), 0
                    break
                if state["in_flight"] == 0:
                    changed.notify_all()
                    return None
                await changed.wait()
            state["in_flight"] += 1
            index = state["next_index"]
            state["next_index"] += 1
            return request, seed, page, index

    async def worker() -> None:
        while True:
            task = await take()
            if task is None:
                return
            request, seed, page, index = task
            next_request = None
            run.started_request()
            try:
                result = await _send(session, request, index, options)
                result.seed_index, result.page = seed, page
                if paginate is not None and result.ok and page + 1 < max_pages:
                    next_request = paginate.next(request, result, page)
                    if next_request == request:
                        next_request = None  # the server pointed back to the same page
            finally:
                async with changed:
                    if next_request is not None:
                        follow_ups.append((next_request, seed, page + 1))
                    state["in_flight"] -= 1
                    changed.notify_all()
                run.finished_request()
            await queue.put(result)

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
            if owns_run:
                run.completed(item)
            yield item
    finally:
        supervisor.cancel()
        try:
            await supervisor
        except (asyncio.CancelledError, Exception):
            pass
        if owns_session:
            await session.close()
        if options.socks is not None:
            await options.socks.close()
        if owns_run:
            await run.finish()


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
    if retry_rounds and stream_options.get("paginate") is not None:
        raise ValueError(
            "retry_rounds cannot be combined with paginate: a retried page would not continue the "
            "pagination. Use retries= to retry pages right away."
        )
    retry_statuses = frozenset(stream_options.get("retry_statuses", DEFAULT_RETRY_STATUSES))
    run: Run = stream_options["_run"]
    deferred: List[Result] = []
    async for result in stream(urls, **stream_options):
        if retry_rounds and _should_retry(result, retry_statuses):
            deferred.append(result)
        else:
            run.completed(result)
            yield result

    for round_number in range(1, retry_rounds + 1):
        if not deferred:
            break
        run.retry_round(round_number, retry_rounds, len(deferred), retry_round_delay)
        await asyncio.sleep(retry_round_delay)
        previous = deferred
        deferred = []
        async for retried in stream([earlier.request for earlier in previous], **stream_options):
            earlier = previous[retried.index]
            retried.index = earlier.index
            retried.seed_index, retried.page = earlier.seed_index, earlier.page
            retried.history = earlier.history + [
                Attempt(earlier.attempts + attempt.number, attempt.status, attempt.error, attempt.elapsed)
                for attempt in retried.history
            ]
            retried.attempts += earlier.attempts
            retried.elapsed += earlier.elapsed
            if round_number < retry_rounds and _should_retry(retried, retry_statuses):
                deferred.append(retried)
            else:
                run.completed(retried)
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
    max_backoff: float = ...,
    jitter: bool = ...,
    retry_statuses: Sequence[int] = ...,
    retry_rounds: int = ...,
    retry_round_delay: float = ...,
    verify_ssl: bool = ...,
    ssl: Optional[ssllib.SSLContext] = ...,
    rate_limit: Union[RateLimit, str, None] = ...,
    concurrency_per_host: int = ...,
    auth: Any = ...,
    cache: Any = ...,
    proxy: Union[str, Sequence[str], None] = ...,
    paginate: Any = ...,
    callback: Optional[Callback] = ...,
    progress: ProgressTarget = ...,
    total: Optional[int] = ...,
    log_level: LogLevel = ...,
    log_file: LogTarget = ...,
    log_format: str = ...,
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
    max_backoff: float = 30.0,
    jitter: bool = True,
    retry_statuses: Sequence[int] = DEFAULT_RETRY_STATUSES,
    retry_rounds: int = 0,
    retry_round_delay: float = 5.0,
    verify_ssl: bool = True,
    ssl: Optional[ssllib.SSLContext] = None,
    rate_limit: Union[RateLimit, str, None] = None,
    concurrency_per_host: int = 0,
    auth: Any = None,
    cache: Any = None,
    proxy: Union[str, Sequence[str], None] = None,
    paginate: Any = None,
    callback: Optional[Callback] = None,
    progress: ProgressTarget = False,
    total: Optional[int] = None,
    log_level: LogLevel = None,
    log_file: LogTarget = None,
    log_format: str = "text",
    session: Optional[aiohttp.ClientSession] = None,
    **legacy: Any,
) -> Optional[Results]:
    """Send all requests concurrently and return their results in the order given.

    Args:
        urls: URLs, or ``Request`` objects for per-request method, headers, body or params.
        method: HTTP method for plain URLs (default ``"GET"``).
        headers: Headers sent with every request; a ``Request``'s own headers are merged on top.
        params: Query parameters for every request, unless a ``Request`` sets its own.
        json: JSON body for every request, unless a ``Request`` sets its own.
        data: Form or raw body for every request, unless a ``Request`` sets its own.
        concurrency: Maximum number of requests in flight at once.
        timeout: Seconds allowed per attempt, including reading the body. ``None`` disables it.
        retries: How many times to retry a request right away after a connection error,
            a timeout or a status in ``retry_statuses``. Invalid URLs are not retried.
        backoff: Delay before the first retry in seconds, doubled for each further retry
            (0.5, 1, 2, 4, ... with the default).
        max_backoff: Upper limit for that delay in seconds (default 30), so many retries
            never wait minutes. A ``Retry-After`` from the server is followed instead, up
            to 60 seconds.
        jitter: Wait a random time between half and all of the delay, so requests that
            failed together do not retry at the same moment. ``False`` waits exactly.
            A ``Retry-After`` header (in seconds, up to 60) takes precedence.
        retry_rounds: After all requests have finished, send the ones that still failed
            for a retryable reason again, up to this many more rounds.
        retry_round_delay: Seconds to wait before each retry round.
        verify_ssl: Verify TLS certificates (default). Only disable this for hosts you control.
        ssl: A custom ``ssl.SSLContext``, for example to trust a private certificate authority.
        rate_limit: Maximum request rate per host (``host:port``), retries included:
            requests per second as a number, or a string such as ``"100/min"``,
            ``"30/5min"`` or ``"1000/h"``, or ``(count, seconds)``. ``"auto"`` follows the
            server instead: it slows down on 429 responses and ``Retry-After``, spreads
            requests by ``X-RateLimit-Remaining`` / ``X-RateLimit-Reset``, speeds back up
            when the server stops pushing back, and retries 429 responses (up to 10 times,
            not counted in ``retries``).
        concurrency_per_host: Maximum requests in flight to each host; 0 means no per-host limit.
        auth: ``reqstorm.BearerAuth``: sends a token and refreshes it once on a 401.
        cache: ``reqstorm.Cache``: reuses earlier GET responses, revalidating with ETag.
        proxy: A proxy URL, or a list of them used in turn, attempt by attempt:
            ``"http://user:pass@host:port"``, or ``socks5://``, ``socks5h://`` (the proxy
            resolves host names), ``socks4://`` and ``socks4a://`` with
            ``pip install 'reqstorm[socks]'``. ``Request(proxy=...)`` sets one per request.
        paginate: ``reqstorm.NextLink``, ``LinkHeader``, ``Cursor`` or ``PageNumber``: each
            starting URL is followed through its pages. Results carry ``page`` and
            ``seed_index``. Cannot be combined with ``retry_rounds``.
        callback: Called with each final ``Result`` as soon as it is known. May be a coroutine function.
        progress: ``True`` prints a progress line to stderr every few seconds, even while
            no request finishes: done/total, ok, failed, requests in flight, retries,
            the rate over the last minute, paused hosts and the time left. A text stream
            prints it there instead; a function receives a ``reqstorm.ProgressInfo``.
        total: Number of requests, for the percentage and the time left when ``urls`` is a
            generator (a list is counted automatically). Ignored with ``paginate``, where the
            number of pages is not known in advance.
        log_level: ``"DEBUG"``, ``"INFO"``, ``"WARNING"`` or ``"ERROR"`` to log this run on its
            own, whatever the application's logging configuration: to stderr, or to
            ``log_file``. Leave it out to log through the standard ``"reqstorm"`` logger,
            which follows the application's configuration. See the logging guide.
        log_file: With ``log_level``: a file name, a name containing ``{time}``, or a
            directory for time-stamped files. An existing file is never overwritten:
            ``run.log`` becomes ``run-2.log``. A text stream also works.
        log_format: ``"text"`` (default) or ``"json"``, one object per line.
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
    run = Run(total=_total(urls, total, paginate), progress=progress, log_level=log_level, log_file=log_file,
              log_format=log_format)  # fmt: skip
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
        max_backoff=max_backoff,
        jitter=jitter,
        retry_statuses=retry_statuses,
        verify_ssl=verify_ssl,
        ssl=ssl,
        rate_limit=rate_limit,
        concurrency_per_host=concurrency_per_host,
        auth=auth,
        cache=cache,
        proxy=proxy,
        paginate=paginate,
        session=session,
        _run=run,
    )
    run.start(describe(concurrency=concurrency, rate_limit=rate_limit, retries=retries,
                       retry_rounds=retry_rounds))  # fmt: skip
    try:
        async for result in _execute(urls, retry_rounds, retry_round_delay, stream_options):
            results.append(result)
            if callback is not None:
                outcome = callback(result)
                if asyncio.iscoroutine(outcome):
                    await outcome
    finally:
        await run.finish()
    results.sort(key=lambda r: r.index)
    return results


def _total(urls: Iterable[Any], total: Optional[int], paginate: Any) -> Optional[int]:
    if paginate is not None:
        return None
    return total if total is not None else _length(urls)


def _length(urls: Iterable[Any]) -> Optional[int]:
    try:
        return len(urls)  # type: ignore[arg-type]
    except TypeError:
        return None
