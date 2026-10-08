import json

import aiohttp
import pytest

import reqstorm


async def _counts(server):
    async with aiohttp.ClientSession() as session:
        async with session.get(server + "/counts") as response:
            return await response.json()


async def test_unchanged_resource_is_revalidated_with_a_304(server, tmp_path):
    cache = reqstorm.Cache(tmp_path / "cache.sqlite")
    first = await reqstorm.fetch_all([server + "/etag?key=a"], cache=cache)
    second = await reqstorm.fetch_all([server + "/etag?key=a"], cache=cache)
    assert not first[0].from_cache and second[0].from_cache
    assert second[0].status == 200 and second[0].json() == {"version": "1"}
    assert second[0].history[0].status == 304
    assert await _counts(server) == {"a:200": 1, "a:304": 1}
    assert cache.revalidated == 1 and len(cache) == 1


async def test_changed_resource_is_fetched_again(server, tmp_path):
    cache = reqstorm.Cache(tmp_path / "cache.sqlite")
    await reqstorm.fetch_all([server + "/etag?key=b"], cache=cache)
    # Same cache key needs the same URL, so the server's version comes from a header-free toggle:
    results = await reqstorm.fetch_all([reqstorm.Request(server + "/etag?key=b", params={"version": "2"})],
                                       cache=cache)  # fmt: skip
    assert results[0].json() == {"version": "2"} and not results[0].from_cache


async def test_fresh_entries_skip_the_network(server, tmp_path):
    cache = reqstorm.Cache(tmp_path / "cache.sqlite", ttl=60)
    await reqstorm.fetch_all([server + "/etag?key=c"], cache=cache)
    results = await reqstorm.fetch_all([server + "/etag?key=c"] * 3, cache=cache)
    assert all(r.from_cache and r.history == [] for r in results)
    assert await _counts(server) == {"c:200": 1}
    assert cache.hits == 3


async def test_cache_survives_a_new_cache_object(server, tmp_path):
    path = tmp_path / "cache.sqlite"
    await reqstorm.fetch_all([server + "/etag?key=d"], cache=reqstorm.Cache(path))
    results = await reqstorm.fetch_all([server + "/etag?key=d"], cache=reqstorm.Cache(path, ttl=60))
    assert results[0].from_cache


async def test_only_successful_gets_are_cached(server, tmp_path):
    cache = reqstorm.Cache(tmp_path / "cache.sqlite", ttl=60)
    await reqstorm.fetch_all([server + "/status/500", server + "/status/404"], cache=cache)
    await reqstorm.fetch_all([server + "/echo"], method="POST", json={"a": 1}, cache=cache)
    assert len(cache) == 0
    cache.clear()
    cache.close()


def test_cache_keys_depend_on_params_and_auth_headers():
    key = reqstorm.Cache.key
    assert key("GET", "http://x/", {"a": 1}, None) != key("GET", "http://x/", {"a": 2}, None)
    assert key("GET", "http://x/", None, {"Authorization": "a"}) != key(
        "GET", "http://x/", None, {"Authorization": "b"}
    )
    assert key("GET", "http://x/", None, {"X-Trace": "1"}) == key("GET", "http://x/", None, None)
    assert key("POST", "http://x/", None, None) is None


def test_negative_ttl_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        reqstorm.Cache(tmp_path / "c.sqlite", ttl=-1)


async def test_cached_results_are_counted_in_the_report(server, tmp_path):
    cache = reqstorm.Cache(tmp_path / "cache.sqlite", ttl=60)
    await reqstorm.fetch_all([server + "/etag?key=e"], cache=cache)
    report = (await reqstorm.fetch_all([server + "/etag?key=e"] * 2, cache=cache)).report()
    assert report["from_cache"] == 2 and report["retries"] == 0


async def test_proxy_receives_the_request(server, second_server):
    port = int(second_server.rsplit(":", 1)[1])
    results = await reqstorm.fetch_all(["http://api.example.test/proxy-check"], proxy=second_server)
    assert results[0].json() == {"host": "api.example.test", "port": port}


async def test_proxy_pool_rotates(server, second_server):
    ports = {int(url.rsplit(":", 1)[1]) for url in (server, second_server)}
    urls = ["http://api.example.test/proxy-check"] * 4
    results = await reqstorm.fetch_all(urls, proxy=[server, second_server], concurrency=1)
    seen = [r.json()["port"] for r in results]
    assert set(seen) == ports and seen[0] != seen[1]


async def test_per_request_proxy_overrides_the_pool(server, second_server):
    port = int(second_server.rsplit(":", 1)[1])
    request = reqstorm.Request("http://api.example.test/proxy-check", proxy=second_server)
    results = await reqstorm.fetch_all([request], proxy=server)
    assert results[0].json()["port"] == port


async def test_unreachable_proxy_is_an_error_result():
    results = await reqstorm.fetch_all(["http://api.example.test/"], proxy="http://127.0.0.1:9", timeout=2)
    assert results[0].error is not None and not results[0].ok
    assert json.dumps(results[0].to_dict())  # serialisable
