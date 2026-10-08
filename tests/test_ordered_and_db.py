import csv
import json
import os
import sqlite3

import pytest

import reqstorm


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


async def test_ordered_output(server, tmp_path):
    path = tmp_path / "out.jsonl"
    urls = [f"{server}/slow?delay=0.3", f"{server}/ok", f"{server}/slow?delay=0.1", f"{server}/ok"]
    await reqstorm.fetch_to_file(urls, path, ordered=True, batch_size=1)
    assert [r["index"] for r in read_jsonl(path)] == [0, 1, 2, 3]


async def test_unordered_output_is_completion_order(server, tmp_path):
    path = tmp_path / "out.jsonl"
    await reqstorm.fetch_to_file([f"{server}/slow?delay=0.3", f"{server}/ok"], path, batch_size=1)
    assert [r["index"] for r in read_jsonl(path)] == [1, 0]


async def test_ordered_with_resume_keeps_input_positions(server, tmp_path):
    path = tmp_path / "out.csv"
    urls = [f"{server}/ok", f"{server}/flaky?key=ord&fail=1", f"{server}/echo"]
    await reqstorm.fetch_to_file(urls, path, ordered=True)
    await reqstorm.fetch_to_file(urls, path, ordered=True, resume=True)
    with path.open() as file:
        rows = list(csv.DictReader(file))
    assert [r["index"] for r in rows] == ["0", "1", "2", "1"] and rows[-1]["ok"] == "True"


async def test_file_retry_rounds_write_one_record_per_request(server, tmp_path):
    path = tmp_path / "out.jsonl"
    summary = await reqstorm.fetch_to_file(
        [f"{server}/flaky?key=frr&fail=1", f"{server}/ok"], path, retry_rounds=1, retry_round_delay=0
    )
    records = read_jsonl(path)
    assert len(records) == 2 and all(r["ok"] for r in records) and summary.failed == 0
    assert max(r["attempts"] for r in records) == 2


async def test_summary_errors(server, tmp_path):
    summary = await reqstorm.fetch_to_file([f"{server}/status/500", f"{server}/ok"], tmp_path / "o.jsonl")
    assert [e["status"] for e in summary.errors] == [500] and "body" not in summary.errors[0]


async def test_callback_is_rejected(tmp_path):
    with pytest.raises(TypeError, match="callback"):
        await reqstorm.fetch_to_file([], tmp_path / "o.jsonl", callback=print)


# --- Databases ---------------------------------------------------------------------------


def rows(connection, table="reqstorm_results"):
    cursor = connection.cursor()
    cursor.execute(
        f"SELECT request_index, method, url, status, ok, attempts, history, body FROM {table} ORDER BY id"
    )
    fetched = cursor.fetchall()
    cursor.close()
    return fetched


async def check_database(connection, server, binary_type):
    urls = [f"{server}/ok", f"{server}/flaky?key=db-{binary_type.__name__}&fail=1", f"{server}/latin1"]
    summary = await reqstorm.fetch_to_db(urls, connection, table="reqstorm_test", retries=0)
    assert (summary.ok, summary.failed) == (2, 1)
    first = sorted(rows(connection, "reqstorm_test"), key=lambda row: row[0])
    assert [row[3] for row in first] == [200, 503, 200]
    assert [bool(row[4]) for row in first] == [True, False, True]
    history = first[1][6] if not isinstance(first[1][6], str) else json.loads(first[1][6])
    assert history[0]["status"] == 503
    assert first[2][7] == "café"

    resumed = await reqstorm.fetch_to_db(urls, connection, table="reqstorm_test", resume=True)
    assert (resumed.skipped, resumed.ok) == (2, 1)
    assert len(rows(connection, "reqstorm_test")) == 4

    await reqstorm.fetch_to_db([f"{server}/latin1"], connection, table="reqstorm_bytes", body="bytes")
    stored = rows(connection, "reqstorm_bytes")[0][7]
    assert bytes(stored) == "café".encode("latin-1")


async def test_sqlite_connection(server, tmp_path):
    connection = sqlite3.connect(tmp_path / "results.db")
    try:
        await check_database(connection, server, bytes)
    finally:
        connection.close()


async def test_sqlite_path(server, tmp_path):
    path = tmp_path / "results.sqlite"
    summary = await reqstorm.fetch_to_file([f"{server}/ok", f"{server}/status/404"], path, ordered=True)
    assert summary.target == str(path) and (summary.ok, summary.failed) == (1, 1)
    connection = sqlite3.connect(path)
    assert [(row[0], row[3]) for row in rows(connection)] == [(0, 200), (1, 404)]
    connection.close()


async def test_invalid_table_name(server, tmp_path):
    connection = sqlite3.connect(tmp_path / "x.db")
    with pytest.raises(ValueError, match="table"):
        await reqstorm.fetch_to_db([f"{server}/ok"], connection, table="results; DROP TABLE x")
    connection.close()


async def test_unsupported_connection(server):
    with pytest.raises(TypeError, match="unsupported database"):
        await reqstorm.fetch_to_db([f"{server}/ok"], object())


@pytest.mark.skipif(
    not os.environ.get("REQSTORM_TEST_POSTGRES"), reason="set REQSTORM_TEST_POSTGRES to a connection string"
)
async def test_postgresql(server):
    psycopg = pytest.importorskip("psycopg")
    connection = psycopg.connect(os.environ["REQSTORM_TEST_POSTGRES"])
    try:
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS reqstorm_test, reqstorm_bytes")
        connection.commit()
        await check_database(connection, server, bytes)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'reqstorm_test' AND column_name = 'history'"
            )
            assert cursor.fetchone()[0] == "jsonb"
    finally:
        connection.close()


@pytest.mark.skipif(
    not os.environ.get("REQSTORM_TEST_MYSQL"), reason="set REQSTORM_TEST_MYSQL to host:port:user:password:db"
)
async def test_mysql(server):
    pymysql = pytest.importorskip("pymysql")
    host, port, user, password, database = os.environ["REQSTORM_TEST_MYSQL"].split(":")
    connection = pymysql.connect(
        host=host, port=int(port), user=user, password=password, database=database, charset="utf8mb4"
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS reqstorm_test, reqstorm_bytes")
        connection.commit()
        await check_database(connection, server, bytes)
    finally:
        connection.close()
