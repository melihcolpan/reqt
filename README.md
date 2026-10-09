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
#  'failures': {'HTTP 404': 9, 'TimeoutError': 4},
#  'latency': {'p50': 0.21, 'p95': 0.73, ...}, ...}

for error in results.errors():
    print(error["url"], error["error"] or error["status"])
```

## Contents

- [Why reqstorm](#why-reqstorm)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Results and reports](#results-and-reports)
- [Rate limits and concurrency](#rate-limits-and-concurrency)
- [Progress and logging](#progress-and-logging)
- [Timeouts and retries](#timeouts-and-retries)
- [Writing results to a file](#writing-results-to-a-file)
- [Writing results to a database](#writing-results-to-a-database)
- [Schemas: JSON to typed columns](#schemas-json-to-typed-columns)
- [Pagination](#pagination)
- [Requests from a CSV file or a table](#requests-from-a-csv-file-or-a-table)
- [Tokens, proxies and caching](#tokens-proxies-and-caching)
- [Requests, headers and bodies](#requests-headers-and-bodies)
- [Streaming results](#streaming-results)
- [TLS and sessions](#tls-and-sessions)
- [Command line](#command-line)
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
| Rate limits | Per host, in any unit (`"100/min"`), or `"auto"` from the server's 429s and headers |
| Concurrency | Overall and per host |
| Retries | Exponential backoff with a cap and jitter, `Retry-After`; plus end-of-run rounds |
| Reports | Failures by reason, p50/p95/p99 response times, per-host figures |
| Pagination | Next links, `Link` headers, cursors and page numbers |
| Requests from data | URL templates over CSV rows or SQL query results |
| Tokens and proxies | Refresh an expired token on 401; rotate through HTTP and SOCKS proxies |
| Caching | ETag / `If-None-Match`: unchanged resources cost a 304 |
| Output | JSONL, CSV, SQLite, PostgreSQL, MySQL; ordered or as completed |
| Schemas | JSON fields to typed, checked columns; nested paths, arrays to rows, upserts |
| Resume | Skip what already succeeded after an interruption |
| Planning | `estimate()` before you start, progress with ETA while running |
| Logging | Retries, pauses and failures; per-run level, file and JSON, independent of the app |
| API | Blocking (scripts, Jupyter), asyncio and a `reqstorm` command |
| Safety | TLS verified, timeouts on, bounded concurrency, fully typed |

## Installation

**In your Python code:**

```console
$ python -m pip install reqstorm
```

**As a command** (`reqstorm urls.txt -o out.jsonl`), pick what you already use:

| You have | Install with |
|---|---|
| Homebrew (macOS, Linux) | `brew install melihcolpan/tap/reqstorm` |
| pipx | `pipx install "reqstorm[socks]"` |
| uv | `uv tool install "reqstorm[socks]"` |
| Docker | nothing to install, see below |

```console
$ docker run --rm -v "$PWD:/data" \
    ghcr.io/melihcolpan/reqstorm \
    urls.txt -o results.jsonl
```

Python 3.9 to 3.13 on Linux, macOS and Windows. The only required dependency is [aiohttp](https://docs.aiohttp.org). Optional extras:

| Extra | Adds |
|---|---|
| `reqstorm[socks]` | SOCKS4/5 proxies |
| `reqstorm[pydantic]` | Pydantic models as schemas |

For PostgreSQL or MySQL output, install the driver you already use (`psycopg`, `psycopg2`, `pymysql`, `mysqlclient` or `mysql-connector-python`).

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
results.report()     # response times, statuses, hosts
```

```python
>>> results.report()["latency"]
{'min': 0.081, 'p50': 0.214, 'p90': 0.502,
 'p95': 0.733, 'p99': 1.902, 'max': 10.004,
 'mean': 0.297}
>>> results.report()["hosts"]["api.example.com:443"]
{'requests': 7000, 'ok': 6987, 'failed': 13,
 'latency': {...}}
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

**Don't know the limit?** `rate_limit="auto"` learns it from the server. A `429 Too Many Requests` pauses the host for `Retry-After` and slows it down; `X-RateLimit-Remaining` and `X-RateLimit-Reset` spread the remaining requests over the window; the rate recovers when the server stops pushing back. 429 responses are retried without using up `retries`.

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

## Progress and logging

`progress=True` shows a line on stderr that keeps moving even while every request is waiting, so you can tell a long run is alive:

```text
reqstorm: 3500/7000 (50%)  ok 3493
  failed 7  active 12  retries 41
  1.7 req/s  ETA 34m 10s
