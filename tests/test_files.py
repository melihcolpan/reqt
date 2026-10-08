import base64
import csv
import io
import json

import pytest

import reqt


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


async def test_writes_jsonl(server, tmp_path):
    path = tmp_path / "out.jsonl"
    summary = await reqt.fetch_to_file([f"{server}/ok", "not a url", f"{server}/status/500"], path)
    records = sorted(read_jsonl(path), key=lambda r: r["index"])
    assert [r["ok"] for r in records] == [True, False, False]
    assert records[0]["body"] == "ok" and records[0]["status"] == 200 and records[0]["method"] == "GET"
    assert records[1]["status"] is None and records[1]["error"].startswith("InvalidUrl")
    assert records[2]["status"] == 500
    assert (summary.total, summary.ok, summary.failed, summary.skipped) == (3, 1, 2, 0)


async def test_writes_csv(server, tmp_path):
    path = tmp_path / "out.csv"
    await reqt.fetch_to_file([f"{server}/ok", f"{server}/status/404"], path, include_headers=True)
    with path.open() as file:
        rows = sorted(csv.DictReader(file), key=lambda r: int(r["index"]))
    assert [r["status"] for r in rows] == ["200", "404"]
    assert rows[0]["body"] == "ok" and "Content-Type" in json.loads(rows[0]["headers"])


async def test_body_modes(server, tmp_path):
    path = tmp_path / "out.jsonl"
    await reqt.fetch_to_file([f"{server}/latin1"], path, body="base64")
    assert base64.b64decode(read_jsonl(path)[0]["body"]) == "café".encode("latin-1")
    await reqt.fetch_to_file([f"{server}/ok"], path, body="none")
    assert "body" not in read_jsonl(path)[0]


async def test_resume_skips_successes_and_retries_failures(server, tmp_path):
    path = tmp_path / "out.jsonl"
    urls = [f"{server}/ok", f"{server}/flaky?key=resume&fail=1", f"{server}/echo"]
    first = await reqt.fetch_to_file(urls, path)
    assert (first.ok, first.failed) == (2, 1)

    second = await reqt.fetch_to_file(urls, path, resume=True)
    assert (second.total, second.ok, second.skipped) == (1, 1, 2)
    records = read_jsonl(path)
    assert len(records) == 4
    assert records[-1]["index"] == 1 and records[-1]["ok"]


async def test_resume_after_an_interrupted_write(server, tmp_path):
    path = tmp_path / "out.jsonl"
    await reqt.fetch_to_file([f"{server}/ok"], path)
    with path.open("a") as file:
        file.write('{"index": 1, "method": "GET", "url": "cut sh')  # no newline: interrupted run
    summary = await reqt.fetch_to_file([f"{server}/ok", f"{server}/echo"], path, resume=True)
    assert (summary.total, summary.skipped) == (1, 1)
    lines = path.read_text().splitlines()
    assert json.loads(lines[-1])["url"].endswith("/echo")


async def test_resume_csv(server, tmp_path):
    path = tmp_path / "out.csv"
    urls = [f"{server}/ok", f"{server}/flaky?key=resume-csv&fail=1"]
    await reqt.fetch_to_file(urls, path)
    summary = await reqt.fetch_to_file(urls, path, resume=True)
    with path.open() as file:
        rows = list(csv.DictReader(file))
    assert summary.skipped == 1 and len(rows) == 3 and rows[-1]["ok"] == "True"


async def test_resume_uses_the_method(server, tmp_path):
    path = tmp_path / "out.jsonl"
    await reqt.fetch_to_file([f"{server}/echo"], path)
    summary = await reqt.fetch_to_file([reqt.Request(f"{server}/echo", method="POST")], path, resume=True)
    assert summary.skipped == 0 and summary.ok == 1


async def test_without_resume_the_file_is_replaced(server, tmp_path):
    path = tmp_path / "out.jsonl"
    await reqt.fetch_to_file([f"{server}/ok"] * 3, path)
    await reqt.fetch_to_file([f"{server}/ok"], path)
    assert len(read_jsonl(path)) == 1


async def test_progress_and_options(server, tmp_path):
    output = io.StringIO()
    path = tmp_path / "out.jsonl"
    summary = await reqt.fetch_to_file(
        [f"{server}/echo"] * 3, path, method="PUT", concurrency=1, progress=output
    )
    assert {r["method"] for r in read_jsonl(path)} == {"PUT"}
    assert summary.ok == 3 and "3/3 (100%)" in output.getvalue()


def test_fetch_to_file_sync(thread_server, tmp_path):
    path = tmp_path / "out.jsonl"
    summary = reqt.fetch_to_file_sync([f"{thread_server}/ok"] * 2, path)
    assert summary.ok == 2 and len(read_jsonl(path)) == 2


async def test_invalid_arguments(tmp_path):
    with pytest.raises(ValueError):
        await reqt.fetch_to_file([], tmp_path / "x.jsonl", body="raw")
    with pytest.raises(ValueError):
        await reqt.fetch_to_file([], tmp_path / "x.jsonl", format="xml")
