import time

import pytest

import reqstorm
from reqstorm._client import _Options
from reqstorm._socks import is_socks, parse

pytest.importorskip("aiohttp_socks")


def _port(server):
    return int(server.rsplit(":", 1)[1])


async def test_socks5_connects_through_the_proxy(server, socks_proxy):
    results = await reqstorm.fetch_all([server + "/ok"], proxy=socks_proxy.url("socks5"))
    assert results[0].text() == "ok"
    assert socks_proxy.requests == [(5, "127.0.0.1", _port(server))]


async def test_socks5h_lets_the_proxy_resolve_names(server, socks_proxy):
    url = f"http://api.example.test:{_port(server)}/ok"
    results = await reqstorm.fetch_all([url], proxy=socks_proxy.url("socks5h"))
    assert results[0].text() == "ok"
    assert socks_proxy.requests == [(5, "api.example.test", _port(server))]


async def test_socks5_resolves_names_locally(server, socks_proxy):
    url = f"http://api.example.test:{_port(server)}/ok"
    results = await reqstorm.fetch_all([url], proxy=socks_proxy.url("socks5"), timeout=5)
    assert results[0].error is not None  # the made-up name does not resolve here
    assert socks_proxy.requests == []


async def test_socks5_with_username_and_password(server, socks_proxy_auth):
    proxy = socks_proxy_auth.url("socks5", ("ayse", "p%40ss%3Aword"))  # percent-encoded in the URL
    results = await reqstorm.fetch_all([server + "/ok"], proxy=proxy)
    assert results[0].text() == "ok"
    wrong = await reqstorm.fetch_all([server + "/ok"], proxy=socks_proxy_auth.url("socks5", ("ayse", "x")))
    assert wrong[0].error is not None and not wrong[0].ok


@pytest.mark.parametrize("scheme, requested", [("socks4", "127.0.0.1"), ("socks4a", "api.example.test")])
async def test_socks4_and_4a(server, socks_proxy, scheme, requested):
    host = "127.0.0.1" if scheme == "socks4" else "api.example.test"
    results = await reqstorm.fetch_all([f"http://{host}:{_port(server)}/ok"], proxy=socks_proxy.url(scheme))
    assert results[0].text() == "ok"
    assert socks_proxy.requests == [(4, requested, _port(server))]


async def test_https_through_socks(tls_server, trusted_context, socks_proxy):
    results = await reqstorm.fetch_all([tls_server + "/ok"], proxy=socks_proxy.url(), ssl=trusted_context)
    assert results[0].text() == "ok" and len(socks_proxy.requests) == 1


async def test_pool_mixes_socks_and_http_proxies(server, second_server, socks_proxy):
    # second_server stands in for an HTTP proxy and answers itself; the SOCKS proxy
    # forwards to the real server. /proxy-check reports which port answered.
    url = f"http://api.example.test:{_port(server)}/proxy-check"
    proxies = [second_server, socks_proxy.url("socks5h")]
    results = await reqstorm.fetch_all([url] * 4, proxy=proxies, concurrency=1)
    ports = [result.json()["port"] for result in results]
    assert ports == [_port(second_server), _port(server)] * 2
    assert len(socks_proxy.requests) == 1  # the SOCKS connection is reused


async def test_per_request_socks_proxy_and_connection_reuse(server, socks_proxy):
    requests = [reqstorm.Request(server + "/ok", proxy=socks_proxy.url()) for _ in range(5)]
    results = await reqstorm.fetch_all(requests, concurrency=1)
    assert all(result.ok for result in results)
    assert len(socks_proxy.requests) == 1  # one connection, kept alive


async def test_socks_cannot_share_a_user_session(server, socks_proxy):
    import aiohttp

    async with aiohttp.ClientSession() as session:
        with pytest.raises(ValueError, match="session"):
            await reqstorm.fetch_all([server + "/ok"], proxy=socks_proxy.url(), session=session)


async def test_missing_aiohttp_socks_is_explained(server, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def without_socks(name, *args, **kwargs):
        if name.startswith("aiohttp_socks"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_socks)
    with pytest.raises(ImportError, match=r"reqstorm\[socks\]"):
        await reqstorm.fetch_all([server + "/ok"], proxy="socks5://127.0.0.1:1080")


def test_proxy_url_parsing():
    assert parse("socks5h://user:p%40ss@proxy.example.com") == (
        "socks5",
        "proxy.example.com",
        1080,
        "user",
        "p@ss",
        True,
    )
    assert parse("SOCKS4://10.0.0.1:9050")[:3] == ("socks4", "10.0.0.1", 9050)
    assert is_socks("socks5://x:1") and not is_socks("http://x:1") and not is_socks(None)
    with pytest.raises(ValueError):
        parse("socks5://")


def _options(**overrides):
    import aiohttp

    defaults = dict(method="GET", headers=None, params=None, json=None, data=None,
                    timeout=aiohttp.ClientTimeout(total=1), retries=10, backoff=0.5,
                    retry_statuses=frozenset(), ssl=True, rate_limiter=None)  # fmt: skip
    return _Options(**{**defaults, **overrides})


def test_backoff_doubles_and_stops_at_max_backoff():
    options = _options(jitter=False, max_backoff=4)
    assert [options.backoff_delay(n) for n in range(1, 7)] == [0.5, 1, 2, 4, 4, 4]


def test_jitter_stays_between_half_and_the_full_delay():
    options = _options(max_backoff=30)
    delays = [options.backoff_delay(4) for _ in range(500)]  # 4 seconds without jitter
    assert all(2 <= delay <= 4 for delay in delays)
    assert max(delays) - min(delays) > 1  # actually spread out


async def test_max_backoff_limits_the_real_wait(server):
    started = time.monotonic()
    results = await reqstorm.fetch_all(
        [server + "/flaky?key=cap&fail=3"], retries=3, backoff=10, max_backoff=0.1
    )
    assert results[0].ok and time.monotonic() - started < 1.5


async def test_negative_backoff_is_rejected(server):
    with pytest.raises(ValueError, match="backoff"):
        await reqstorm.fetch_all([server + "/ok"], max_backoff=-1)
