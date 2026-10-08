"""Write results to a JSONL or CSV file, or to a database table, as they complete."""

from __future__ import annotations

import asyncio
import concurrent.futures
import csv
import json as jsonlib
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Iterator, List, Optional, Set, Tuple, Union

from ._client import Request, Result, _execute
from ._progress import Progress, ProgressTarget

__all__ = ["Summary", "fetch_to_db", "fetch_to_file"]

_FILE_FIELDS = [
    "index", "method", "url", "status", "ok", "error", "attempts",
    "elapsed", "final_url", "history", "headers", "body",
]  # fmt: skip
_SQLITE_SUFFIXES = (".db", ".sqlite", ".sqlite3")
_TABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?")


@dataclass
class Summary:
    """What ``fetch_to_file`` or ``fetch_to_db`` did."""

    target: str
    total: int
    ok: int
    failed: int
    skipped: int
    elapsed: float
    errors: List[Dict[str, Any]] = field(default_factory=list)


class _Sink:
    """Where records go. Methods are blocking; they are called from the event loop thread
    unless ``offload`` is True, in which case writes run on a single helper thread."""

    offload = False

    def completed(self) -> Set[Tuple[str, str]]:
        return set()

    def write(self, records: List[Dict[str, Any]]) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class _JsonlSink(_Sink):
    def __init__(self, path: str, append: bool) -> None:
        self.path = path
        needs_newline = append and _missing_final_newline(path)
        self.file = open(path, "a" if append else "w", encoding="utf-8")  # noqa: SIM115
        if needs_newline:
            self.file.write("\n")

    @staticmethod
    def read_completed(path: str) -> Set[Tuple[str, str]]:
        done: Set[Tuple[str, str]] = set()
        if not os.path.exists(path):
            return done
        with open(path, encoding="utf-8") as file:
            for line in file:
                try:
                    record = jsonlib.loads(line)
                except ValueError:  # a line cut short by an interrupted run
                    continue
                if isinstance(record, dict) and record.get("ok"):
                    done.add((record.get("method", "GET"), record.get("url", "")))
        return done

    def write(self, records: List[Dict[str, Any]]) -> None:
        for record in records:
            self.file.write(jsonlib.dumps(record, ensure_ascii=False) + "\n")
        self.file.flush()

    def close(self) -> None:
        self.file.close()


class _CsvSink(_Sink):
    def __init__(self, path: str, append: bool, fields: List[str]) -> None:
        write_header = not append or not os.path.exists(path) or os.path.getsize(path) == 0
        needs_newline = append and not write_header and _missing_final_newline(path)
        self.file = open(path, "a" if append else "w", newline="", encoding="utf-8")  # noqa: SIM115
        if needs_newline:
            self.file.write("\r\n")
        self.writer = csv.DictWriter(self.file, fieldnames=fields, extrasaction="ignore")
        if write_header:
            self.writer.writeheader()

    @staticmethod
    def read_completed(path: str) -> Set[Tuple[str, str]]:
        done: Set[Tuple[str, str]] = set()
        if not os.path.exists(path):
            return done
        with open(path, newline="", encoding="utf-8") as file:
            for row in csv.DictReader(file):
                if row.get("ok") == "True":
                    done.add((row.get("method") or "GET", row.get("url") or ""))
        return done

    def write(self, records: List[Dict[str, Any]]) -> None:
        for record in records:
            row = dict(record)
            for key in ("history", "headers"):
                if key in row:
                    row[key] = jsonlib.dumps(row[key], ensure_ascii=False)
            self.writer.writerow(row)
        self.file.flush()

    def close(self) -> None:
        self.file.close()


@dataclass(frozen=True)
class _Dialect:
    name: str
    placeholder: str
    id_column: str
    integer: str
    real: str
    boolean: str
    text: str
    long_text: str
    blob: str
    json: str
    json_placeholder: str
    created_at: str


_SQLITE = _Dialect(
    "sqlite", "?", "id INTEGER PRIMARY KEY AUTOINCREMENT", "INTEGER", "REAL", "INTEGER", "TEXT", "TEXT",
    "BLOB", "TEXT", "?", "created_at TEXT DEFAULT CURRENT_TIMESTAMP",
)  # fmt: skip
_POSTGRES = _Dialect(
    "postgresql", "%s", "id BIGSERIAL PRIMARY KEY", "INTEGER", "DOUBLE PRECISION", "BOOLEAN", "TEXT", "TEXT",
    "BYTEA", "JSONB", "%s::jsonb", "created_at TIMESTAMPTZ DEFAULT now()",
)  # fmt: skip
_MYSQL = _Dialect(
    "mysql", "%s", "id BIGINT AUTO_INCREMENT PRIMARY KEY", "INT", "DOUBLE", "BOOLEAN", "VARCHAR(2048)",
    "LONGTEXT", "LONGBLOB", "JSON", "%s", "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
)  # fmt: skip


