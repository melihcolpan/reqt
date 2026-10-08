import io
import json
import sqlite3
import subprocess
import sys

import pytest

from reqstorm._cli import _paginator, main
from reqstorm._paginate import Cursor, LinkHeader, NextLink, PageNumber


def _lines(text):
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_urls_to_stdout(thread_server, tmp_path, capsys):
    urls = tmp_path / "urls.txt"
    urls.write_text(f"# a comment\n{thread_server}/ok\n\n{thread_server}/status/404\n")
    assert main([str(urls), "-q"]) == 1
    records = _lines(capsys.readouterr().out)
    assert sorted((r["url"].rsplit("/", 1)[1], r["status"]) for r in records) == [("404", 404), ("ok", 200)]


def test_stdin_input(thread_server, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(f"{thread_server}/ok\n"))
    assert main(["-q", "--body", "text"]) == 0
    assert _lines(capsys.readouterr().out)[0]["body"] == "ok"


def test_output_file_with_rate_retries_and_report(thread_server, tmp_path, capsys):
    urls = tmp_path / "urls.txt"
    urls.write_text(f"{thread_server}/flaky?key=cli&fail=1\n{thread_server}/ok\n")
    target = tmp_path / "out.jsonl"
    code = main([str(urls), "-o", str(target), "--rate", "100/s", "--retries", "2", "--backoff", "0", "-q",
                 "--report"])  # fmt: skip
    assert code == 0
    assert all(r["status"] == 200 for r in _lines(target.read_text()))
    err = capsys.readouterr().err
    assert "2 ok, 0 failed" in err and '"p95"' in err and '"retries": 1' in err


def test_template_csv_to_sqlite_with_schema_file(thread_server, tmp_path, capsys):
    rows = tmp_path / "keys.csv"
    rows.write_text("id\n1\n2\n")
    schema = tmp_path / "schema.json"
    key = {"path": "query.id", "type": "int", "coerce": True, "key": True}
    schema.write_text(json.dumps({"fields": {"key": key, "method": "method"}}))
    target = tmp_path / "out.db"
    code = main([str(rows), "--template", thread_server + "/echo?id={id}", "--schema", str(schema),
                 "-o", str(target), "--table", "echoes", "-q"])  # fmt: skip
    assert code == 0
    with sqlite3.connect(target) as connection:
        assert connection.execute("SELECT key, method FROM echoes ORDER BY key").fetchall() == [
            (1, '"GET"'), (2, '"GET"')
        ]  # fmt: skip


def test_pagination_from_the_command_line(thread_server, tmp_path, capsys):
    urls = tmp_path / "urls.txt"
    urls.write_text(f"{thread_server}/pages/next\n")
    assert main([str(urls), "--paginate", "next:links.next", "-q", "--body", "text"]) == 0
    pages = _lines(capsys.readouterr().out)
    assert sorted(r["page"] for r in pages) == [0, 1, 2]


def test_estimate(tmp_path, capsys):
    urls = tmp_path / "urls.txt"
    urls.write_text("\n".join(f"https://api.example.com/{n}" for n in range(7000)))
    assert main([str(urls), "--estimate", "--rate", "100/min"]) == 0
    assert "7000 requests: about 1h 10m" in capsys.readouterr().out


def test_infer_schema_prints_a_schema_file(thread_server, tmp_path, capsys):
    body = json.dumps({"items": [{"id": 1, "name": "a"}]})
    urls = tmp_path / "urls.txt"
    urls.write_text(f"{thread_server}/json?body={body}\n")
    assert main([str(urls), "--infer-schema", "1", "--explode", "items", "-q"]) == 0
    drafted = json.loads(capsys.readouterr().out)
    assert drafted["explode"] == "items"
    assert drafted["fields"]["id"] == {"path": "id", "type": "int", "required": True}


def test_bearer_token_from_the_environment(thread_server, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("REQSTORM_TOKEN", "token-1")
    urls = tmp_path / "urls.txt"
    urls.write_text(f"{thread_server}/protected\n")
    assert main([str(urls), "-q", "--body", "text"]) == 0
    assert _lines(capsys.readouterr().out)[0]["body"] == "secret"


@pytest.mark.parametrize(
    "args, message",
    [
        (["--rate", "fast"], "rate"),
        (["--paginate", "sideways"], "paginate"),
        (["-H", "no colon"], "header"),
        (["--schema", "missing.json", "-o", "x.jsonl"], "missing.json"),
        (["--retry-rounds", "1"], "need --output"),
    ],
)
def test_invalid_arguments_exit_with_2(tmp_path, capsys, args, message):
    urls = tmp_path / "urls.txt"
    urls.write_text("https://api.example.com/\n")
    assert main([str(urls), *args]) == 2
    assert message in capsys.readouterr().err


def test_paginator_specs():
    assert _paginator("next:links.next", 10) == NextLink("links.next", max_pages=10)
    assert _paginator("link", 10) == LinkHeader(max_pages=10)
    assert _paginator("cursor:meta.next:after", 10) == Cursor("meta.next", param="after", max_pages=10)
    assert _paginator("page:p:data", 10) == PageNumber("p", items="data", max_pages=10)
    assert _paginator(None, 10) is None


def test_console_script_runs():
    output = subprocess.run([sys.executable, "-m", "reqstorm", "--version"], capture_output=True, text=True,
                            check=True)  # fmt: skip
    assert output.stdout.startswith("reqstorm 2.")
