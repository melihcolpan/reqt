import logging

import aiohttp
import pytest

import reqstorm
from reqstorm import _resources
from reqstorm._concurrency import AutoConcurrency
from reqstorm._resources import fit_concurrency


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _feed(control, clock, count, status=200, elapsed=0.03, error=None, url="https://api.test/x", epoch=None):
    """`count` attempts sent together, so all in the same epoch."""
    epoch = control.epoch if epoch is None else epoch
    change = None
    for _ in range(count):
        change = control.attempt(url, status, error, elapsed, epoch) or change
    return change


def _window(control, clock, status=200, elapsed=0.03):
    """One window in which the whole limit was in use and every attempt got `status`."""
    control._peak = control.limit
    clock.now += 1.0
    return _feed(control, clock, max(10, control.limit), status=status, elapsed=elapsed)


async def _peaks(server):
    async with aiohttp.ClientSession() as session:
        async with session.get(server + "/peaks") as response:
            return await response.json()


# -- the controller ---------------------------------------------------------------------


def test_slow_start_doubles_then_grows_by_ten_percent():
    clock = Clock()
    control = AutoConcurrency(maximum=500, clock=clock)
    assert control.limit == 8
    _window(control, clock)
    assert control.limit == 16
    _window(control, clock, status=503)
    assert control.limit == 12 and not control.slow_start and control.ceiling == 16
    control.ceiling = None
    _window(control, clock)
    assert control.limit == 13


def test_pushback_lowers_the_limit_without_waiting_for_the_window():
    clock = Clock()
    control = AutoConcurrency(maximum=500, start=40, clock=clock)
    change = _feed(control, clock, 10, status=503)  # no time has passed
    assert control.limit == 30 and change.startswith("concurrency 40 -> 30: the servers pushed back")


SIGNALS = [
    (429, None),
    (502, None),
    (504, None),
    (None, aiohttp.ServerDisconnectedError()),
    (None, TimeoutError()),
]


@pytest.mark.parametrize("status, error", SIGNALS)
def test_overload_signals(status, error):
    clock = Clock()
    control = AutoConcurrency(maximum=500, start=20, clock=clock)
    _feed(control, clock, 5, status=status, error=error)
    assert control.limit == 15


def test_attempts_sent_before_a_backoff_are_not_counted_again():
    clock = Clock()
    control = AutoConcurrency(maximum=500, start=40, clock=clock)
    old_epoch = control.epoch
    _feed(control, clock, 10, status=503)
    assert control.limit == 30
    _feed(control, clock, 50, status=503, epoch=old_epoch)  # stragglers from the burst
    assert control.limit == 30


def test_errors_that_say_nothing_about_load_are_ignored():
    clock = Clock()
    control = AutoConcurrency(maximum=500, clock=clock)
    _window(control, clock, status=404)  # a missing page is not overload
    assert control.limit == 16
    control = AutoConcurrency(maximum=500, clock=clock)
    _feed(control, clock, 20, status=None, error=aiohttp.InvalidURL("x"), url="not a url")
    clock.now += 1
    _feed(control, clock, 1, status=None, error=aiohttp.InvalidURL("x"), url="not a url")
    assert control.limit == 8 and control._attempts == 0


def test_slow_responses_ease_off_relative_to_each_host():
    clock = Clock()
    control = AutoConcurrency(maximum=500, start=10, clock=clock)
    control.slow_start = False
    for _ in range(5):
        control.attempt("https://fast.test/", 200, None, 0.02, 0)
        control.attempt("https://slow.test/", 200, None, 1.0, 0)
    control._peak = control.limit
    clock.now += 5
    control.attempt("https://fast.test/", 200, None, 0.02, 0)
    assert control.limit == 11  # each host at its own normal speed: healthy
    for _ in range(9):
        control.attempt("https://fast.test/", 200, None, 0.2, control.epoch)  # ten times its normal
    clock.now += 5
    control.attempt("https://fast.test/", 200, None, 0.2, control.epoch)
    assert control.limit == 9  # nine tenths


def test_the_limit_grows_only_when_it_is_used():
    clock = Clock()
    control = AutoConcurrency(maximum=500, clock=clock)
    control._peak = 3  # only 3 of 8 places were ever used
    clock.now += 1
    _feed(control, clock, 10)
    assert control.limit == 8


def test_the_ceiling_is_approached_then_probed_after_a_pause():
    clock = Clock()
    control = AutoConcurrency(maximum=500, start=16, clock=clock)
    _window(control, clock, status=503)  # pushback at 16
    assert control.limit == 12 and control.ceiling == 16
    for expected in (13, 14, 15):  # normal growth up to just below the ceiling
        _window(control, clock)
        assert control.limit == expected
    for _ in range(8):
        _window(control, clock)
        assert control.limit == 15  # holding
    _window(control, clock)
    assert control.limit == 16  # one probe
    _window(control, clock)
    assert control.ceiling is None and control.limit == 17  # the probe went well


def test_limit_stays_between_one_and_the_maximum():
    clock = Clock()
    control = AutoConcurrency(maximum=10, clock=clock)
    for _ in range(10):
        _window(control, clock)
    assert control.limit == 10
    for _ in range(20):
        _feed(control, clock, 10, status=503)
    assert control.limit == 1


async def test_the_gate_holds_attempts_at_the_limit():
    import asyncio

    control = AutoConcurrency(maximum=500, start=2)
    await control.acquire()
    await control.acquire()
    third = asyncio.ensure_future(control.acquire())
    await asyncio.sleep(0.05)
    assert not third.done() and control.active == 2
    await control.release()
    assert await asyncio.wait_for(third, 1) == 0 and control.active == 2


