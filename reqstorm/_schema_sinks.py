"""Sinks that write schema rows to typed database columns, JSON Lines or CSV."""

from __future__ import annotations

import csv
import json as jsonlib
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from ._files import (
    _MYSQL,
    _SQLITE,
    _TABLE_NAME,
    _Dialect,
    _dialect_for,
    _missing_final_newline,
    _Sink,
)
from ._schema import Schema, SchemaError

SOURCE_COLUMNS = ["source_index", "source_method", "source_url"]
_MAX_REJECTED_RECORD = 100_000  # characters of a rejected record kept in the rejects table


def item_rows(item: Dict[str, Any], include_source: bool) -> List[Dict[str, Any]]:
    if not include_source:
        return item["rows"]
    source = {"source_index": item["position"], "source_method": item["method"], "source_url": item["url"]}
    return [{**row, **source} for row in item["rows"]]


def _record_text(record: Any) -> Optional[str]:
    if record is None:
        return None
    # allow_nan: a rejected record may contain the NaN that got it rejected
    text = jsonlib.dumps(record, ensure_ascii=False, default=str, allow_nan=True)
    return text if len(text) <= _MAX_REJECTED_RECORD else text[:_MAX_REJECTED_RECORD] + "..."


class SchemaDatabaseSink(_Sink):
    """One row per record, one typed column per schema field, upserts on key fields."""

    _TYPES = {
        "sqlite": {"int": "INTEGER", "float": "REAL", "str": "TEXT", "bool": "INTEGER",
                   "datetime": "TEXT", "json": "TEXT"},
        "postgresql": {"int": "BIGINT", "float": "DOUBLE PRECISION", "str": "TEXT", "bool": "BOOLEAN",
                       "datetime": "TIMESTAMPTZ", "json": "JSONB"},
        "mysql": {"int": "BIGINT", "float": "DOUBLE", "str": "LONGTEXT", "bool": "BOOLEAN",
                  "datetime": "DATETIME(6)", "json": "JSON"},
    }  # fmt: skip

    def __init__(
        self,
        connection: Any,
        table: str,
        schema: Schema,
        *,
        rejects_table: Optional[str],
        include_source: bool,
        close: bool,
    ) -> None:
        for name in (table, rejects_table):
            if name is not None and not _TABLE_NAME.fullmatch(name):
                raise ValueError(f"invalid table name {name!r}")
        self.connection = connection
        self.table = table
        self.schema = schema
        self.rejects_table = rejects_table
        self.include_source = include_source
        self.close_connection = close
        self.dialect: _Dialect = _dialect_for(connection)
        self.offload = self.dialect is not _SQLITE
        self.columns = list(schema.fields) + (SOURCE_COLUMNS if include_source else [])
        overlap = set(schema.fields) & set(SOURCE_COLUMNS)
        if include_source and overlap:
            raise SchemaError(f"column names {sorted(overlap)} are reserved for the source columns")
        self._create_tables()

    # -- DDL ---------------------------------------------------------------------------

    def _column_type(self, column: str) -> str:
        if column == "source_index":
            return "BIGINT" if self.dialect is not _SQLITE else "INTEGER"
        if column in ("source_method", "source_url"):
            return self.dialect.text if column == "source_method" else self.dialect.long_text
        spec = self.schema.fields[column]
        kind = spec.type_name
        if self.dialect is _MYSQL and spec.key and kind in ("str", "json"):
            return "VARCHAR(255)"  # MySQL cannot index TEXT columns in a primary key
        return self._TYPES[self.dialect.name][kind]

    def _execute(self, sql: str, params: Any = None) -> Any:
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, params) if params is not None else cursor.execute(sql)
            return cursor.fetchall() if cursor.description else None
        finally:
            cursor.close()

    def _create_tables(self) -> None:
        d = self.dialect
        keys = self.schema.keys
        definitions = [] if keys else [d.id_column]
        for column in self.columns:
            spec = self.schema.fields.get(column)
            not_null = column in keys or (spec is not None and spec.required)
            definitions.append(f"{column} {self._column_type(column)}" + (" NOT NULL" if not_null else ""))
        definitions.append(d.created_at)
        if keys:
            definitions.append(f"PRIMARY KEY ({', '.join(keys)})")
        self._execute(f"CREATE TABLE IF NOT EXISTS {self.table} ({', '.join(definitions)})")
        self._check_columns(self.table, self.columns)
        if self.rejects_table:
            self._execute(
                f"CREATE TABLE IF NOT EXISTS {self.rejects_table} ({d.id_column}, "
                f"source_index {'INTEGER' if d is _SQLITE else 'BIGINT'}, source_method {d.text}, "
                f"source_url {d.long_text}, reasons {d.long_text}, record {d.long_text}, {d.created_at})"
            )
        self.connection.commit()

    def _check_columns(self, table: str, expected: List[str]) -> None:
        cursor = self.connection.cursor()
        try:
            cursor.execute(f"SELECT * FROM {table} WHERE 1 = 0")
            existing = {str(description[0]).lower() for description in cursor.description}
            cursor.fetchall()
        finally:
            cursor.close()
        missing = [column for column in expected if column.lower() not in existing]
        if missing:
            raise SchemaError(
                f"table {table} already exists without the columns {', '.join(missing)}; "
                "use another table name or add the columns"
            )

    # -- resume ------------------------------------------------------------------------

    def completed(self) -> Set[Tuple[str, str]]:
        if not self.include_source:
            raise ValueError("resume needs the source columns; do not combine it with include_source=False")
        rows = self._execute(f"SELECT DISTINCT source_method, source_url FROM {self.table}") or []
        self.connection.commit()
        return {(str(method), str(url)) for method, url in rows}

    # -- writing -----------------------------------------------------------------------

    def _adapt(self, column: str, value: Any) -> Any:
        if value is None or column in SOURCE_COLUMNS:
            return value
        kind = self.schema.fields[column].type_name
        if kind == "json":
            return jsonlib.dumps(value, ensure_ascii=False)
        if kind == "datetime" and isinstance(value, datetime):
            if self.dialect is _SQLITE:
                return value.isoformat()
            if self.dialect is _MYSQL and value.tzinfo is not None:
                return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def _insert_sql(self) -> str:
        d = self.dialect
        json_columns = {c for c in self.schema.fields if self.schema.fields[c].type_name == "json"}
        placeholders = [d.json_placeholder if c in json_columns else d.placeholder for c in self.columns]
        sql = f"INSERT INTO {self.table} ({', '.join(self.columns)}) VALUES ({', '.join(placeholders)})"
        keys = self.schema.keys
        updates = [column for column in self.columns if column not in keys]
        if keys:
            if d is _MYSQL:
                assignments = ", ".join(f"{c} = VALUES({c})" for c in updates) or f"{keys[0]} = {keys[0]}"
                sql += f" ON DUPLICATE KEY UPDATE {assignments}"
            else:
                action = (
                    "DO UPDATE SET " + ", ".join(f"{c} = excluded.{c}" for c in updates)
                    if updates
                    else "DO NOTHING"
                )
                sql += f" ON CONFLICT ({', '.join(keys)}) {action}"
        return sql

    def write(self, items: List[Dict[str, Any]]) -> None:
        rows = [row for item in items for row in item_rows(item, self.include_source)]
        cursor = self.connection.cursor()
        try:
            if rows:
                cursor.executemany(
                    self._insert_sql(),
                    [tuple(self._adapt(column, row.get(column)) for column in self.columns) for row in rows],
                )
            if self.rejects_table:
                p = self.dialect.placeholder
                rejected = [
                    (item["position"], item["method"], item["url"], jsonlib.dumps(entry["reasons"]),
                     _record_text(entry.get("record")))
                    for item in items
                    for entry in item["rejects"]
                ]  # fmt: skip
                if rejected:
                    cursor.executemany(
                        f"INSERT INTO {self.rejects_table} "
                        "(source_index, source_method, source_url, reasons, record) "
                        f"VALUES ({p}, {p}, {p}, {p}, {p})",
                        rejected,
                    )
        finally:
            cursor.close()
        self.connection.commit()

    def close(self) -> None:
        if self.close_connection:
            self.connection.close()


