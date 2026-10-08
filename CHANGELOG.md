# Changelog

## 2.1.0

### Added
- **Structured data:** write the fields of JSON responses to typed columns. Pass `schema=` to `fetch_to_db` or `fetch_to_file`, a dict of column name to `reqstorm.Field(path, type, required=, coerce=, key=)`.
  - Types `int`, `float`, `str`, `bool`, `"datetime"` and `"json"` map to `BIGINT`, `DOUBLE PRECISION`, `TEXT`, `BOOLEAN`, `TIMESTAMPTZ` and `JSONB` in PostgreSQL, and to their MySQL and SQLite equivalents.
  - Values are checked strictly; NaN and Infinity are always rejected. `coerce=True` accepts compatible values such as `"12.5"` for a float.
  - Nested values are read with dotted paths (`"pricing.amount"`, `"tags.0"`). `explode="items"` turns each array element into a row, and `"$."` paths read from the response root.
  - Rejected records are never written. They are listed in `Summary.errors` with their reasons, and with `rejects_table` also stored in a table.
  - `key=True` fields form the primary key, and writing an existing key updates the row.
  - Rows carry `source_index`, `source_method` and `source_url`, which makes `resume=True` work.
- `reqstorm.extract(results, schema)` returns typed rows and rejected records from `fetch_all` results.
- `reqstorm.infer_schema(samples)` drafts a schema from sample responses; `print()` shows it as Python code.
- `Summary.rows` and `Summary.rejected`.

## 2.0.1

First release as **reqstorm**. reqt is renamed because another project already uses the reqt name.

- Same features as reqt 2.0.0; `import reqt` becomes `import reqstorm`.
- The `reqt` package on PyPI (2.0.1) now installs reqstorm and re-exports it with a deprecation warning.
- Documentation site at [reqstorm.github.io](https://reqstorm.github.io).

## 2.0.0 (released as reqt)

### Added
- `fetch_all` returns a `Result` per request, in input order, with `status`, `headers`, `body`, `text()`, `json()`, `error`, `attempts` and `elapsed`.
- `reqstorm.stream` yields results as they complete and consumes its input lazily, so generators of millions of URLs work.
- Per-attempt `timeout`, `retries` with exponential `backoff`, `Retry-After` support and configurable `retry_statuses`.
- `reqstorm.Request` for per-request method, headers, params, JSON or form data; `headers`, `params`, `json` and `data` defaults on `fetch_all`.
- `concurrency`, `callback`, `ssl` (custom `SSLContext`) and `session` options.
- Blocking versions for scripts and notebooks: `fetch_all_sync`, `stream_sync`, `fetch_to_file_sync`, `fetch_to_db_sync`.
- Per-host `rate_limit` in any unit (`5`, `"10/s"`, `"100/min"`, `"30/5min"`, `"1000/h"`, `(count, seconds)`) and `concurrency_per_host`.
- `retry_rounds` and `retry_round_delay`: resend requests that still failed for a retryable reason after the rest of the batch.
- `Result.history` with every attempt, `Result.to_dict()`, and a `Results` list with `succeeded`, `failed`, `errors()`, `summary()` and `to_dicts()`.
- `fetch_to_file` writes results to JSONL, CSV or SQLite as they complete, optionally in input order, and can resume an interrupted run.
- `fetch_to_db` writes to SQLite, PostgreSQL or MySQL through an existing DB-API connection, with typed columns and JSON for history and headers.
- `progress=True` prints progress with an ETA; `reqstorm.estimate` predicts how long a batch takes.
- `Result.raise_for_error()` and `reqstorm.HTTPStatusError`.
- Type hints (`py.typed`), support for Python 3.9 to 3.13, and a test suite that runs against a local server and real PostgreSQL and MySQL databases.

### Changed
- **TLS certificates are verified.** 1.x disabled verification for every request. Use `verify_ssl=False` to opt out for hosts you control.
- A failing request no longer stops the batch. In 1.x, any error other than a connection error was raised from `fetch_all` and the remaining results were lost.
- IPv6 hosts are reachable; 1.x forced IPv4.
- Packaging moved to `pyproject.toml`, and the project is licensed under MIT (the 1.x license file was empty).

### Deprecated
- Passing a callback as `method` (`fetch_all(urls=..., method=callback)`), the `request_type` and `semaphore_limit` arguments, and the `Reqt` class. They still work with a `DeprecationWarning` and will be removed in 3.0.

### Removed
- `reqstorm.helpers`, an internal module.

## 1.0.3 (2021-01-17)
- Last 1.x release.
