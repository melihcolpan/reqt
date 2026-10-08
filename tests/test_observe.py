import asyncio
import io
import json
import logging
import os
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

import reqstorm
from reqstorm import _observe
from reqstorm._observe import _open_new, log_path, redact


@pytest.fixture
def fast_ticks(monkeypatch):
    monkeypatch.setattr(_observe, "CALLBACK_INTERVAL", 0.1)
    monkeypatch.setattr(_observe, "LOG_INTERVAL", 0.1)


# -- progress ---------------------------------------------------------------------------


async def test_progress_function_gets_updates_while_nothing_finishes(server, fast_ticks):
    seen = []
    await reqstorm.fetch_all([server + "/slow?delay=0.6"] * 2, progress=seen.append)
    waiting = [info for info in seen if not info.finished]
    assert waiting and waiting[0].done == 0 and waiting[0].in_flight == 2
    final = seen[-1]
    assert final.finished and final.done == 2 and final.ok == 2 and final.in_flight == 0


async def test_paused_hosts_and_retries_are_visible(server, fast_ticks):
    seen = []
    urls = [server + "/limited?key=obs&fail=1&retry_after=0.6"]
    await reqstorm.fetch_all(urls, rate_limit="auto", progress=seen.append)
    assert any(info.paused for info in seen)
    host = server.split("//")[1]
    assert any(host in str(info) and "paused" in str(info) for info in seen)


async def test_retry_rounds_appear_in_progress(server, fast_ticks):
    seen = []
    await reqstorm.fetch_all([server + "/flaky?key=rr&fail=1"], retry_rounds=1, retry_round_delay=0.3,
                             progress=seen.append)  # fmt: skip
    assert any(info.round == 1 and info.rounds == 1 for info in seen)


async def test_total_gives_a_percentage_for_generators(server):
    seen = []
    await reqstorm.fetch_all((server + "/ok" for _ in range(4)), total=4, progress=seen.append)
    assert seen[-1].total == 4 and "4/4 (100%)" in str(seen[-1])


async def test_progress_line_on_a_stream_and_in_stream(server, fast_ticks):
    output = io.StringIO()
    async for _ in reqstorm.stream([server + "/slow?delay=0.3", server + "/ok"], progress=output):
        pass
    lines = output.getvalue().splitlines()
    assert "active" in lines[0] and lines[-1].startswith("reqstorm: 2/2 (100%)") and " in " in lines[-1]


async def test_terminal_output_clears_the_line_before_log_messages(server):
    class Terminal(io.StringIO):
        def isatty(self):
            return True

    terminal = Terminal()
    await reqstorm.fetch_all(
        [server + "/status/404"], progress=terminal, log_level="ERROR", log_file=terminal
    )
    text = terminal.getvalue()
    assert "\r\x1b[K" in text and "failed after 1 attempt" in text


def test_eta_uses_the_recent_rate_and_waits_for_paused_hosts():
    from reqstorm._client import Request, Result
    from reqstorm._observe import Run

    run = Run(total=100)
    for index in range(10):
        run.completed(Result(Request("https://a.test/"), index=index, status=200))

    class Limiter:
        def paused(self):
            return {"a.test:443": 120.0}

    plain = run.snapshot().eta
    run.limiters.append(Limiter())
    assert run.snapshot().eta == pytest.approx(plain + 120, rel=0.05)


# -- logging ----------------------------------------------------------------------------


async def test_standard_logger_follows_the_application(server, caplog):
    caplog.set_level(logging.WARNING, logger="reqstorm")
    await reqstorm.fetch_all([server + "/flaky?key=std&fail=1", server + "/status/404"], retries=1, backoff=0)
    messages = [
        (record.levelname, record.getMessage()) for record in caplog.records if record.name == "reqstorm"
    ]
    assert any(level == "WARNING" and "retrying in" in text for level, text in messages)
    assert any(level == "ERROR" and "/status/404 failed after 1 attempt" in text for level, text in messages)
    assert not any(level == "INFO" for level, _ in messages)


async def test_log_level_is_independent_of_the_application(server, caplog):
    caplog.set_level(logging.CRITICAL)  # the application shows almost nothing
    logging.getLogger("reqstorm").setLevel(logging.CRITICAL)
    output = io.StringIO()
    try:
        await reqstorm.fetch_all([server + "/ok"], log_level="DEBUG", log_file=output)
    finally:
        logging.getLogger("reqstorm").setLevel(logging.NOTSET)
    text = output.getvalue()
    assert "INFO    reqstorm: starting 1 requests" in text
    assert "DEBUG   reqstorm: GET " in text and "-> HTTP 200" in text
    assert "finished 1 requests" in text
    assert not [record for record in caplog.records if record.name == "reqstorm"]  # nothing leaked


async def test_two_runs_with_different_levels(server):
    quiet, loud = io.StringIO(), io.StringIO()
    await asyncio.gather(
        reqstorm.fetch_all([server + "/status/500"], log_level="CRITICAL", log_file=quiet),
        reqstorm.fetch_all([server + "/status/500"], log_level="INFO", log_file=loud),
    )
    assert quiet.getvalue() == "" and "failed after 1 attempt" in loud.getvalue()


async def test_json_log_lines(server):
    output = io.StringIO()
    await reqstorm.fetch_all([server + "/flaky?key=json&fail=1"], retries=1, backoff=0, log_level="INFO",
                             log_file=output, log_format="json")  # fmt: skip
    events = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [event["event"] for event in events] == ["start", "retry", "finish"]
    retry = events[1]
    assert retry["level"] == "WARNING" and retry["reason"] == "HTTP 503" and retry["attempt"] == 1
    assert events[2]["ok"] == 1 and events[2]["retries"] == 1


