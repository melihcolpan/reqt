import time

import pytest

import reqt


def test_fetch_all_sync(thread_server):
    results = reqt.fetch_all_sync([f"{thread_server}/echo?i={i}" for i in range(5)], concurrency=2)
    assert [r.json()["query"]["i"] for r in results] == [str(i) for i in range(5)]


def test_fetch_all_sync_passes_options(thread_server):
    (result,) = reqt.fetch_all_sync([f"{thread_server}/echo"], "POST", json={"a": 1})
    assert result.json()["method"] == "POST" and result.json()["body"] == '{"a": 1}'


async def test_fetch_all_sync_inside_a_running_loop(thread_server):
    # As in a Jupyter notebook: a loop is already running in this thread
    results = reqt.fetch_all_sync([f"{thread_server}/ok"] * 3)
    assert [r.text() for r in results] == ["ok"] * 3


def test_stream_sync(thread_server):
    urls = [f"{thread_server}/slow?delay=0.3", f"{thread_server}/ok"]
    assert [r.index for r in reqt.stream_sync(urls)] == [1, 0]


def test_stream_sync_stops_early(thread_server):
    started = time.monotonic()
    for _ in reqt.stream_sync([f"{thread_server}/ok"] + [f"{thread_server}/slow?delay=5"] * 3):
        break
    assert time.monotonic() - started < 2


def test_stream_sync_raises_input_errors(thread_server):
    def urls():
        yield f"{thread_server}/ok"
        raise RuntimeError("bad input")

    with pytest.raises(RuntimeError, match="bad input"):
        list(reqt.stream_sync(urls()))


def test_session_is_rejected():
    with pytest.raises(TypeError, match="session"):
        reqt.fetch_all_sync(["http://example.invalid"], session=object())
