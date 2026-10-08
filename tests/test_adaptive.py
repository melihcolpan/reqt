import asyncio
import time

import pytest

import reqstorm
from reqstorm._adaptive import AdaptiveRateLimiter, seconds_until_reset


async def test_auto_retries_429_after_retry_after(server):
    started = time.monotonic()
    results = await reqstorm.fetch_all([server + "/limited?key=a&fail=2&retry_after=0.2"], rate_limit="auto")
    assert results[0].status == 200
    assert [attempt.status for attempt in results[0].history] == [429, 429, 200]
    assert time.monotonic() - started >= 0.35  # paused for Retry-After at least twice


async def test_429_without_auto_is_left_to_retries(server):
    results = await reqstorm.fetch_all([server + "/limited?key=b&fail=1&retry_after=0"])
    assert results[0].status == 429 and results[0].attempts == 1
    results = await reqstorm.fetch_all([server + "/limited?key=b2&fail=1&retry_after=0"], retries=1)
    assert results[0].status == 200


async def test_auto_pauses_the_whole_host_not_just_one_request(server):
    urls = [server + "/limited?key=c&fail=1&retry_after=0.3"] + [server + "/ok"] * 5
    results = await reqstorm.fetch_all(urls, rate_limit="auto", concurrency=1)
    assert results.succeeded and len(results.succeeded) == 6
    after = [r for r in results if r.url.endswith("/ok")]
    assert all(r.status == 200 for r in after)


async def test_auto_gives_up_after_the_throttled_retry_limit(server, monkeypatch):
    from reqstorm import _client

    assert _client.MAX_THROTTLED_RETRIES == 10
    monkeypatch.setattr(_client, "MAX_THROTTLED_RETRIES", 3)
    results = await reqstorm.fetch_all([server + "/limited?key=d&fail=100&retry_after=0"], rate_limit="auto")
    assert results[0].status == 429 and results[0].attempts == 4


def test_spacing_follows_remaining_and_reset_headers():
    limiter = AdaptiveRateLimiter()
    limiter.record("http://api.test/x", 200, {"X-RateLimit-Remaining": "50", "X-RateLimit-Reset": "10"})
    assert limiter.interval("http://api.test/y") == pytest.approx(0.2)
    limiter.record("http://other.test/x", 200, {})
    assert limiter.interval("http://other.test/x") == 0.0


def test_429_doubles_spacing_and_success_recovers():
    limiter = AdaptiveRateLimiter()
    limiter.record("http://api.test/", 429, {"Retry-After": "0"})
    limiter.record("http://api.test/", 429, {"Retry-After": "0"})
    assert limiter.interval("http://api.test/") == pytest.approx(0.2)
    for _ in range(40):
        limiter.record("http://api.test/", 200, {})
    assert limiter.interval("http://api.test/") == 0.0


async def test_no_remaining_requests_pauses_until_reset():
    limiter = AdaptiveRateLimiter()
    limiter.record("http://api.test/", 200, {"RateLimit-Remaining": "0", "RateLimit-Reset": "0.3"})
    started = time.monotonic()
    await limiter.wait("http://api.test/")
    assert time.monotonic() - started >= 0.25


def test_reset_header_formats():
    now = 1_700_000_000.0
    assert seconds_until_reset({"X-RateLimit-Reset": str(now + 30)}, now) == pytest.approx(30)  # Unix time
    assert seconds_until_reset({"RateLimit-Reset": "12"}, now) == 12
    assert seconds_until_reset({"Retry-After": "Tue, 14 Nov 2023 22:13:50 GMT"}, now) == pytest.approx(30)
    assert seconds_until_reset({"Retry-After": "1000000"}, now) == 300  # capped
    assert seconds_until_reset({"Retry-After": "soon"}, now) is None
    assert seconds_until_reset({}, now) is None


async def test_adaptive_spacing_is_applied_per_host():
    limiter = AdaptiveRateLimiter()
    limiter.record("http://slow.test/", 200, {"X-RateLimit-Remaining": "10", "X-RateLimit-Reset": "1"})
    started = time.monotonic()
    await asyncio.gather(*(limiter.wait("http://slow.test/") for _ in range(4)))
    assert time.monotonic() - started >= 0.25
    started = time.monotonic()
    await asyncio.gather(*(limiter.wait("http://fast.test/") for _ in range(20)))
    assert time.monotonic() - started < 0.05
