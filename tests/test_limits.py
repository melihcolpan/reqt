import io
import time

import pytest

import reqstorm
from reqstorm._limits import host_key


async def test_rate_limit_spaces_requests_to_one_host(server):
    started = time.monotonic()
    await reqstorm.fetch_all([f"{server}/ok"] * 6, rate_limit=10)
    assert time.monotonic() - started >= 0.45  # six requests at 10/s: last one starts at 0.5 s


async def test_rate_limit_is_per_host(server, second_server):
    urls = [f"{server}/ok"] * 4 + [f"{second_server}/ok"] * 4
    started = time.monotonic()
    await reqstorm.fetch_all(urls, rate_limit=5)
    elapsed = time.monotonic() - started
    assert 0.55 <= elapsed < 1.1  # both hosts in parallel: ~0.6 s; one shared limit would take ~1.4 s


async def test_rate_limit_applies_to_retries(server):
    started = time.monotonic()
    (result,) = await reqstorm.fetch_all(
        [f"{server}/flaky?key=rl&fail=2"], rate_limit=5, retries=2, backoff=0
    )
    assert result.ok and time.monotonic() - started >= 0.35


async def test_concurrency_per_host(server):
    started = time.monotonic()
    await reqstorm.fetch_all([f"{server}/slow?delay=0.2"] * 4, concurrency_per_host=2)
    assert time.monotonic() - started >= 0.38


async def test_invalid_limits():
    with pytest.raises(ValueError):
        await reqstorm.fetch_all(["http://example.invalid"], rate_limit=0)
    with pytest.raises(ValueError):
        await reqstorm.fetch_all(["http://example.invalid"], concurrency_per_host=-1)


def test_host_key():
    assert host_key("https://example.com/a") == "example.com:443"
    assert host_key("http://example.com:8080/") == "example.com:8080"
    assert host_key("not a url") is None


async def test_progress_output(server):
    output = io.StringIO()
    await reqstorm.fetch_all([f"{server}/ok"] * 3 + ["not a url"], progress=output)
    last = output.getvalue().strip().splitlines()[-1]
    assert "4/4 (100%)" in last and "ok 3" in last and "failed 1" in last


async def test_progress_without_known_total(server):
    output = io.StringIO()
    await reqstorm.fetch_all((f"{server}/ok" for _ in range(2)), progress=output)
    assert "reqstorm: 2  ok 2" in output.getvalue()