def _plain(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


class SchemaJsonlSink(_Sink):
    def __init__(self, path: str, append: bool, include_source: bool) -> None:
        self.include_source = include_source
        needs_newline = append and _missing_final_newline(path)
        self.file = open(path, "a" if append else "w", encoding="utf-8")  # noqa: SIM115
        if needs_newline:
            self.file.write("\n")

    @staticmethod
    def read_completed(path: str) -> Set[Tuple[str, str]]:
        done: Set[Tuple[str, str]] = set()
        if os.path.exists(path):
            with open(path, encoding="utf-8") as file:
                for line in file:
                    try:
                        row = jsonlib.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict) and "source_url" in row:
                        done.add((str(row.get("source_method", "GET")), str(row["source_url"])))
        return done

    def write(self, items: List[Dict[str, Any]]) -> None:
        for item in items:
            for row in item_rows(item, self.include_source):
                self.file.write(
                    jsonlib.dumps({k: _plain(v) for k, v in row.items()}, ensure_ascii=False) + "\n"
                )
        self.file.flush()

    def close(self) -> None:
        self.file.close()


class SchemaCsvSink(_Sink):
    def __init__(self, path: str, append: bool, schema: Schema, include_source: bool) -> None:
        self.include_source = include_source
        self.schema = schema
        columns = list(schema.fields) + (SOURCE_COLUMNS if include_source else [])
        write_header = not append or not os.path.exists(path) or os.path.getsize(path) == 0
        needs_newline = append and not write_header and _missing_final_newline(path)
        self.file = open(path, "a" if append else "w", newline="", encoding="utf-8")  # noqa: SIM115
        if needs_newline:
            self.file.write("\r\n")
        self.writer = csv.DictWriter(self.file, fieldnames=columns, extrasaction="ignore")
        if write_header:
            self.writer.writeheader()

    @staticmethod
    def read_completed(path: str) -> Set[Tuple[str, str]]:
        done: Set[Tuple[str, str]] = set()
        if os.path.exists(path):
            with open(path, newline="", encoding="utf-8") as file:
                for row in csv.DictReader(file):
                    if row.get("source_url"):
                        done.add((row.get("source_method") or "GET", row["source_url"]))
        return done

    def write(self, items: List[Dict[str, Any]]) -> None:
        for item in items:
            for row in item_rows(item, self.include_source):
                out = {}
                for column, value in row.items():
                    spec = self.schema.fields.get(column)
                    if spec is not None and spec.type_name == "json" and value is not None:
                        value = jsonlib.dumps(value, ensure_ascii=False)
                    out[column] = _plain(value)
                self.writer.writerow(out)
        self.file.flush()

    def close(self) -> None:
        self.file.close()
