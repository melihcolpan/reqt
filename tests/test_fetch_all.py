import asyncio
import time

import aiohttp
import pytest

import reqstorm


async def test_returns_results_in_input_order(server):
    urls = [f"{server}/echo?i={i}" for i in range(20)]
    results = await reqstorm.fetch_all(urls)
    assert [r.json()["query"]["i"] for r in results] == [str(i) for i in range(20)]
    assert [r.index for r in results] == list(range(20))
    assert all(r.ok and r.status == 200 and r.attempts == 1 for r in results)


async def test_one_failure_does_not_stop_the_others(server):
    urls = [f"{server}/disconnect", "not a url", f"{server}/ok", f"{server}/ok"]
    results = await reqstorm.fetch_all(urls)
    assert isinstance(results[0].error, aiohttp.ClientError)
    assert isinstance(results[1].error, (aiohttp.InvalidURL, ValueError))
    assert [r.text() for r in results[2:]] == ["ok", "ok"]
    assert [r.ok for r in results] == [False, False, True, True]


async def test_timeout_is_per_request(server):
    started = time.monotonic()
    results = await reqstorm.fetch_all([f"{server}/slow?delay=5", f"{server}/ok"], timeout=0.3)
    assert time.monotonic() - started < 2
    assert isinstance(results[0].error, asyncio.TimeoutError)
    assert results[1].ok


async def test_http_error_status_is_a_result_not_an_exception(server):
    (result,) = await reqstorm.fetch_all([f"{server}/status/404"])
    assert result.status == 404 and result.error is None and not result.ok
    with pytest.raises(reqstorm.HTTPStatusError, match="404"):
        result.raise_for_error()


async def test_raise_for_error_reraises_the_request_error(server):
    (result,) = await reqstorm.fetch_all([f"{server}/disconnect"])
    with pytest.raises(aiohttp.ClientError):
        result.raise_for_error()


async def test_retries_retryable_status(server):
    (result,) = await reqstorm.fetch_all([f"{server}/flaky?key=a&fail=2"], retries=3, backoff=0.01)
    assert result.ok and result.attempts == 3 and result.text() == "ok after 3"


async def test_no_retry_by_default(server):
    (result,) = await reqstorm.fetch_all([f"{server}/flaky?key=b&fail=1"])
    assert result.status == 503 and result.attempts == 1


async def test_retries_give_up_and_keep_last_response(server):
    (result,) = await reqstorm.fetch_all([f"{server}/flaky?key=c&fail=10"], retries=2, backoff=0.01)
    assert result.status == 503 and result.attempts == 3


async def test_retry_after_header_is_honoured(server):
    started = time.monotonic()
    (result,) = await reqstorm.fetch_all(
        [f"{server}/flaky?key=d&fail=1&retry_after=0.4"], retries=1, backoff=0.01
    )
    assert result.ok and time.monotonic() - started >= 0.4


async def test_connection_errors_are_retried(server):
    (result,) = await reqstorm.fetch_all([f"{server}/disconnect"], retries=2, backoff=0.01)
    assert result.attempts == 3 and isinstance(result.error, aiohttp.ClientError)


async def test_invalid_urls_are_not_retried():
    (result,) = await reqstorm.fetch_all(["not a url"], retries=3, backoff=0.01)
    assert result.attempts == 1


async def test_defaults_and_per_request_overrides(server):
    results = await reqstorm.fetch_all(
        [
            f"{server}/echo",
            reqstorm.Request(f"{server}/echo", method="POST", json={"a": 1}, headers={"X-Two": "2"}),
            reqstorm.Request(f"{server}/echo", params={"q": "override"}),
        ],
        headers={"X-One": "1"},
        params={"q": "default"},
    )
    first, second, third = (r.json() for r in results)
    assert first == {"method": "GET", "query": {"q": "default"}, "headers": {"X-One": "1"}, "body": ""}
    assert second["method"] == "POST" and second["body"] == '{"a": 1}'
    assert second["headers"] == {"X-One": "1", "X-Two": "2"}
    assert third["query"] == {"q": "override"}


async def test_method_for_plain_urls(server):
    (result,) = await reqstorm.fetch_all([f"{server}/echo"], "put", data=b"payload")
    assert result.json()["method"] == "PUT" and result.json()["body"] == "payload"


async def test_concurrency_limit(server):
    started = time.monotonic()
    await reqstorm.fetch_all([f"{server}/slow?delay=0.2"] * 6, concurrency=2)
    assert time.monotonic() - started >= 0.55  # three rounds of two


async def test_concurrency_allows_parallel_requests(server):
    started = time.monotonic()
    await reqstorm.fetch_all([f"{server}/slow?delay=0.3"] * 20, concurrency=20)
    assert time.monotonic() - started < 1.5


async def test_sync_and_async_callbacks(server):
    seen_sync, seen_async = [], []

    async def on_result(result):
        seen_async.append(result.status)

    await reqstorm.fetch_all([f"{server}/ok"] * 3, callback=lambda r: seen_sync.append(r.status))
    await reqstorm.fetch_all([f"{server}/ok"] * 3, callback=on_result)
    assert seen_sync == seen_async == [200, 200, 200]


async def test_callback_errors_propagate(server):
    def broken(result):
        raise RuntimeError("bug in callback")

    with pytest.raises(RuntimeError, match="bug in callback"):
        await reqstorm.fetch_all([f"{server}/ok"] * 5, callback=broken)


async def test_text_uses_response_charset(server):
    (result,) = await reqstorm.fetch_all([f"{server}/latin1"])
    assert result.text() == "café"


async def test_external_session_is_not_closed(server):
    async with aiohttp.ClientSession() as session:
        (result,) = await reqstorm.fetch_all([f"{server}/ok"], session=session)
        assert result.ok and not session.closed


async def test_invalid_arguments():
    with pytest.raises(ValueError):
        await reqstorm.fetch_all(["http://example.invalid"], concurrency=0)
    with pytest.raises(ValueError):
        await reqstorm.fetch_all(["http://example.invalid"], retries=-1)


async def test_empty_input():
    assert await reqstorm.fetch_all([]) == []
