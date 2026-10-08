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

    async def raw_json(request: web.Request) -> web.Response:
        """Returns the `body` query parameter as is, with a JSON content type."""
        return web.Response(text=request.query["body"], content_type="application/json")

    items = [{"id": number, "name": f"item {number}"} for number in range(1, 8)]

    def page_slice(page: int) -> list:
        return items[(page - 1) * 3 : page * 3]

    async def pages_next(request: web.Request) -> web.Response:
        """Seven items, three per page, with a relative "links.next" URL."""
        page = int(request.query.get("page", "1"))
        rest = page * 3 < len(items)
        links = {"next": f"/pages/next?page={page + 1}" if rest else None}
        return web.json_response({"items": page_slice(page), "links": links})

    async def pages_link(request: web.Request) -> web.Response:
        page = int(request.query.get("page", "1"))
        headers = {}
        if page * 3 < len(items):
            headers["Link"] = (
                f'<{request.url.with_query(page=page + 1)}>; rel="next", <{request.url}>; rel="self"'
            )
        return web.json_response({"items": page_slice(page)}, headers=headers)

    async def pages_cursor(request: web.Request) -> web.Response:
        start = int(request.query.get("after", "0"))
        chunk = items[start : start + 3]
        cursor = str(start + 3) if start + 3 < len(items) else None
        return web.json_response({"items": chunk, "meta": {"next_cursor": cursor}})

    async def pages_number(request: web.Request) -> web.Response:
        return web.json_response({"items": page_slice(int(request.query.get("page", "1")))})

    async def pages_loop(request: web.Request) -> web.Response:
        """Always has a next page; with ?same=1 the next page is this page again."""
        if "same" in request.query:
            return web.json_response({"next": str(request.rel_url)})
        return web.json_response({"next": f"/pages/loop?n={int(request.query.get('n', '0')) + 1}"})

    limited: collections.Counter = collections.Counter()

    async def limited_route(request: web.Request) -> web.Response:
        """429 with Retry-After for the first `fail` requests of `key`, then 200."""
        key = request.query["key"]
        limited[key] += 1
        if limited[key] <= int(request.query.get("fail", "1")):
            return web.Response(status=429, headers={"Retry-After": request.query.get("retry_after", "0.2")})
        return web.Response(text="ok", headers={"X-RateLimit-Remaining": "100", "X-RateLimit-Reset": "1"})

    tokens = {"valid": "token-1"}
    refreshes: collections.Counter = collections.Counter()

    async def protected(request: web.Request) -> web.Response:
        if request.headers.get("Authorization") != f"Bearer {tokens['valid']}":
            return web.Response(status=401, text="expired")
        return web.Response(text="secret")

    async def rotate(request: web.Request) -> web.Response:
        """Expire the current token; the next valid one is token-<n+1>."""
        number = int(tokens["valid"].split("-")[1]) + 1
        tokens["valid"] = f"token-{number}"
        refreshes["rotations"] += 1
        return web.Response(text=tokens["valid"])

    etag_hits: collections.Counter = collections.Counter()

    async def etag(request: web.Request) -> web.Response:
        key = request.query.get("key", "default")
        version = request.query.get("version", "1")
        tag = f'"v{version}"'
        if request.headers.get("If-None-Match") == tag:
            etag_hits[f"{key}:304"] += 1
            return web.Response(status=304, headers={"ETag": tag})
        etag_hits[f"{key}:200"] += 1
        return web.json_response({"version": version}, headers={"ETag": tag})

    async def counts(request: web.Request) -> web.Response:
        return web.json_response({**etag_hits, **refreshes})

    async def proxy_check(request: web.Request) -> web.Response:
        """Used as a stand-in proxy: reports the Host the client asked for and the port it reached."""
        return web.json_response(
            {"host": request.host, "port": request.transport.get_extra_info("sockname")[1]}
        )

    app = web.Application()
    app.add_routes(
        [
            web.get("/pages/next", pages_next),
            web.get("/pages/link", pages_link),
            web.get("/pages/cursor", pages_cursor),
            web.get("/pages/number", pages_number),
            web.get("/pages/loop", pages_loop),
            web.get("/limited", limited_route),
            web.get("/protected", protected),
            web.post("/rotate", rotate),
            web.get("/etag", etag),
            web.get("/counts", counts),
            web.get("/proxy-check", proxy_check),
            web.get("/json", raw_json),
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
