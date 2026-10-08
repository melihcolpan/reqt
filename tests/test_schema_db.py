import json
import os
import sqlite3
from datetime import datetime, timezone
from urllib.parse import quote

import pytest

import reqstorm
from reqstorm import Field


def url(server, document):
    return f"{server}/json?body={quote(json.dumps(document))}"


def page(number, items):
    return {"meta": {"page": number, "fetched": "2026-10-08T10:30:00Z"}, "items": items}


SCHEMA = {
    "id": Field("id", int, required=True, key=True),
    "name": Field("name", str, required=True),
    "price": Field("pricing.amount", float),
    "active": Field("active", bool),
    "tags": Field("tags", "json"),
    "page": Field("$.meta.page", int),
    "fetched": Field("$.meta.fetched", "datetime"),
}

EXPECTED_TYPES = {
    "sqlite": {"id": "INTEGER", "name": "TEXT", "price": "REAL", "active": "INTEGER", "tags": "TEXT",
               "page": "INTEGER", "fetched": "TEXT"},
    "postgresql": {"id": "bigint", "name": "text", "price": "double precision", "active": "boolean",
                   "tags": "jsonb", "page": "bigint", "fetched": "timestamp with time zone"},
    "mysql": {"id": "bigint", "name": "longtext", "price": "double", "active": "tinyint(1)", "tags": "json",
              "page": "bigint", "fetched": "datetime(6)"},
}  # fmt: skip


def column_types(connection, dialect, table):
    cursor = connection.cursor()
    if dialect == "sqlite":
        cursor.execute(f"PRAGMA table_info({table})")
        types = {row[1]: row[2] for row in cursor.fetchall()}
    elif dialect == "postgresql":
        cursor.execute(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = %s", (table,)
        )
        types = dict(cursor.fetchall())
    else:
        cursor.execute(
            "SELECT column_name, column_type FROM information_schema.columns "
            "WHERE table_name = %s AND table_schema = DATABASE()",
            (table,),
        )
        types = {name: kind.decode() if isinstance(kind, bytes) else kind for name, kind in cursor.fetchall()}
    cursor.close()
    return types


def query(connection, sql):
    cursor = connection.cursor()
    cursor.execute(sql)
    rows = cursor.fetchall() if cursor.description else []
    cursor.close()
    connection.commit()
    return rows


async def check(connection, dialect, server):
    for table in ("products", "products_rejects", "plain"):
        query(connection, f"DROP TABLE IF EXISTS {table}")
    urls = [
        url(server, page(1, [
            {"id": 1, "name": "kettle", "pricing": {"amount": 19.9}, "active": True, "tags": ["home"]},
            {"id": 2, "name": "mug", "pricing": {"amount": 5}, "active": False, "tags": []},
            {"id": "x3", "name": "bad id", "pricing": {"amount": 1}},
            {"id": 4, "name": None},
        ])),
        url(server, page(2, [{"id": 5, "name": "pan", "pricing": {"amount": 31.25}, "active": True}])),
        f"{server}/status/503",
    ]  # fmt: skip
    summary = await reqstorm.fetch_to_db(
        urls, connection, table="products", schema=SCHEMA, explode="items", rejects_table="products_rejects"
    )
    assert (summary.ok, summary.failed, summary.rows, summary.rejected) == (2, 1, 3, 2)

    types = column_types(connection, dialect, "products")
    assert {column: types[column] for column in SCHEMA} == EXPECTED_TYPES[dialect]

    rows = query(
        connection,
        "SELECT id, name, price, active, tags, page, fetched, source_index FROM products ORDER BY id",
    )
    assert [row[0] for row in rows] == [1, 2, 5]
    first = rows[0]
    assert first[1] == "kettle" and first[2] == 19.9 and bool(first[3]) is True and first[5] == 1
    tags = first[4] if isinstance(first[4], list) else json.loads(first[4])
    assert tags == ["home"]
    fetched = first[6]
    if isinstance(fetched, str):
        fetched = datetime.fromisoformat(fetched)
    if fetched.tzinfo is None:  # MySQL stores UTC without a time zone
        fetched = fetched.replace(tzinfo=timezone.utc)
    assert fetched == datetime(2026, 10, 8, 10, 30, tzinfo=timezone.utc)
    assert rows[2][7] == 1  # source_index of the second page

    rejects = query(connection, "SELECT source_index, reasons, record FROM products_rejects ORDER BY id")
    reasons = [json.loads(row[1]) for row in rejects]
    assert reasons == [
        ["id: expected an integer, got a string 'x3'"],
        ["name: null"],
        ["request failed: HTTP 503"],
    ]
    assert json.loads(rejects[0][2])["name"] == "bad id"

    # Same keys again with a new price: rows are updated, not duplicated
    again = [url(server, page(1, [{"id": 1, "name": "kettle", "pricing": {"amount": 17.5}}]))]
    await reqstorm.fetch_to_db(again, connection, table="products", schema=SCHEMA, explode="items")
    assert query(connection, "SELECT count(*) FROM products")[0][0] == 3
    assert query(connection, "SELECT price FROM products WHERE id = 1")[0][0] == 17.5

    # Resume skips responses that already produced rows
    resumed = await reqstorm.fetch_to_db(
        urls, connection, table="products", schema=SCHEMA, explode="items", resume=True
    )
    assert resumed.skipped == 2 and resumed.failed == 1

    # An existing table without the schema's columns is refused
    query(connection, "CREATE TABLE plain (id INTEGER)")
    with pytest.raises(reqstorm.SchemaError, match="without the columns"):
        await reqstorm.fetch_to_db(urls, connection, table="plain", schema=SCHEMA, explode="items")


