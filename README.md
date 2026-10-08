# reqstorm

[![PyPI](https://img.shields.io/pypi/v/reqstorm)](https://pypi.org/project/reqstorm/)
[![Python](https://img.shields.io/pypi/pyversions/reqstorm)](https://pypi.org/project/reqstorm/)
[![CI](https://github.com/melihcolpan/reqstorm/actions/workflows/ci.yml/badge.svg)](https://github.com/melihcolpan/reqstorm/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

**Documentation: [reqstorm.github.io](https://reqstorm.github.io)**

> **reqstorm** is the new name of **reqt**. If you used reqt, see [Coming from reqt](#coming-from-reqt).

**reqstorm** sends large numbers of HTTP requests concurrently and gives you one result per request, with rate limits, retries, progress and output to files or databases built in.

```python
import reqstorm

urls = [f"https://api.example.com/items/{i}" for i in range(7000)]

print(reqstorm.estimate(len(urls), rate_limit="100/min"))
# 7000 requests: about 1h 10m (limited by rate_limit)

results = reqstorm.fetch_all_sync(urls, rate_limit="100/min", retries=2, progress=True)

print(results.summary())
# {'total': 7000, 'ok': 6987, 'failed': 13, 'failures': {'HTTP 404': 9, 'TimeoutError': 4}}
for error in results.errors():
    print(error["url"], error["error"] or error["status"])
```

- **Every request gets a result.** A timeout, a dropped connection or an invalid URL is recorded on that request and never stops the others. Each result keeps the history of its attempts.
- **Rate limits per host** in any unit (`"10/s"`, `"100/min"`, `"1000/h"`), plus overall and per-host concurrency limits.
- **Timeouts and retries**: immediate retries with exponential backoff and `Retry-After` support, and retry rounds that resend what still failed at the end.
- **Large batches**: stream results as they finish, or write them straight to JSONL, CSV, SQLite, PostgreSQL or MySQL, and resume an interrupted run where it stopped.
- **Works with or without asyncio**, and inside Jupyter notebooks.
- **TLS certificates are verified** by default. Fully typed, with a single dependency: [aiohttp](https://docs.aiohttp.org).

## Installation

```console
$ python -m pip install reqstorm
```

reqstorm supports Python 3.9 and newer.

## Usage

Every function has an async version for code that already runs an event loop: `fetch_all`, `stream`, `fetch_to_file`, `fetch_to_db`. The blocking versions end in `_sync`. They take the same options.

```python
results = reqstorm.fetch_all_sync(urls)  # in a script or a notebook
results = await reqstorm.fetch_all(urls)  # inside async code
```

### Results

`fetch_all` returns a `Results` list with one `Result` per request, in input order.

| `Result` attribute | Meaning |
|---|---|
| `ok` | `True` when a response arrived with a status below 400 |
| `status`, `headers`, `body` | The response (`status` is `None` if none arrived) |
| `text()`, `json()` | The body decoded as text (using the response charset) or JSON |
| `error` | The exception that prevented a response, or `None` |
| `attempts`, `elapsed`, `history` | Number of attempts, total seconds, and each attempt's status, error and duration |
| `url`, `method`, `final_url`, `index` | What was requested, where redirects ended, and its position in the input |

`result.raise_for_error()` raises the request's error, or `reqstorm.HTTPStatusError` for a status of 400 or above. `result.to_dict()` gives the same fields as plain data.

The `Results` list has reporting helpers:

```python
results.succeeded  # list of successful results
results.failed  # list of failed results
results.summary()  # {'total': ..., 'ok': ..., 'failed': ..., 'failures': {'HTTP 404': 9, ...}}
results.errors()  # failed requests as dicts with index, url, status, error, attempts and history
results.to_dicts()  # every result as a dict, ready for json.dump or a DataFrame
```

### Rate limits and concurrency

```python
results = reqstorm.fetch_all_sync(
    urls,
    rate_limit="100/min",  # per host; also 5 (per second), "10/s", "30/5min", "1000/h", (100, 60)
    concurrency=50,  # at most 50 requests in flight in total
    concurrency_per_host=10,  # and at most 10 to the same host
)
```

The rate limit applies to each host (`host:port`) separately and counts retries too. `reqstorm.estimate(requests, rate_limit=..., hosts=..., concurrency=..., latency=...)` tells you how long a batch should take before you send it.

### Timeouts and retries

```python
results = reqstorm.fetch_all_sync(
    urls,
    timeout=10,  # seconds per attempt, including the body; None disables it
    retries=3,  # retry right away: 0.5 s, 1 s, 2 s apart (Retry-After takes precedence)
    backoff=0.5,
    retry_rounds=2,  # then resend whatever still failed, after everything else...
    retry_round_delay=30,  # ...waiting 30 s before each round
)
```

Connection errors, timeouts and the statuses in `retry_statuses` (default: 429, 500, 502, 503, 504) are retried. Invalid URLs and other statuses such as 404 are not.

### Writing results to a file

`fetch_to_file` writes each result as soon as it is final, so results never pile up in memory:

```python
summary = reqstorm.fetch_to_file_sync(urls, "results.jsonl", rate_limit="10/s", progress=True)
print(summary.ok, summary.failed, summary.errors[:5])
```

- **Format** follows the file name: `.jsonl` (JSON Lines), `.csv`, or `.db` / `.sqlite` for SQLite. Pass `format=` to override it.
- **Order:** records are written as requests complete. With `ordered=True` they are written in input order instead; finished records then wait in memory until the ones before them are done.
- **Resume:** with `resume=True`, requests already recorded as successful are skipped, and the others, including earlier failures, are sent again and appended. Without it a JSONL or CSV file is overwritten.
- **Fields:** each record has `index`, `method`, `url`, `status`, `ok`, `error`, `attempts`, `elapsed`, `final_url`, `history` and `body`. Use `body="none"` to leave the body out, `body="base64"` for binary responses, and `include_headers=True` to add the response headers.

`progress=True` prints a line such as `reqstorm: 3500/7000 (50%)  ok 3493  failed 7  1.7 req/s  ETA 35m 00s` to stderr.

### Writing results to a database

`fetch_to_db` inserts one row per request through a database connection you already have:

```python
import psycopg  # or sqlite3, psycopg2, pymysql, MySQLdb, mysql.connector

with psycopg.connect("dbname=crawl") as connection:
    summary = reqstorm.fetch_to_db_sync(urls, connection, table="api_results", resume=True)
```

reqstorm creates the table if it does not exist. The fields you filter on are real columns, and the variable parts are JSON:

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

So `SELECT url, error FROM api_results WHERE NOT ok` works directly. Rows are inserted in batches on a background thread, so a remote database does not slow down the requests. Existing rows are never deleted; with `resume=True`, requests that already have a successful row are skipped.

### Methods, headers and bodies

Options given to `fetch_all` apply to every request. Use `reqstorm.Request` to vary them per request:

```python
results = reqstorm.fetch_all_sync(
    [
        "https://api.example.com/items/1",
        reqstorm.Request("https://api.example.com/items", method="POST", json={"name": "new"}),
    ],
    headers={"Authorization": "Bearer ..."},  # merged with each Request's own headers
)
```

`fetch_all_sync(urls, "POST", json=...)` sends every plain URL as a POST. `params`, `json` and `data` work the same way.

### Streaming results

`stream` (or `stream_sync`) yields each result as soon as it completes. `urls` can be a generator, which is read lazily, so millions of URLs never need to be in memory at once:

```python
for result in reqstorm.stream_sync(read_urls_from_file(), concurrency=100):
    save(result.url, result.status, result.body)
```

`fetch_all` also accepts `callback=`, a function or coroutine function called with each result as it completes.

### TLS

Certificates are verified by default. To trust a private certificate authority, pass an `ssl.SSLContext`:

```python
import ssl

context = ssl.create_default_context(cafile="internal-ca.pem")
results = reqstorm.fetch_all_sync(urls, ssl=context)
```

`verify_ssl=False` turns verification off. Only use it for hosts you control.

### Using your own session

In async code, pass an existing `aiohttp.ClientSession` with `session=` to share cookies, connection pools or proxy settings. reqstorm will not close it.

## Coming from reqt

reqstorm 2.0 is the next version of reqt, renamed because another project already uses that name. Install `reqstorm` and replace `import reqt` with `import reqstorm`. The `reqt` package on PyPI now just installs reqstorm and shows a deprecation warning, so existing code keeps working in the meantime.

reqt 1.x called a function with each raw response and returned nothing. That style still works, with a `DeprecationWarning`:

```python
async def handle(response):  # reqt 1.x
    print(response.status)


await reqstorm.fetch_all(urls=urls, method=handle)
```

Two things changed even in the 1.x style:

- **TLS certificates are now verified.** 1.x silently skipped verification, which let anyone in the network path impersonate the server. Pass `verify_ssl=False` only if you really need the old behaviour for hosts you control.
- **Every failed request is logged** to the `reqstorm` logger instead of only connection errors; other errors no longer abort the whole batch.

The 2.0 equivalent of the example above is:

```python
results = await reqstorm.fetch_all(urls)
for result in results:
    print(result.status)
```

The reqt 1.x style will be removed in reqstorm 3.0.

## Development

```console
$ python -m pip install -e ".[test,lint]"
$ pytest
$ ruff check . && ruff format --check . && mypy reqstorm
```

The tests run against a local server and need no network access. The PostgreSQL and MySQL tests run when `REQSTORM_TEST_POSTGRES` (a libpq connection string) and `REQSTORM_TEST_MYSQL` (`host:port:user:password:database`) are set.

## License

MIT, see [LICENSE](LICENSE).