```

It shows requests in flight, retries, the rate over the last minute, the retry round, hosts paused after a 429, and the time left. `progress=` also takes a function, which gets a `ProgressInfo` every two seconds. For a generator, pass `total=` to get a percentage.

**Logs.** reqstorm logs each retry and its reason, pauses, token refreshes and final failures. Turn them on for one run, independent of your application's logging setup:

```python
reqstorm.fetch_to_file_sync(
    urls, "out.jsonl",
    log_level="INFO",    # or DEBUG: every attempt
    log_file="logs/",    # a new file per run
    log_format="json",   # optional
)
```

```text
14:32:41 WARNING reqstorm: GET .../items/412
  failed (HTTP 503) on attempt 1 of 3;
  retrying in 0.4s
15:42:18 ERROR   reqstorm: GET .../items/913
  failed after 3 attempts: HTTP 404
```

Log files are never overwritten: a directory or `{time}` in the name gives a time-stamped file per run, and an existing `run.log` becomes `run-2.log`. Without `log_level`, reqstorm logs to the standard `"reqstorm"` logger and follows your application's configuration.

**Read results while they are written.** Output files are written at least once a second (`flush_interval`), even while nothing finishes, and never with half a line; SQLite files are opened in WAL mode, so other programs can read them during the run.

## Timeouts and retries

```python
results = reqstorm.fetch_all_sync(
    urls,
    timeout=10,            # seconds per attempt
    retries=3,             # retry right away...
    backoff=0.5,           # ...0.5 s, 1 s, 2 s apart
    max_backoff=30,        # never wait longer
    retry_rounds=2,        # then resend what still
    retry_round_delay=30,  # failed, 30 s later
)
```

- **`timeout`** is the time allowed for one attempt, including reading the body (default 30 s; `None` disables it). A slow server only fails its own requests.
- **`retries`** retries a request right away, also when no response came back at all (timeouts, dropped connections). The wait starts at `backoff` and doubles each time (0.5, 1, 2, 4, ... s), up to **`max_backoff`** (30 s by default), so many retries never wait minutes.
- **Jitter** (on by default) waits a random time between half and all of that delay, so thousands of requests that failed together do not retry at the same moment. `jitter=False` waits exactly.
- A **`Retry-After`** header from the server (up to 60 s) is followed as given instead.
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

## Schemas: JSON to typed columns

Instead of storing raw responses, give a schema and each JSON field goes to its own typed column. Every value is checked first, so a number column never gets a string or a `NaN`.

```python
from reqstorm import Field

schema = {
    "id": Field("id", int, required=True, key=True),
    "name": Field("name", str, required=True),
    "price": Field("pricing.amount", float),
    "tags": Field("tags", "json"),
    "updated": Field("updated_at", "datetime"),
    "page": Field("$.meta.page", int),
}

summary = reqstorm.fetch_to_db_sync(
    urls,
    connection,
    table="products",
    schema=schema,
    explode="items",  # one row per array element
    rejects_table="products_rejects",
)
print(summary.rows, summary.rejected)
```

- **Types:** `int`, `float`, `str`, `bool`, `"datetime"` and `"json"` become `BIGINT`, `DOUBLE PRECISION`, `TEXT`, `BOOLEAN`, `TIMESTAMPTZ` and `JSONB` in PostgreSQL, and their equivalents in MySQL and SQLite.
- **Strict checks:** `10.5` is rejected for an `int`, `"42"` for a number, and NaN or Infinity always. With `coerce=True`, compatible values such as `"12.5"` are converted.
- **Nested JSON:** dotted paths read nested values (`"pricing.amount"`, `"tags.0"`). Type `"json"` keeps a part whole. `explode` makes each array element a row, and `"$."` paths read from the response root.
- **Rejected records** are never written, not even partly. They are listed in `summary.errors` with their reasons and, with `rejects_table`, stored with the original record.
- **No duplicates:** `key=True` fields form the primary key. Running the batch again updates existing rows.
- **Resume works:** each row records the request it came from (`source_url`).

The same schema works for `.db`, `.jsonl` and `.csv` files, and `reqstorm.extract(results, schema)` returns the rows as Python lists. `reqstorm.infer_schema(samples)` drafts a schema from a few responses for you to review.

**Already have a Pydantic model?** Pass it as the schema; its fields become columns and Pydantic validates each record:

```python
class Product(BaseModel):
    id: int = Field(
        json_schema_extra={"key": True})
    name: str
    price: Optional[float] = Field(
        None,
        json_schema_extra={"path": "pricing.amount"})