async def check_paginated_model(connection, server):
    """A paginated API written through a Pydantic model; a second run updates rows in place."""
    pydantic = pytest.importorskip("pydantic")

    class Item(pydantic.BaseModel):
        id: int = pydantic.Field(json_schema_extra={"key": True})
        name: str

    query(connection, "DROP TABLE IF EXISTS paged_items")
    for _ in range(2):
        summary = await reqstorm.fetch_to_db(
            [server + "/pages/next"], connection, table="paged_items", schema=Item, explode="items",
            paginate=reqstorm.NextLink("links.next"),
        )  # fmt: skip
        assert (summary.ok, summary.rows, summary.rejected) == (3, 7, 0)
    rows = list(query(connection, "SELECT id, name, source_index FROM paged_items ORDER BY id"))
    assert rows == [(number, f"item {number}", 0) for number in range(1, 8)]


async def test_sqlite(server, tmp_path):
    connection = sqlite3.connect(tmp_path / "shop.db")
    try:
        await check(connection, "sqlite", server)
        await check_paginated_model(connection, server)
    finally:
        connection.close()


async def test_sqlite_path_and_options(server, tmp_path):
    path = tmp_path / "shop.sqlite"
    document = page(1, [{"id": 1, "name": "kettle"}])
    summary = await reqstorm.fetch_to_file([url(server, document)], path, schema=SCHEMA, explode="items",
                                           include_source=False)  # fmt: skip
    assert summary.rows == 1
    connection = sqlite3.connect(path)
    columns = [row[1] for row in connection.execute("PRAGMA table_info(reqstorm_results)")]
    assert "source_url" not in columns and columns[:2] == ["id", "name"]
    connection.close()
    with pytest.raises(ValueError, match="source columns"):
        await reqstorm.fetch_to_file([url(server, document)], path, schema=SCHEMA, explode="items",
                                     include_source=False, resume=True)  # fmt: skip


async def test_reserved_column_names(server, tmp_path):
    connection = sqlite3.connect(tmp_path / "x.db")
    with pytest.raises(reqstorm.SchemaError, match="reserved"):
        await reqstorm.fetch_to_db([], connection, schema={"source_url": Field("u", str)})
    connection.close()


@pytest.mark.skipif(not os.environ.get("REQSTORM_TEST_POSTGRES"), reason="set REQSTORM_TEST_POSTGRES")
async def test_postgresql(server):
    psycopg = pytest.importorskip("psycopg")
    connection = psycopg.connect(os.environ["REQSTORM_TEST_POSTGRES"])
    try:
        await check(connection, "postgresql", server)
        await check_paginated_model(connection, server)
    finally:
        connection.close()


@pytest.mark.skipif(not os.environ.get("REQSTORM_TEST_MYSQL"), reason="set REQSTORM_TEST_MYSQL")
async def test_mysql(server):
    pymysql = pytest.importorskip("pymysql")
    host, port, user, password, database = os.environ["REQSTORM_TEST_MYSQL"].split(":")
    connection = pymysql.connect(
        host=host, port=int(port), user=user, password=password, database=database, charset="utf8mb4"
    )
    try:
        await check(connection, "mysql", server)
        await check_paginated_model(connection, server)
    finally:
        connection.close()
