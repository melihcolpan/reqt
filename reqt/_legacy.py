"""The reqt 1.x calling style, kept working with a deprecation warning."""

from __future__ import annotations

import asyncio
import logging
import warnings
from typing import Any, Callable, Iterable, Mapping, Optional

import aiohttp

logger = logging.getLogger("reqt")

_LEGACY_KEYWORDS = {"request_type", "semaphore_limit"}


def _warn() -> None:
    warnings.warn(
        "Passing a callback as `method` is the reqt 1.x API and will be removed in reqt 3.0. "
        "Use `results = await reqt.fetch_all(urls)` or `callback=`; see the README.",
        DeprecationWarning,
        stacklevel=4,
    )


async def legacy_fetch_all(
    urls: Iterable[str],
    method: Callable[..., Any],
    headers: Optional[Mapping[str, str]] = None,
    verify_ssl: bool = True,
    request_type: str = "GET",
    semaphore_limit: int = 500,
    **unexpected: Any,
) -> None:
    """Run the 1.x API: ``method`` is called with each ``aiohttp.ClientResponse``.

    Unlike 1.x, TLS certificates are verified (pass ``verify_ssl=False`` to opt out)
    and every failed request is logged instead of only connection errors.
    """
    if unexpected:
        raise TypeError(f"fetch_all() got unexpected keyword arguments: {', '.join(sorted(unexpected))}")
    if not callable(method):
        raise TypeError(
            "`request_type` and `semaphore_limit` belong to the reqt 1.x API, "
            "which needs a callback as `method`"
        )
    _warn()
    semaphore = asyncio.Semaphore(semaphore_limit)
    is_coroutine = asyncio.iscoroutinefunction(method)

    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=0)) as session:

        async def fetch(url: str) -> None:
            async with semaphore:
                try:
                    async with session.request(
                        request_type.upper(), url, headers=headers, ssl=None if verify_ssl else False
                    ) as response:
                        if is_coroutine:
                            await method(response)
                        else:
                            method(response)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("reqt: request to %s failed", url)

        await asyncio.gather(*(fetch(url) for url in urls))


class Reqt:
    """The reqt 1.x class. Deprecated: use ``reqt.fetch_all``."""

    def __init__(
        self,
        urls: Iterable[str],
        method: Callable[..., Any],
        headers: Optional[Mapping[str, str]] = None,
        request_type: str = "GET",
        semaphore_limit: int = 500,
    ) -> None:
        self.urls = urls
        self.method = method
        self.headers = headers
        self.request_type = request_type
        self.semaphore_limit = semaphore_limit

    async def fetch_all(self) -> None:
        await legacy_fetch_all(
            self.urls,
            self.method,
            headers=self.headers,
            request_type=self.request_type,
            semaphore_limit=self.semaphore_limit,
        )
