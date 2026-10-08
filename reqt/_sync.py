"""Blocking wrappers, for scripts that do not use asyncio."""

from __future__ import annotations

import asyncio
import concurrent.futures
import queue
import threading
from typing import Any, Awaitable, Iterable, Iterator, TypeVar, Union

from ._client import Request, Result, Results, fetch_all, stream
from ._files import Summary, fetch_to_db, fetch_to_file

T = TypeVar("T")


def _run(awaitable: Awaitable[T]) -> T:
    """Run a coroutine to completion, even when called from inside a running event loop.

    Inside a running loop (for example a Jupyter notebook) ``asyncio.run`` is not allowed,
    so the coroutine runs on a fresh loop in a helper thread instead.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)  # type: ignore[arg-type]
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, awaitable).result()  # type: ignore[arg-type]


def fetch_all_sync(urls: Iterable[Union[str, Request]], method: str = "GET", **options: Any) -> Results:
    """Blocking version of ``fetch_all``: send the requests and return their results in order.

    Takes the same options as ``fetch_all`` except ``session``, which belongs to an event loop.

        results = reqt.fetch_all_sync(urls, concurrency=20, retries=2)
    """
    if "session" in options:
        raise TypeError("fetch_all_sync() does not accept `session`; use fetch_all() inside your event loop")
    return _run(fetch_all(urls, method, **options))


def fetch_to_file_sync(urls: Iterable[Union[str, Request]], path: Any, **options: Any) -> Summary:
    """Blocking version of ``fetch_to_file``."""
    if "session" in options:
        raise TypeError(
            "fetch_to_file_sync() does not accept `session`; use fetch_to_file() inside your event loop"
        )
    return _run(fetch_to_file(urls, path, **options))


def fetch_to_db_sync(urls: Iterable[Union[str, Request]], connection: Any, **options: Any) -> Summary:
    """Blocking version of ``fetch_to_db``.

    The requests run on a helper thread when called from a running event loop; a sqlite3
    connection must then be opened with ``check_same_thread=False``.
    """
    if "session" in options:
        raise TypeError(
            "fetch_to_db_sync() does not accept `session`; use fetch_to_db() inside your event loop"
        )
    return _run(fetch_to_db(urls, connection, **options))


_FINISHED = object()


def stream_sync(urls: Iterable[Union[str, Request]], method: str = "GET", **options: Any) -> Iterator[Result]:
    """Blocking version of ``stream``: yield each ``Result`` as soon as it completes.

    The requests run on an event loop in a background thread. Leaving the loop early
    (``break``) stops the remaining requests.

        for result in reqt.stream_sync(urls, concurrency=50):
            print(result.url, result.status)
    """
    if "session" in options:
        raise TypeError("stream_sync() does not accept `session`; use stream() inside your event loop")
    results: queue.Queue[Any] = queue.Queue(maxsize=max(int(options.get("concurrency", 100)) * 2, 1))
    stop = threading.Event()

    async def produce() -> None:
        generator = stream(urls, method, **options)
        try:
            async for result in generator:
                while not stop.is_set():
                    try:
                        results.put_nowait(result)
                        break
                    except queue.Full:
                        await asyncio.sleep(0.01)  # the consumer is slower: wait without blocking the loop
                if stop.is_set():
                    return
        finally:
            await generator.aclose()

    loop = asyncio.new_event_loop()
    task: asyncio.Future[None] = loop.create_task(produce())

    def run() -> None:
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(task)
        except asyncio.CancelledError:
            results.put(_FINISHED)
        except BaseException as error:
            results.put(error)
        else:
            results.put(_FINISHED)
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    thread = threading.Thread(target=run, name="reqt-stream", daemon=True)
    thread.start()
    try:
        while True:
            item = results.get()
            if item is _FINISHED:
                return
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        stop.set()
        if thread.is_alive():
            loop.call_soon_threadsafe(task.cancel)  # stop requests still in flight
        while thread.is_alive():  # let the producer notice `stop` even if the queue is full
            try:
                results.get_nowait()
            except queue.Empty:
                thread.join(0.05)