async def test_debug_shows_the_proxy_without_its_password(server, second_server):
    output = io.StringIO()
    proxy = second_server.replace("http://", "http://user:secret@")
    await reqstorm.fetch_all(["http://api.example.test/proxy-check"], proxy=proxy, log_level="DEBUG",
                             log_file=output)  # fmt: skip
    text = output.getvalue()
    assert "via http://user:***@127.0.0.1" in text and "secret" not in text


async def test_token_refresh_and_pause_are_logged(server):
    output = io.StringIO()
    auth = reqstorm.BearerAuth("expired", refresh=lambda: "token-1")
    urls = [server + "/protected", server + "/limited?key=logpause&fail=1&retry_after=0.1"]
    await reqstorm.fetch_all(urls, auth=auth, rate_limit="auto", log_level="INFO", log_file=output)
    text = output.getvalue()
    assert "token refreshed after a 401 (refresh 1)" in text
    assert "429 Too Many Requests (Retry-After)" in text and "pausing for" in text


async def test_rejected_records_and_rounds_are_logged(server, tmp_path):
    output = io.StringIO()
    schema = {"id": reqstorm.Field("id", int, required=True)}
    body = json.dumps({"items": [{"id": 1}, {"id": "x"}]})
    await reqstorm.fetch_to_file(
        [reqstorm.Request(server + "/json", params={"body": body}), server + "/flaky?key=lr&fail=1"],
        tmp_path / "out.jsonl", schema=schema, explode="items", retry_rounds=1, retry_round_delay=0,
        log_level="INFO", log_file=output,
    )  # fmt: skip
    text = output.getvalue()
    assert "rejected: id: expected an integer" in text
    assert "retry round 1 of 1: sending 1 failed request again" in text


def test_invalid_logging_options():
    with pytest.raises(ValueError, match="log_level"):
        _observe.Run(total=None, log_level="LOUD")
    with pytest.raises(ValueError, match="log_file needs log_level"):
        _observe.Run(total=None, log_file="x.log")
    with pytest.raises(ValueError, match="log_format"):
        _observe.Run(total=None, log_level="INFO", log_format="xml")


def test_redact():
    assert redact("socks5://ayse:secret@proxy:1080") == "socks5://ayse:***@proxy:1080"
    assert redact("http://proxy:8080") == "http://proxy:8080" and redact(None) == ""


# -- log files ----------------------------------------------------------------------------


def test_log_file_names_never_reuse_a_file(tmp_path):
    stamp = 1_791_000_000.25
    assert os.path.basename(log_path(tmp_path, stamp)).startswith("reqstorm-")
    assert log_path(str(tmp_path / "new-dir") + "/", stamp).endswith(".250.log")
    assert "{time}" not in log_path(tmp_path / "run-{time}.log", stamp)
    names = []
    for _ in range(3):
        path, handle = _open_new(str(tmp_path / "run.log"))
        handle.close()
        names.append(os.path.basename(path))
    assert names == ["run.log", "run-2.log", "run-3.log"]


async def test_summary_reports_the_log_file(server, tmp_path):
    (tmp_path / "logs").mkdir()
    first = await reqstorm.fetch_to_file([server + "/ok"], tmp_path / "a.jsonl", log_level="INFO",
                                         log_file=tmp_path / "logs" / "run.log")  # fmt: skip
    second = await reqstorm.fetch_to_file([server + "/ok"], tmp_path / "b.jsonl", log_level="INFO",
                                          log_file=tmp_path / "logs" / "run.log")  # fmt: skip
    assert os.path.basename(first.log_file) == "run.log" and os.path.basename(second.log_file) == "run-2.log"
    assert "finished 1 requests" in Path(second.log_file).read_text(encoding="utf-8")  # noqa: ASYNC240


# -- writing while running ----------------------------------------------------------------


async def _wait_for(condition, timeout=2.5):
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "condition not met in time"
        await asyncio.sleep(0.05)


def _lines(path):
    return path.read_text().splitlines() if path.exists() else []


async def test_waiting_records_are_written_without_new_results(server, tmp_path):
    path = tmp_path / "live.jsonl"
    urls = [server + "/ok", server + "/slow?delay=4"]
    task = asyncio.ensure_future(reqstorm.fetch_to_file(urls, path, concurrency=2, flush_interval=0.2))
    await _wait_for(lambda: len(_lines(path)) == 1)  # written while /slow is still running
    assert not task.done()
    assert json.loads(_lines(path)[0])["url"].endswith("/ok")
    await task
    assert len(_lines(path)) == 2


async def test_sqlite_output_can_be_read_while_written(server, tmp_path):
    path = tmp_path / "live.db"
    urls = [server + "/ok"] * 3 + [server + "/slow?delay=4"]
    task = asyncio.ensure_future(reqstorm.fetch_to_file(urls, path, flush_interval=0.1))

    def rows():
        if not path.exists():
            return 0
        with closing(sqlite3.connect(path, timeout=0.5)) as reader:
            try:
                return reader.execute("SELECT count(*) FROM reqstorm_results").fetchone()[0]
            except sqlite3.OperationalError:  # the table is not created yet
                return 0

    await _wait_for(lambda: rows() == 3)  # readable while the run is still writing
    assert not task.done()
    with closing(sqlite3.connect(path)) as reader:
        assert reader.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    await task


async def test_flush_interval_zero_and_negative(server, tmp_path):
    summary = await reqstorm.fetch_to_file([server + "/ok"] * 3, tmp_path / "now.jsonl", flush_interval=0)
    assert summary.ok == 3 and len((tmp_path / "now.jsonl").read_text().splitlines()) == 3
    with pytest.raises(ValueError, match="flush_interval"):
        await reqstorm.fetch_to_file([server + "/ok"], tmp_path / "x.jsonl", flush_interval=-1)