reqstorm.fetch_to_file_sync(
    urls, "shop.db", schema=Product, explode="items"
)
```

More in the [schemas guide](https://reqstorm.github.io/guide/structured-data/).

## Pagination

`paginate=` follows each starting URL through all of its pages, concurrently with the other URLs and under the same rate limits:

```python
results = reqstorm.fetch_all_sync(
    ["https://api.example.com/products"],
    paginate=reqstorm.NextLink("links.next"),
)
```

| Strategy | Next page comes from |
|---|---|
| `NextLink("links.next")` | a URL in the JSON body |
| `LinkHeader()` | the `Link` header (GitHub style) |
| `Cursor("meta.next", param="cursor")` | a cursor in the body |
| `PageNumber("page", items="data")` | `?page=2, 3, ...` until empty |

Each result has `page` and `seed_index` (its starting URL). Combined with a schema and `explode`, every page of a catalogue becomes typed rows in one call. `max_pages` (1000 by default) stops an API that never ends.

## Requests from a CSV file or a table

`from_template` makes one request per row. Values are percent-encoded, and rows are read lazily:

```python
rows = reqstorm.read_csv("users.csv")
requests = reqstorm.from_template(
    "https://api.example.com/users/{id}",
    rows,
    params={"country": "{country}"},
)
reqstorm.fetch_to_file_sync(requests, "users.jsonl")
```

`read_sql(connection, query)` reads rows from any database connection instead, and `json=` builds a request body per row.

## Tokens, proxies and caching

**Tokens that expire.** `BearerAuth` gets a new token when a response is 401 and sends the request again. Concurrent 401s share one refresh:

```python
auth = reqstorm.BearerAuth(refresh=get_token)
reqstorm.fetch_all_sync(urls, auth=auth)
```

**Proxies.** One proxy, a pool used in turn, or one per `Request`. HTTP and SOCKS proxies can be mixed:

```python
reqstorm.fetch_all_sync(urls, proxy=[
    "http://proxy-1.example.com:8080",
    "socks5h://user:pass@proxy-2.example.com:1080",
])
```

SOCKS4 and SOCKS5 (`socks5h://` and `socks4a://` let the proxy resolve host names, as with Tor) need `pip install "reqstorm[socks]"`.

**Caching.** `Cache` keeps responses in an SQLite file. The next run asks the server with `If-None-Match`; an unchanged resource comes back as a `304` with no body, and the stored response is used. With `ttl`, recent responses skip the network entirely:

```python
with reqstorm.Cache("responses.sqlite") as cache:
    reqstorm.fetch_all_sync(urls, cache=cache)
```

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

## Command line

The `reqstorm` command runs a batch without any Python code:

```console
$ reqstorm urls.txt -o results.jsonl \
    --rate 100/min --retries 2
$ reqstorm urls.txt --estimate --rate 100/min
$ cat urls.txt | reqstorm --rate auto -q > out.jsonl
$ reqstorm users.csv -o users.db \
    --template "https://api.example.com/users/{id}"
$ reqstorm urls.txt -o shop.db --report \
    --paginate next:links.next \
    --schema products.json --explode items
```

`-v` / `-vv` log more, `-q` logs only errors, and `--log-file logs/` writes a new log file per run. `reqstorm --help` lists every option, and the [command line guide](https://reqstorm.github.io/guide/cli/) explains them.

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
