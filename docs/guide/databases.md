# Writing to databases

There are two ways to write results to a table:

| | Raw responses (this page) | Typed columns with a schema |
|---|---|---|
| One row per | request | JSON record (`explode` turns an array into many rows) |
| Columns | `url`, `status`, `ok`, `error`, `attempts`, `body`, ... | the fields you choose: `id BIGINT`, `price DOUBLE PRECISION`, `tags JSONB`, ... |
| Values checked | no | yes; wrong types and `NaN` go to a rejects table |
| Running again | adds rows (or skips with `resume`) | updates rows with the same key (upsert) |
| Use it for | logging, auditing, any response format | putting API data into tables you query |

For typed columns, pass `schema=`:

```python
from reqstorm import Field

schema = {
    "id": Field("id", int, required=True, key=True),
    "name": Field("name", str),
    "price": Field("pricing.amount", float),
}
reqstorm.fetch_to_db_sync(urls, connection, table="products", schema=schema, explode="items")
```

See [Schemas: JSON to typed columns](structured-data.md) for field types, nested paths, rejected records, upserts, Pydantic models and drafting a schema from samples.

## Raw responses

`fetch_to_db` inserts one row per request through a database connection you already have:

=== "PostgreSQL"

    ```python
    import psycopg
    import reqstorm

    with psycopg.connect("dbname=crawl") as connection:
        summary = reqstorm.fetch_to_db_sync(urls, connection, table="api_results", resume=True)
    ```

=== "MySQL"

    ```python
    import pymysql
    import reqstorm

    connection = pymysql.connect(host="localhost", user="crawler", database="crawl", charset="utf8mb4")
    summary = reqstorm.fetch_to_db_sync(urls, connection, table="api_results")
    connection.close()
    ```

=== "SQLite"

    ```python
    import reqstorm

    # A file name is enough for SQLite
    summary = reqstorm.fetch_to_file_sync(urls, "results.db", table="api_results")
    ```

Supported drivers: `sqlite3`, `psycopg` and `psycopg2` (PostgreSQL), `pymysql`, `MySQLdb` (mysqlclient) and `mysql.connector` (MySQL). reqstorm does not close a connection you pass in.

### The table

reqstorm creates the table if it does not exist. The fields you filter on are real columns, and the parts whose shape varies are JSON:

| Column | PostgreSQL | MySQL | SQLite |
|---|---|---|---|
| `id` | `BIGSERIAL` | `BIGINT AUTO_INCREMENT` | `INTEGER` |
| `request_index`, `status`, `attempts` | `INTEGER` | `INT` | `INTEGER` |
| `method`, `url`, `error`, `final_url` | `TEXT` | `VARCHAR` / `LONGTEXT` | `TEXT` |
| `ok` | `BOOLEAN` | `BOOLEAN` | `INTEGER` |
| `elapsed` | `DOUBLE PRECISION` | `DOUBLE` | `REAL` |
| `history`, `headers` | `JSONB` | `JSON` | `TEXT` (JSON) |
| `body` | `TEXT`, or `BYTEA` with `body="bytes"` | `LONGTEXT` / `LONGBLOB` | `TEXT` / `BLOB` |
| `created_at` | `TIMESTAMPTZ` | `TIMESTAMP` | `TEXT` |

So the questions you ask afterwards are plain SQL:

```sql
SELECT url, error FROM api_results WHERE NOT ok;
SELECT status, count(*) FROM api_results GROUP BY status;
SELECT url FROM api_results WHERE history -> 0 ->> 'status' = '503';   -- PostgreSQL
```

### How rows are written

Rows are inserted in batches (`batch_size`, default 100) with `executemany` on a background thread, so a remote database does not slow down the requests. `sqlite3` connections are the exception: they must stay on the thread that created them, so their writes happen in place.

Existing rows are never deleted. With `resume=True`, requests that already have a successful row are skipped. `body="bytes"` stores the raw body in a binary column; `body="none"` leaves it out.

The table name must be a plain identifier (letters, digits and underscores, optionally `schema.table`).
