import asyncio
import collections
import ssl

import pytest
import pytest_asyncio
import trustme
from aiohttp import web


def make_app() -> web.Application:
    attempts: collections.Counter = collections.Counter()

    async def ok(request: web.Request) -> web.Response:
        return web.Response(text="ok")

    async def echo(request: web.Request) -> web.Response:
        return web.json_response(
            {
                "method": request.method,
                "query": dict(request.query),
                "headers": {k: v for k, v in request.headers.items() if k.lower().startswith("x-")},
                "body": (await request.read()).decode(),
            }
        )

    async def status(request: web.Request) -> web.Response:
        return web.Response(status=int(request.match_info["code"]), text="status")

    async def slow(request: web.Request) -> web.Response:
        await asyncio.sleep(float(request.query.get("delay", "5")))
        return web.Response(text="late")

    async def disconnect(request: web.Request) -> web.Response:
        assert request.transport is not None
        request.transport.close()
        return web.Response(text="never sent")

    async def flaky(request: web.Request) -> web.Response:
        """Fails with 503 `fail` times per `key`, then succeeds."""
        key = request.query["key"]
        attempts[key] += 1
        if attempts[key] <= int(request.query.get("fail", "1")):
            headers = {"Retry-After": request.query["retry_after"]} if "retry_after" in request.query else {}
            return web.Response(status=503, headers=headers, text="unavailable")
        return web.Response(text=f"ok after {attempts[key]}")

    async def latin1(request: web.Request) -> web.Response:
        return web.Response(body="café".encode("latin-1"), content_type="text/plain", charset="latin-1")

    app = web.Application()
    app.add_routes(
        [
            web.get("/ok", ok),
            web.route("*", "/echo", echo),
            web.get("/status/{code}", status),
            web.get("/slow", slow),
            web.get("/disconnect", disconnect),
            web.get("/flaky", flaky),
            web.get("/latin1", latin1),
        ]
    )
    return app


async def _serve(ssl_context=None):
    # Cancel a handler when its client disconnects, so slow handlers do not delay shutdown
    runner = web.AppRunner(make_app(), handler_cancellation=True)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=ssl_context)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    return runner, port


@pytest_asyncio.fixture
async def server():
    runner, port = await _serve()
    yield f"http://127.0.0.1:{port}"
    await runner.cleanup()


@pytest.fixture(scope="session")
def certificate_authority():
    return trustme.CA()


@pytest_asyncio.fixture
async def tls_server(certificate_authority):
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    certificate_authority.issue_cert("127.0.0.1").configure_cert(context)
    runner, port = await _serve(context)
    yield f"https://127.0.0.1:{port}"
    await runner.cleanup()


@pytest.fixture
def trusted_context(certificate_authority):
    context = ssl.create_default_context()
    certificate_authority.configure_trust(context)
    return context


@pytest_asyncio.fixture
async def second_server():
    runner, port = await _serve()
    yield f"http://127.0.0.1:{port}"
    await runner.cleanup()


@pytest.fixture
def thread_server():
    """A server on its own event loop in a background thread, for the blocking API."""
    import threading

    loop = asyncio.new_event_loop()
    ready = threading.Event()
    state = {}

    def run():
        asyncio.set_event_loop(loop)
        state["runner"], state["port"] = loop.run_until_complete(_serve())
        ready.set()
        loop.run_forever()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    ready.wait(5)
    yield f"http://127.0.0.1:{state['port']}"
    asyncio.run_coroutine_threadsafe(state["runner"].cleanup(), loop).result(5)
    loop.call_soon_threadsafe(loop.stop)
    thread.join(5)
    loop.close()