def _dialect_for(connection: Any) -> _Dialect:
    module = type(connection).__module__.split(".")[0]
    if module == "sqlite3":
        return _SQLITE
    if module in ("psycopg", "psycopg2"):
        return _POSTGRES
    if module in ("pymysql", "MySQLdb", "mysql"):
        return _MYSQL
    raise TypeError(
        f"unsupported database connection {type(connection).__module__}.{type(connection).__name__}; "
        "use sqlite3, psycopg, psycopg2, PyMySQL, mysqlclient or mysql-connector-python"
    )


class _DatabaseSink(_Sink):
    """Writes one row per result, with queryable columns and JSON for headers and history."""

    def __init__(self, connection: Any, table: str, body: str, include_headers: bool, close: bool) -> None:
        if not _TABLE_NAME.fullmatch(table):
            raise ValueError(f"invalid table name {table!r}")
        self.connection = connection
        self.table = table
        self.dialect = _dialect_for(connection)
        self.body = body
        self.include_headers = include_headers
        self.close_connection = close
        # sqlite3 connections may only be used from the thread that created them
        self.offload = self.dialect is not _SQLITE
        self.columns = [
            "request_index", "method", "url", "status", "ok",
            "error", "attempts", "elapsed", "final_url", "history",
        ]  # fmt: skip
        if include_headers:
            self.columns.append("headers")
        if body != "none":
            self.columns.append("body")
        self._create_table()

    def _create_table(self) -> None:
        d = self.dialect
        body_type = d.blob if self.body == "bytes" else d.long_text
        definitions = [
            d.id_column,
            f"request_index {d.integer}",
            f"method {d.text} NOT NULL",
            f"url {d.long_text if d is not _MYSQL else d.text} NOT NULL",
            f"status {d.integer}",
            f"ok {d.boolean} NOT NULL",
            f"error {d.long_text}",
            f"attempts {d.integer} NOT NULL",
            f"elapsed {d.real}",
            f"final_url {d.long_text}",
            f"history {d.json}",
            f"headers {d.json}",
            f"body {body_type}",
            d.created_at,
        ]
        cursor = self.connection.cursor()
        try:
            cursor.execute(f"CREATE TABLE IF NOT EXISTS {self.table} ({', '.join(definitions)})")
        finally:
            cursor.close()
        self.connection.commit()

    def completed(self) -> Set[Tuple[str, str]]:
        cursor = self.connection.cursor()
        try:
            cursor.execute(
                f"SELECT method, url FROM {self.table} WHERE ok = {self.dialect.placeholder}", (True,)
            )
            done = {(str(method), str(url)) for method, url in cursor.fetchall()}
        finally:
            cursor.close()
        self.connection.commit()
        return done

    def write(self, records: List[Dict[str, Any]]) -> None:
        d = self.dialect
        placeholders = [
            d.json_placeholder if c in ("history", "headers") else d.placeholder for c in self.columns
        ]
        sql = f"INSERT INTO {self.table} ({', '.join(self.columns)}) VALUES ({', '.join(placeholders)})"
        rows = []
        for record in records:
            values = {**record, "request_index": record["index"], "history": jsonlib.dumps(record["history"])}
            if "headers" in record:
                values["headers"] = jsonlib.dumps(record["headers"])
            rows.append(tuple(values.get(column) for column in self.columns))
        cursor = self.connection.cursor()
        try:
            cursor.executemany(sql, rows)
        finally:
            cursor.close()
        self.connection.commit()

    def close(self) -> None:
        if self.close_connection:
            self.connection.close()


def _missing_final_newline(path: str) -> bool:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return False
    with open(path, "rb") as file:
        file.seek(-1, os.SEEK_END)
        return file.read(1) != b"\n"


