import json

import reqstorm
from reqstorm._report import Report, _percentile


def test_percentiles_use_the_nearest_rank():
    values = [float(n) for n in range(1, 101)]
    assert (
        _percentile(values, 0.5) == 50 and _percentile(values, 0.95) == 95 and _percentile(values, 0.99) == 99
    )
    assert _percentile([], 0.5) == 0.0 and _percentile([3.0], 0.99) == 3.0


async def test_report_breaks_down_statuses_errors_and_hosts(server, second_server):
    urls = [server + "/ok"] * 3 + [
        server + "/status/404",
        second_server + "/status/500",
        "http://127.0.0.1:9/",
    ]
    results = await reqstorm.fetch_all(urls, timeout=2)
    report = results.report()
    assert report["total"] == 6 and report["ok"] == 3 and report["failed"] == 3
    assert report["statuses"] == {200: 3, 404: 1, 500: 1}
    assert sum(report["errors"].values()) == 1
    host = server.split("//")[1]
    assert report["hosts"][host]["requests"] == 4 and report["hosts"][host]["failed"] == 1
    latency = report["latency"]
    assert 0 <= latency["min"] <= latency["p50"] <= latency["p95"] <= latency["p99"] <= latency["max"]
    json.dumps(report)


async def test_retries_are_counted(server):
    results = await reqstorm.fetch_all([server + "/flaky?key=report&fail=2"], retries=2, backoff=0)
    assert results.report()["attempts"] == 3 and results.report()["retries"] == 2


async def test_file_summary_carries_the_report(server, tmp_path):
    summary = await reqstorm.fetch_to_file([server + "/ok", server + "/status/503"], tmp_path / "out.jsonl")
    assert summary.report["statuses"] == {200: 1, 503: 1}
    assert summary.report["total"] == 2


def test_empty_report():
    report = Report().as_dict()
    assert report["total"] == 0 and report["latency"]["p99"] == 0.0 and report["hosts"] == {}


async def test_summary_includes_latency_and_hosts(server):
    summary = (await reqstorm.fetch_all([server + "/ok", "not a url"])).summary()
    assert set(summary) >= {"total", "ok", "failed", "failures", "latency", "statuses", "hosts"}
    assert summary["statuses"] == {200: 1} and "invalid" in summary["hosts"]
