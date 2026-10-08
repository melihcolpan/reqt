"""Concurrent HTTP requests with per-request results."""

from __future__ import annotations

import asyncio
import json as jsonlib
import ssl as ssllib
import time
from dataclasses import dataclass, field
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
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

__all__ = ["HTTPStatusError", "Request", "Result", "fetch_all", "stream"]

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

    @property
    def url(self) -> str:
        return self.request.url

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

    def __repr__(self) -> str:
        outcome = f"status={self.status}" if self.error is None else f"error={self.error!r}"
        return f"<Result {self.request.method or 'GET'} {self.url} {outcome} attempts={self.attempts}>"


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


def _is_retryable(error: BaseException) -> bool:
    if isinstance(error, (aiohttp.InvalidURL, ValueError)):
        return False
    return isinstance(error, (aiohttp.ClientError, asyncio.TimeoutError))


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
            if response.status not in options.retry_statuses or attempt > options.retries:
                break
            delay = _retry_after(response.headers) or delay
        except asyncio.CancelledError:
            raise
        except Exception as error:  # one failing request must not stop the others
            result.error = error
            result.status = None
            if not _is_retryable(error) or attempt > options.retries:
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
    session: Optional[aiohttp.ClientSession] = None,
) -> AsyncIterator[Result]:
    """Send the requests concurrently and yield each ``Result`` as soon as it completes.

    ``urls`` may be any iterable, including a generator; it is consumed lazily, so
    millions of requests can be streamed without building them all in memory.
    See ``fetch_all`` for the parameters.
    """
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    if retries < 0:
        raise ValueError("retries must not be negative")
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
    )

    owns_session = session is None
    if session is None:
        session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=concurrency))

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
    verify_ssl: bool = ...,
    ssl: Optional[ssllib.SSLContext] = ...,
    callback: Optional[Callback] = ...,
    session: Optional[aiohttp.ClientSession] = ...,
) -> List[Result]: ...


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
    verify_ssl: bool = True,
    ssl: Optional[ssllib.SSLContext] = None,
    callback: Optional[Callback] = None,
    session: Optional[aiohttp.ClientSession] = None,
    **legacy: Any,
) -> Optional[List[Result]]:
    """Send all requests concurrently and return their results in the order given.

    Args:
        urls: URLs, or ``Request`` objects for per-request method, headers, body or params.
        method: HTTP method for plain URLs (default ``"GET"``).
        headers, params, json, data: Defaults applied to every request; a ``Request``'s
            own headers are merged on top, its other fields replace the default.
        concurrency: Maximum number of requests in flight at once.
        timeout: Seconds allowed per attempt, including reading the body. ``None`` disables it.
        retries: How many times to retry after a connection error, a timeout or a status
            in ``retry_statuses``. Invalid URLs are not retried.
        backoff: Delay before the first retry in seconds, doubled for each further retry.
            A ``Retry-After`` header (in seconds, up to 60) takes precedence.
        verify_ssl: Verify TLS certificates (default). Only disable this for hosts you control.
        ssl: A custom ``ssl.SSLContext``, for example to trust a private certificate authority.
        callback: Called with each ``Result`` as soon as it completes. May be a coroutine function.
        session: An existing ``aiohttp.ClientSession`` to use instead of creating one.

    A failing request never stops the others: its ``Result.error`` holds the exception.
    """
    if callable(method) or legacy:
        from ._legacy import legacy_fetch_all

        await legacy_fetch_all(urls, method, headers=headers, verify_ssl=verify_ssl, **legacy)  # type: ignore[arg-type]
        return None

    results: List[Result] = []
    async for result in stream(
        urls,
        method,
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
        session=session,
    ):
        results.append(result)
        if callback is not None:
            outcome = callback(result)
            if asyncio.iscoroutine(outcome):
                await outcome
    results.sort(key=lambda r: r.index)
    return results