class _Writer:
    """Batches records, keeps them in input order if asked, and runs blocking writes off the loop."""

    def __init__(self, sink: _Sink, ordered: bool, batch_size: int, flush_interval: float = 1.0) -> None:
        self.sink = sink
        self.ordered = ordered
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.batch: List[Dict[str, Any]] = []
        self.waiting: Dict[int, Dict[str, Any]] = {}
        self.next_index = 0
        self.last_flush = time.monotonic()
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=1) if sink.offload else None

    async def add(self, position: int, record: Dict[str, Any]) -> None:
        if self.ordered:
            self.waiting[position] = record
            while self.next_index in self.waiting:
                self.batch.append(self.waiting.pop(self.next_index))
                self.next_index += 1
        else:
            self.batch.append(record)
        if len(self.batch) >= self.batch_size or time.monotonic() - self.last_flush >= self.flush_interval:
            await self.flush()

    async def flush(self) -> None:
        if not self.batch:
            return
        batch, self.batch = self.batch, []
        self.last_flush = time.monotonic()
        if self.executor is not None:
            await asyncio.get_running_loop().run_in_executor(self.executor, self.sink.write, batch)
        else:
            self.sink.write(batch)

    async def close(self) -> None:
        try:
            await self.flush()
        finally:
            if self.executor is not None:
                await asyncio.get_running_loop().run_in_executor(self.executor, self.sink.close)
                self.executor.shutdown()
            else:
                self.sink.close()


async def _run_to_sink(
    urls: Iterable[Union[str, Request]],
    make_sink: Any,
    read_completed: Any,
    target: str,
    *,
    body: str,
    include_headers: bool,
    resume: bool,
    ordered: bool,
    progress: ProgressTarget,
    retry_rounds: int,
    retry_round_delay: float,
    batch_size: int,
    options: Dict[str, Any],
) -> Summary:
    if body not in ("text", "base64", "none", "bytes"):
        raise ValueError("body must be 'text', 'base64', 'bytes' or 'none'")
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    if "callback" in options:
        raise TypeError(
            "fetch_to_file() and fetch_to_db() do not accept `callback`; use fetch_all() or stream()"
        )
    default_method = str(options.get("method", "GET")).upper()

    sink: _Sink = make_sink()
    try:
        done = (sink.completed() or read_completed()) if resume else set()
    except BaseException:
        sink.close()
        raise
    skipped = 0
    positions: List[int] = []  # index in `urls` of each request actually sent

    def pending() -> Iterator[Union[str, Request]]:
        nonlocal skipped
        for position, request in enumerate(urls):
            url = request if isinstance(request, str) else request.url
            method = (
                default_method if isinstance(request, str) else (request.method or default_method).upper()
            )
            if (method, url) in done:
                skipped += 1
                continue
            positions.append(position)
            yield request

    total: Optional[int] = None
    try:
        total = len(urls)  # type: ignore[arg-type]
    except TypeError:
        pass
    tracker = Progress.create(
        progress, total=None if total is None else total - len(done) if resume else total
    )
    started = time.monotonic()
    ok = failed = 0
    errors: List[Dict[str, Any]] = []
    writer = _Writer(sink, ordered=ordered, batch_size=batch_size)
    try:
        async for result in _execute(pending(), retry_rounds, retry_round_delay, options):
            record = _record(result, positions[result.index], body, include_headers)
            await writer.add(result.index, record)
            if result.ok:
                ok += 1
            else:
                failed += 1
                errors.append({key: value for key, value in record.items() if key != "body"})
            if tracker is not None:
                tracker.update(result)
    finally:
        await writer.close()
    if tracker is not None:
        tracker.close()
    return Summary(
        target=target,
        total=ok + failed,
        ok=ok,
        failed=failed,
        skipped=skipped,
        elapsed=time.monotonic() - started,
        errors=errors,
    )


def _record(result: Result, position: int, body: str, include_headers: bool) -> Dict[str, Any]:
    record = result.to_dict(body="none" if body == "bytes" else body, include_headers=include_headers)
    record["index"] = position
    if body == "bytes":
        record["body"] = result.body
    return record


