import json

import pytest

import reqt
from reqt._limits import parse_rate


@pytest.mark.parametrize(
    "rate, per_second",
    [(5, 5.0), (0.5, 0.5), ("10/s", 10.0), ("100/min", 100 / 60), ("100 / minute", 100 / 60),
     ("30/5min", 0.1), ("1000/h", 1000 / 3600), ("2/day", 2 / 86400), ((100, 60), 100 / 60)],
)  # fmt: skip
def test_rate_formats(rate, per_second):
    assert parse_rate(rate) == pytest.approx(per_second)


@pytest.mark.parametrize("rate", ["abc", "10/fortnight", "/min", 0, "0/s", -1, True, [1, 2]])
def test_invalid_rates(rate):
    with pytest.raises((ValueError, TypeError)):
        parse_rate(rate)


async def test_rate_limit_per_minute(server):
    import time

    started = time.monotonic()
    await reqt.fetch_all([f"{server}/ok"] * 3, rate_limit="600/min")  # 10 per second
    assert time.monotonic() - started >= 0.18


async def test_history_records_every_attempt(server):
    (result,) = await reqt.fetch_all([f"{server}/flaky?key=h&fail=2"], retries=2, backoff=0.01)
    assert [(a.number, a.status) for a in result.history] == [(1, 503), (2, 503), (3, 200)]
    (failed,) = await reqt.fetch_all([f"{server}/disconnect"], retries=1, backoff=0.01)
    assert [a.status for a in failed.history] == [None, None]
    assert all("ServerDisconnected" in a.to_dict()["error"] for a in failed.history)


async def test_results_report(server):
    results = await reqt.fetch_all([f"{server}/ok", f"{server}/status/404", "not a url", f"{server}/ok"])
    assert isinstance(results, reqt.Results) and isinstance(results, list)
    assert [r.index for r in results.succeeded] == [0, 3]
    assert [r.index for r in results.failed] == [1, 2]
    summary = results.summary()
    assert summary["total"] == 4 and summary["ok"] == 2 and summary["failed"] == 2
    assert summary["failures"] == {"HTTP 404": 1, "InvalidUrlClientError": 1}
    errors = results.errors()
    assert [e["index"] for e in errors] == [1, 2]
    assert errors[0]["status"] == 404 and errors[1]["error"].startswith("InvalidUrlClientError")
    json.dumps(errors)  # plain data, ready to log or store
    assert results.to_dicts(body="text")[0]["body"] == "ok"


async def test_retry_rounds_resend_failures_at_the_end(server):
    results = await reqt.fetch_all(
        [f"{server}/flaky?key=rounds&fail=2", f"{server}/ok"], retry_rounds=2, retry_round_delay=0.05
    )
    flaky = results[0]
    assert flaky.ok and flaky.attempts == 3 and [a.number for a in flaky.history] == [1, 2, 3]
    assert results[1].ok


async def test_retry_rounds_give_up(server):
    (result,) = await reqt.fetch_all(
        [f"{server}/flaky?key=giveup&fail=9"], retry_rounds=2, retry_round_delay=0
    )
    assert result.status == 503 and result.attempts == 3


async def test_retry_rounds_skip_permanent_failures(server):
    results = await reqt.fetch_all([f"{server}/status/404", "not a url"], retry_rounds=3, retry_round_delay=0)
    assert [r.attempts for r in results] == [1, 1]


async def test_retries_and_rounds_combine(server):
    (result,) = await reqt.fetch_all(
        [f"{server}/flaky?key=both&fail=3"], retries=1, backoff=0, retry_rounds=1, retry_round_delay=0
    )
    assert result.ok and result.attempts == 4


async def test_callback_gets_final_results_once(server):
    seen = []
    await reqt.fetch_all(
        [f"{server}/flaky?key=cb&fail=1"], retry_rounds=1, retry_round_delay=0, callback=seen.append
    )
    assert len(seen) == 1 and seen[0].ok


def test_estimate():
    assert (
        str(reqt.estimate(7000, rate_limit="100/min"))
        == "7000 requests: about 1h 10m (limited by rate_limit)"
    )
    spread = reqt.estimate(7000, rate_limit="100/min", hosts=7)
    assert spread.limited_by == "rate_limit" and 590 < spread.seconds < 610
    fast = reqt.estimate(7000, concurrency=50, latency=0.3)
    assert fast.limited_by == "concurrency" and fast.seconds == pytest.approx(42)
    assert reqt.estimate(100, concurrency=100, concurrency_per_host=5, latency=1).seconds == pytest.approx(20)
    with pytest.raises(ValueError):
        reqt.estimate(-1)


async def test_progress_shows_eta(server):
    import io

    from reqt._progress import Progress

    output = io.StringIO()
    tracker = Progress(output, total=10)
    results = await reqt.fetch_all([f"{server}/ok"] * 2)
    for result in results:
        tracker._last_print = 0
        tracker.update(result)
    assert "ETA" in output.getvalue()
