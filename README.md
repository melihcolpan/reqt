# reqstorm

[![PyPI](https://img.shields.io/pypi/v/reqstorm?logo=pypi&logoColor=white)](https://pypi.org/project/reqstorm/)
[![Python](https://img.shields.io/pypi/pyversions/reqstorm?logo=python&logoColor=white)](https://pypi.org/project/reqstorm/)
[![CI](https://github.com/melihcolpan/reqstorm/actions/workflows/ci.yml/badge.svg)](https://github.com/melihcolpan/reqstorm/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-reqstorm.github.io-5b50e0)](https://reqstorm.github.io)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

**Send thousands of HTTP requests without writing the plumbing.**

reqstorm sends large numbers of HTTP requests concurrently and gives you one result per request. Rate limits, retries, progress, error reports and output to files or databases are built in.

📖 **Documentation: [reqstorm.github.io](https://reqstorm.github.io)**

> reqstorm is the new name of **reqt**. Coming from reqt? See [Coming from reqt](#coming-from-reqt).

```python
import reqstorm

urls = [
    f"https://api.example.com/items/{i}"
    for i in range(7000)
]

print(reqstorm.estimate(7000, rate_limit="100/min"))
# 7000 requests: about 1h 10m
# (limited by rate_limit)

results = reqstorm.fetch_all_sync(
    urls,
    rate_limit="100/min",
    retries=2,
    progress=True,
)

print(results.summary())
# {'total': 7000, 'ok': 6987, 'failed': 13,
#  'failures': {'HTTP 404': 9, 'TimeoutError': 4}}

for error in results.errors():
    print(error["url"], error["error"] or error["status"])
```

## Contents

- [Why reqstorm](#why-reqstorm)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Results and reports](#results-and-reports)
- [Rate limits and concurrency](#rate-limits-and-concurrency)
- [Timeouts and retries](#timeouts-and-retries)
- [Writing results to a file](#writing-results-to-a-file)
- [Writing results to a database](#writing-results-to-a-database)
- [Requests, headers and bodies](#requests-headers-and-bodies)
- [Streaming results](#streaming-results)
- [TLS and sessions](#tls-and-sessions)
- [When to use something else](#when-to-use-something-else)
- [Coming from reqt](#coming-from-reqt)
- [Development](#development)
- [License](#license)

## Why reqstorm

Sending one request is easy. Sending 7000 to an API that allows 100 a minute, without losing results when a few of them fail, is where the work is:

- **Pacing:** stay under the API's rate limit, per host, including retries.
- **Isolating failures:** one timeout or dropped connection must not stop the other 6999 requests.
- **Retrying the right failures:** retry 503s and timeouts, never 404s, and back off when the server says so.
- **Keeping the results:** write them somewhere as they arrive, and resume where you stopped if the run is interrupted.
- **Knowing what happened:** which requests failed, why, and after how many attempts.

reqstorm does all of that for you, with one call.

| Feature | What you get |
|---|---|
| One result per request | Failures are recorded, never raised; every attempt is kept |
| Rate limits | Per host, in any unit: `"10/s"`, `"100/min"`, `"1000/h"` |
| Concurrency | Overall and per host |
| Retries | Immediate, with backoff and `Retry-After`; plus end-of-run rounds |
| Reports | `summary()`, `errors()`, `to_dicts()` |
| Output | JSONL, CSV, SQLite, PostgreSQL, MySQL; ordered or as completed |
| Resume | Skip what already succeeded after an interruption |
| Planning | `estimate()` before you start, progress with ETA while running |
| API | Blocking (scripts, Jupyter) and asyncio |
| Safety | TLS verified, timeouts on, bounded concurrency, fully typed |

## Installation

```console
$ python -m pip install reqstorm
```

reqstorm supports Python 3.9 to 3.13 on Linux, macOS and Windows. Its only dependency is [aiohttp](https://docs.aiohttp.org). For PostgreSQL or MySQL output, install the driver you already use (`psycopg`, `psycopg2`, `pymysql`, `mysqlclient` or `mysql-connector-python`).

## Quick start

Every function has a blocking version ending in `_sync` for scripts and notebooks, and an async version for code that already runs an event loop. Both take the same options.

**In a script or a Jupyter notebook:**

```python
import reqstorm

urls = [
    f"https://httpbin.org/get?page={page}"
    for page in range(1, 101)
]

results = reqstorm.fetch_all_sync(
    urls,
    concurrency=20,
    timeout=10,
    retries=2,
)

for result in results:
    if result.ok:
        print(result.url, result.status)
    else:
        print(result.url, "failed:",
              result.error or result.status)
```

**In async code:**

```python
import asyncio
import reqstorm

async def main():
    results = await reqstorm.fetch_all(
        urls, concurrency=20, timeout=10
    )
    print(results.summary())

asyncio.run(main())
```

| Blocking | async | What it does |
|---|---|---|
| `fetch_all_sync` | `fetch_all` | Returns a `Results` list, one `Result` per request |
| `stream_sync` | `stream` | Yields each result as soon as it completes |
| `fetch_to_file_sync` | `fetch_to_file` | Writes results to JSONL, CSV or SQLite |
| `fetch_to_db_sync` | `fetch_to_db` | Inserts results into SQLite, PostgreSQL or MySQL |
| `estimate` | | Predicts how long a batch takes |

## Results and reports

`fetch_all` returns a `Results` list with one `Result` per request, in the order you gave them. A failed request never raises an exception; it is recorded on its result.

| `Result` attribute | Meaning |
|---|---|
| `ok` | `True` when a response arrived with a status below 400 |
| `status`, `headers`, `body` | The response; `status` is `None` when none arrived |
| `text()`, `json()` | The body as text (using the response charset) or as JSON |
| `error` | The exception that prevented a response, or `None` |
| `attempts`, `elapsed` | Number of attempts and total seconds |
| `history` | Every attempt: number, status, error and duration |
| `url`, `method` | What was requested |
| `final_url` | Where redirects ended |
| `index` | Position in your input |

```python
results.succeeded    # successful results
results.failed       # failed results
results.summary()    # counts, failures grouped by reason
results.errors()     # failed requests as plain dicts
results.to_dicts()   # every result as a dict
```

`errors()` returns plain data, ready for a log file or a DataFrame:

```json
{
  "index": 12,
  "method": "GET",
  "url": "https://api.example.com/items/12",
  "status": null,
  "ok": false,
  "error": "TimeoutError",
  "attempts": 3,
  "elapsed": 20.415,
  "history": [
    {"attempt": 1, "status": null,
     "error": "TimeoutError", "elapsed": 10.001},
    {"attempt": 2, "status": 503,
     "error": null, "elapsed": 0.412},
    {"attempt": 3, "status": null,
     "error": "TimeoutError", "elapsed": 10.002}
  ]
}
```

Prefer exceptions? `result.raise_for_error()` raises the request's error, or `reqstorm.HTTPStatusError` for a status of 400 or above.

## Rate limits and concurrency

```python
results = reqstorm.fetch_all_sync(
    urls,
    rate_limit="100/min",     # per host
    concurrency=50,           # in flight, in total
    concurrency_per_host=10,  # in flight, per host
)
```

| `rate_limit` value | Means |
|---|---|
| `5` or `0.5` | Requests per second |
| `"10/s"` | 10 per second |
| `"100/min"` | 100 per minute |
| `"30/5min"` | 30 every 5 minutes |
| `"1000/h"` | 1000 per hour |
| `"2/day"` | 2 per day |
| `(100, 60)` | 100 every 60 seconds |

The limit applies to each host (`host:port`) separately and counts retries too, so requests to different APIs never slow each other down. Requests to one host are spaced evenly.

**Plan before you send.** `estimate` predicts the duration and names the bottleneck, without sending anything:

```python
>>> print(reqstorm.estimate(7000, rate_limit="100/min"))
7000 requests: about 1h 10m (limited by rate_limit)
>>> print(reqstorm.estimate(
...     7000, rate_limit="100/min", hosts=7))
7000 requests: about 10m 00s (limited by rate_limit)
```

**Watch it run.** `progress=True` prints progress to stderr:

```text
reqstorm: 3500/7000 (50%)  ok 3493  failed 7
          1.7 req/s  ETA 35m 00s
```

## Timeouts and retries

```python
results = reqstorm.fetch_all_sync(
    urls,
    timeout=10,            # seconds per attempt
    retries=3,             # retry right away...
    backoff=0.5,           # ...0.5 s, 1 s, 2 s apart
    retry_rounds=2,        # then resend what still
    retry_round_delay=30,  # failed, 30 s later
)
```

- **`timeout`** is the time allowed for one attempt, including reading the body (default 30 s; `None` disables it). A slow server only fails its own requests.
- **`retries`** retries a request right away. The delay starts at `backoff` and doubles; a `Retry-After` header (up to 60 s) takes precedence.
- **`retry_rounds`** holds back requests that still failed for a retryable reason and sends them again after the rest of the batch, for temporary outages. Each request still produces exactly one result, with all its attempts in `history`.

| Retried | Not retried |
|---|---|
| Connection errors and dropped connections | Invalid URLs |
| Timeouts | 4xx such as 400, 401, 403, 404 |
| `retry_statuses` (default 429, 500, 502, 503, 504) | Other statuses |

## Writing results to a file

For large batches, write results as they arrive instead of keeping them in memory:

```python
summary = reqstorm.fetch_to_file_sync(
    urls,
    "results.jsonl",
    rate_limit="10/s",
    progress=True,
    resume=True,
)
print(summary.ok, summary.failed, summary.skipped)
print(summary.errors[:5])  # failed records
```

- **Format** follows the file name: `.jsonl`, `.csv`, or `.db` / `.sqlite` / `.sqlite3` for SQLite. Pass `format=` to override it.
- **Order:** records are written as requests complete; `ordered=True` writes them in input order instead (finished records wait in memory for the ones before them).
- **Resume:** with `resume=True`, requests already recorded as successful are skipped and the rest are sent and appended. A line cut short by an interruption is ignored. Without `resume`, a JSONL or CSV file is overwritten.
- **Fields:** `index`, `method`, `url`, `status`, `ok`, `error`, `attempts`, `elapsed`, `final_url`, `history` and `body`.
- **Body:** `body="text"` (default), `"base64"` for binary responses, or `"none"`. `include_headers=True` adds the response headers.

## Writing results to a database

`fetch_to_db` inserts one row per request through a connection you already have:

```python
import psycopg
import reqstorm

with psycopg.connect("dbname=crawl") as connection:
    summary = reqstorm.fetch_to_db_sync(
        urls,
        connection,
        table="api_results",
        resume=True,
    )
```

Supported drivers: `sqlite3`, `psycopg` and `psycopg2` (PostgreSQL), `pymysql`, `MySQLdb` and `mysql.connector` (MySQL). For SQLite a file name is enough: `fetch_to_file_sync(urls, "results.db")`.

reqstorm creates the table if it does not exist. The fields you filter on are real columns; the parts whose shape varies are JSON:

| Column | PostgreSQL | MySQL | SQLite |
|---|---|---|---|
| `id` | `BIGSERIAL` | `BIGINT AUTO_INCREMENT` | `INTEGER` |
| `request_index`, `status`, `attempts` | `INTEGER` | `INT` | `INTEGER` |
| `method`, `url`, `error`, `final_url` | `TEXT` | `VARCHAR` / `LONGTEXT` | `TEXT` |
| `ok` | `BOOLEAN` | `BOOLEAN` | `INTEGER` |
| `elapsed` | `DOUBLE PRECISION` | `DOUBLE` | `REAL` |
| `history`, `headers` | `JSONB` | `JSON` | `TEXT` |
| `body` | `TEXT` / `BYTEA` | `LONGTEXT` / `LONGBLOB` | `TEXT` / `BLOB` |
| `created_at` | `TIMESTAMPTZ` | `TIMESTAMP` | `TEXT` |

So the questions you ask afterwards are plain SQL:

```sql
SELECT url, error FROM api_results WHERE NOT ok;
SELECT status, count(*) FROM api_results
GROUP BY status;
```

Rows are inserted in batches on a background thread, so a remote database does not slow the requests down. Existing rows are never deleted. `body="bytes"` stores the raw body in a binary column.

## Requests, headers and bodies

Options given to `fetch_all` apply to every request; `reqstorm.Request` varies them per request:

```python
results = reqstorm.fetch_all_sync(
    [
        "https://api.example.com/items/1",
        reqstorm.Request(
            "https://api.example.com/items",
            method="POST",
            json={"name": "new"},
        ),
    ],
    headers={"Authorization": "Bearer ..."},
)
```

- `fetch_all_sync(urls, "POST", json=...)` sends every plain URL as a POST.
- `params`, `json` and `data` work the same way.
- A `Request`'s own headers are merged on top of the shared ones; its other fields replace the shared value.

## Streaming results

`stream_sync` (or `stream` in async code) yields each result as soon as it completes. URLs can come from a generator, which is read lazily, so millions of URLs never need to fit in memory:

```python
def read_urls():
    with open("urls.txt") as file:
        for line in file:
            yield line.strip()

for result in reqstorm.stream_sync(read_urls()):
    save(result.url, result.status, result.body)
```

Leaving the loop early (`break`) cancels the requests still in flight. `fetch_all` also accepts `callback=`, called with each result as it completes.

## TLS and sessions

TLS certificates are verified by default. To trust a private certificate authority, pass an `ssl.SSLContext`:

```python
import ssl

context = ssl.create_default_context(
    cafile="internal-ca.pem"
)
results = reqstorm.fetch_all_sync(urls, ssl=context)
```

`verify_ssl=False` turns verification off; only use it for hosts you control.

In async code, `session=` takes an existing `aiohttp.ClientSession` to share cookies, connection pools or proxy settings. reqstorm does not close it.

## When to use something else

reqstorm is built for batches. For other jobs, these are better fits:

| You need | Use |
|---|---|
| A few requests, simple scripts | [requests](https://requests.readthedocs.io) |
| A general HTTP client, sync or async, HTTP/2 | [httpx](https://www.python-httpx.org) |
| Full control over an asyncio HTTP client or server | [aiohttp](https://docs.aiohttp.org) |
| Crawling websites (following links, parsing pages) | [Scrapy](https://scrapy.org) |

reqstorm uses aiohttp underneath and adds the batch work: pacing, retries, failure isolation, reports and output.

## Coming from reqt

reqstorm 2.0.1 is the next version of reqt, renamed because another project already uses the reqt name.

```console
$ python -m pip install reqstorm
```

Then replace `import reqt` with `import reqstorm`. The `reqt` package on PyPI now only installs reqstorm and re-exports it with a deprecation warning, so existing code keeps working while you switch.

The reqt 1.x style, which called a function with each raw response, still works with a `DeprecationWarning` and will be removed in reqstorm 3.0:

```python
async def handle(response):  # reqt 1.x style
    print(response.status)

await reqstorm.fetch_all(urls=urls, method=handle)

# The same today:
for result in reqstorm.fetch_all_sync(urls):
    print(result.status)
```

Two things changed even in the 1.x style:

- **TLS certificates are now verified.** reqt 1.x skipped verification. Use `verify_ssl=False` only for hosts you control.
- **Failures no longer stop the batch.** Every failed request is logged to the `reqstorm` logger.

## Development

```console
$ git clone https://github.com/melihcolpan/reqstorm
$ cd reqstorm
$ python -m pip install -e ".[test,lint]"
$ pytest
$ ruff check . && ruff format --check . && mypy reqstorm
```

- The tests run against a local server and a throwaway certificate authority; they need no network access.
- The PostgreSQL and MySQL tests run when `REQSTORM_TEST_POSTGRES` (a libpq connection string) and `REQSTORM_TEST_MYSQL` (`host:port:user:password:database`) are set.
- The documentation is in `docs/` and builds with `pip install -r docs/requirements.txt && mkdocs serve`.

CI runs the tests on Python 3.9 to 3.13 (Linux, plus macOS and Windows), against real PostgreSQL and MySQL, and builds the package and the documentation. A GitHub release tagged `vX.Y.Z` publishes to PyPI through trusted publishing.

Bug reports and pull requests are welcome on [GitHub](https://github.com/melihcolpan/reqstorm/issues).

## License

MIT, see [LICENSE](LICENSE).