async def fetch_to_file(
    urls: Iterable[Union[str, Request]],
    path: Union[str, os.PathLike],
    *,
    format: Optional[str] = None,
    body: str = "text",
    include_headers: bool = False,
    resume: bool = False,
    ordered: bool = False,
    progress: ProgressTarget = False,
    retry_rounds: int = 0,
    retry_round_delay: float = 5.0,
    table: str = "reqt_results",
    batch_size: int = 100,
    **options: Any,
) -> Summary:
    """Send the requests and write one record per request to ``path`` as soon as it is final.

    Results are not collected in memory, so this suits very large batches.

    Args:
        path: ``.csv`` is written as CSV; ``.db``, ``.sqlite`` and ``.sqlite3`` as an SQLite
            database (see ``fetch_to_db``); anything else as JSON Lines.
        format: ``"jsonl"``, ``"csv"`` or ``"sqlite"`` to override the choice made from the name.
        body: ``"text"`` (decoded with the response charset), ``"base64"``, ``"bytes"``
            (SQLite only) or ``"none"`` to leave it out.
        include_headers: Also write the response headers.
        resume: Skip the requests already recorded as successful in an existing file or
            table; the rest (including earlier failures) are sent and appended.
            Without it a JSONL or CSV file is overwritten; database rows are never deleted.
        ordered: Write records in input order. By default they are written as they complete.
            Ordered output holds back records that finish early until the ones before them
            are done, so it uses more memory when a few requests are slow.
        progress: ``True`` to print progress to stderr, or a text stream to print it to.
        retry_rounds, retry_round_delay: As for ``fetch_all``; each request still gets one record.
        table: Table name for SQLite output.
        batch_size: Records written per batch.
        options: Any other option of ``fetch_all``, such as ``method``, ``concurrency``,
            ``timeout``, ``retries`` or ``rate_limit``.

    Each record has ``index`` (position in ``urls``), ``method``, ``url``, ``status``, ``ok``,
    ``error``, ``attempts``, ``elapsed``, ``final_url`` and ``history`` (every attempt).
    Returns a ``Summary`` whose ``errors`` lists the failed records.
    """
    path = os.fspath(path)
    if format is None:
        lowered = path.lower()
        format = (
            "csv" if lowered.endswith(".csv") else "sqlite" if lowered.endswith(_SQLITE_SUFFIXES) else "jsonl"
        )
    if format not in ("jsonl", "csv", "sqlite"):
        raise ValueError("format must be 'jsonl', 'csv' or 'sqlite'")
    if body == "bytes" and format != "sqlite":
        raise ValueError("body='bytes' needs a database; use 'text' or 'base64' for files")

    if format == "sqlite":

        def make_sink() -> _Sink:
            connection = sqlite3.connect(path, check_same_thread=False)
            return _DatabaseSink(connection, table, body, include_headers, close=True)

        read_completed: Any = set
    elif format == "csv":
        fields = [
            f for f in _FILE_FIELDS if (f != "headers" or include_headers) and (f != "body" or body != "none")
        ]

        def make_sink() -> _Sink:
            return _CsvSink(path, append=resume, fields=fields)

        def read_completed() -> Set[Tuple[str, str]]:
            return _CsvSink.read_completed(path)
    else:

        def make_sink() -> _Sink:
            return _JsonlSink(path, append=resume)

        def read_completed() -> Set[Tuple[str, str]]:
            return _JsonlSink.read_completed(path)

    return await _run_to_sink(
        urls,
        make_sink,
        read_completed,
        path,
        body=body,
        include_headers=include_headers,
        resume=resume,
        ordered=ordered,
        progress=progress,
        retry_rounds=retry_rounds,
        retry_round_delay=retry_round_delay,
        batch_size=batch_size,
        options=options,
    )


async def fetch_to_db(
    urls: Iterable[Union[str, Request]],
    connection: Any,
    *,
    table: str = "reqt_results",
    body: str = "text",
    include_headers: bool = False,
    resume: bool = False,
    ordered: bool = False,
    progress: ProgressTarget = False,
    retry_rounds: int = 0,
    retry_round_delay: float = 5.0,
    batch_size: int = 100,
    **options: Any,
) -> Summary:
    """Send the requests and insert one row per request into ``table``.

    ``connection`` is an open DB-API connection from ``sqlite3``, ``psycopg`` / ``psycopg2``
    (PostgreSQL), or ``pymysql`` / ``MySQLdb`` / ``mysql.connector`` (MySQL). reqt does not
    close it. The table is created if it does not exist, with one column per field
    (``request_index``, ``method``, ``url``, ``status``, ``ok``, ``error``, ``attempts``,
    ``elapsed``, ``final_url``), JSON columns for ``history`` and ``headers``, ``body``,
    and ``created_at``. Rows are inserted in batches on a background thread, except for
    sqlite3 connections, which must stay on the thread that created them.

    Existing rows are never deleted. With ``resume=True``, requests that already have a
    successful row are skipped. ``body="bytes"`` stores the raw body as a binary column.
    The other arguments are as for ``fetch_to_file``.
    """

    def make_sink() -> _Sink:
        return _DatabaseSink(connection, table, body, include_headers, close=False)

    return await _run_to_sink(
        urls,
        make_sink,
        set,
        f"{_dialect_for(connection).name}:{table}",
        body=body,
        include_headers=include_headers,
        resume=resume,
        ordered=ordered,
        progress=progress,
        retry_rounds=retry_rounds,
        retry_round_delay=retry_round_delay,
        batch_size=batch_size,
        options=options,
    )