# -- end to end ---------------------------------------------------------------------------


async def test_auto_finds_the_capacity_of_an_overloaded_api(server):
    urls = [server + "/capacity?key=cap12&max=12"] * 400
    seen = []
    results = await reqstorm.fetch_all(
        urls, concurrency="auto", retries=10, backoff=0.01, progress=seen.append
    )
    assert all(result.ok for result in results)
    attempts = sum(result.attempts for result in results)
    assert attempts < 400 * 1.15, attempts  # few attempts were turned away
    final = seen[-1]
    assert final.concurrency is not None and 6 <= final.concurrency <= 16


async def test_auto_backs_off_when_responses_slow_down(server):
    urls = [server + "/congested?key=slow&per=0.01"] * 300
    seen = []
    await reqstorm.fetch_all(urls, concurrency="auto", progress=seen.append)
    peak = (await _peaks(server))["slow"]
    assert peak < 60, peak  # without backing off it would climb towards 500
    assert seen[-1].concurrency < 60


async def test_auto_respects_max_concurrency(server):
    seen = []
    await reqstorm.fetch_all(
        [server + "/ok"] * 200, concurrency="auto", max_concurrency=12, progress=seen.append
    )
    assert max(info.concurrency for info in seen) <= 12


async def test_auto_with_pagination_and_files(server, tmp_path):
    paginate = reqstorm.NextLink("links.next")
    target = tmp_path / "pages.jsonl"
    summary = await reqstorm.fetch_to_file(
        [server + "/pages/next"] * 3, target, concurrency="auto", paginate=paginate
    )
    assert summary.ok == 9


async def test_auto_is_logged(server):
    import io

    output = io.StringIO()
    urls = [server + "/capacity?key=logged&max=1000&delay=0.03"] * 300  # long enough for a few windows
    await reqstorm.fetch_all(urls, concurrency="auto", log_level="DEBUG", log_file=output)
    text = output.getvalue()
    assert "concurrency auto (max 500)" in text
    assert "concurrency 8 -> 16: responses are healthy" in text
    assert "concurrency auto: ended at" in text


@pytest.mark.parametrize("value", [0, -1, "fast", 1.5, True])
async def test_invalid_concurrency(server, value):
    with pytest.raises(ValueError, match="concurrency"):
        await reqstorm.fetch_all([server + "/ok"], concurrency=value)


# -- open file limit ------------------------------------------------------------------------


def _fake_limits(monkeypatch, soft, hard, can_raise_to=None):
    state = {"soft": soft}
    monkeypatch.setattr(_resources, "_limits", lambda: (state["soft"], hard))

    def raise_to(target, hard_limit):
        allowed = min(target, can_raise_to if can_raise_to is not None else hard_limit)
        state["soft"] = max(state["soft"], allowed)
        return state["soft"]

    monkeypatch.setattr(_resources, "_raise_to", raise_to)
    return state


def test_enough_file_descriptors_change_nothing(monkeypatch):
    _fake_limits(monkeypatch, 1024, 4096)
    notes = []
    assert fit_concurrency(100, 1, lambda level, text: notes.append(text)) == 100 and notes == []


def test_the_limit_is_raised_when_the_system_allows(monkeypatch):
    state = _fake_limits(monkeypatch, 256, 65536)
    notes = []
    assert fit_concurrency(1000, 1, lambda level, text: notes.append((level, text))) == 1000
    assert state["soft"] == 1064
    assert notes == [(logging.INFO, "raised the open file limit from 256 to 1064 for concurrency 1000")]


def test_concurrency_is_lowered_when_the_limit_cannot_be_raised(monkeypatch):
    _fake_limits(monkeypatch, 256, 256)
    notes = []
    assert fit_concurrency(1000, 1, lambda level, text: notes.append((level, text))) == 192
    level, text = notes[0]
    assert level == logging.WARNING
    assert text.startswith("concurrency lowered from 1000 to 192: the open file limit is 256")
    assert "ulimit -n 1064" in text


def test_socks_pools_count_towards_the_limit(monkeypatch):
    _fake_limits(monkeypatch, 1024, 1024)
    assert fit_concurrency(500, 3, lambda level, text: None) == (1024 - 64) // 3


def test_windows_has_no_file_limit(monkeypatch):
    monkeypatch.setattr(_resources.sys, "platform", "win32")
    assert _resources._limits() is None
    assert fit_concurrency(100_000, 1, lambda level, text: None) == 100_000


def test_the_real_limit_is_raised_in_a_fresh_process(thread_server):
    """End to end on this machine: lower the soft limit, then ask for more connections."""
    import subprocess
    import sys

    if sys.platform == "win32":
        pytest.skip("no RLIMIT_NOFILE on Windows")
    code = f"""
import resource, reqstorm
soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
resource.setrlimit(resource.RLIMIT_NOFILE, (256, hard))
results = reqstorm.fetch_all_sync(['{thread_server}/ok'] * 20, concurrency=600, log_level='INFO')
print(resource.getrlimit(resource.RLIMIT_NOFILE)[0] > 256 or 'lowered', all(r.ok for r in results))
"""
    completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stderr
    raised = "raised the open file limit from 256" in completed.stderr
    lowered = "concurrency lowered from 600" in completed.stderr
    assert raised or lowered, completed.stderr
    assert completed.stdout.split()[-1] == "True"
