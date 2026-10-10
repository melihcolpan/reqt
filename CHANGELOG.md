# Changelog

## 2.5.0

### Added
- **`concurrency="auto"`** finds how many requests at once the servers handle well, like TCP congestion control: it starts at 8 attempts on the wire, doubles while responses stay healthy, backs off to three quarters as soon as the servers push back (429/502/503/504, timeouts, dropped connections on more than 5 % of recent attempts) and to nine tenths when responses take twice as long as the host's normal, then stops just below the level that caused pushback and probes it again only after a few healthy windows. It grows only when the whole limit is in use, and a request waiting to retry does not hold a place. `max_concurrency` (default 500) caps it. Against a local server that rejects anything over 12 at a time, it settled at 12 with 1 % extra attempts, where `concurrency=100` needed 46 % more; against one that slows down under load, it kept 9 requests in flight instead of 100.
- The progress line shows `active 12/16` (in flight / allowed) with `concurrency="auto"`; every change is logged at `DEBUG` and a summary at `INFO`. `ProgressInfo.concurrency` holds the current limit.
- **Open file limit:** before sending, the concurrency is checked against the process's open file limit (`ulimit -n`; one file per connection, plus one pool per SOCKS proxy). A limit that is too low is raised as far as the system allows; if that is not enough, the concurrency is lowered to fit, with a warning naming the `ulimit -n` value to use. This replaces "Too many open files" failures, for example with `concurrency=1000` in a shell limited to 256. Not applicable on Windows.
- Command line: `-c auto`, `--max-concurrency`.

### Fixed
- On Python 3.9 and 3.10, the built-in `TimeoutError` (raised for example by SOCKS proxies) was not treated as retryable, because it differs from `asyncio.TimeoutError` before Python 3.11.

## 2.4.3

### Fixed
- Applications that never configure `logging` no longer see reqstorm's warnings and errors on stderr. The `"reqstorm"` logger now has a `NullHandler`, as a library should, so nothing is printed unless the application turns logging on (or a run passes `log_level=`).

### Documentation
- Writing JSON fields into typed columns is now easy to find: the guide is called "Schemas: JSON to typed columns", the databases page compares raw responses with schemas at the top and shows a schema example, Getting started has a "Put API data into a table" step, and the home page names schemas.

## 2.4.2

### Added
- **Docker image:** `ghcr.io/melihcolpan/reqstorm` (linux/amd64 and linux/arm64) runs the command with SOCKS support: `docker run --rm -v "$PWD:/data" ghcr.io/melihcolpan/reqstorm urls.txt -o results.jsonl`. Built and tested for every release, tagged with the version, the minor version and `latest`.
- **Homebrew:** `brew install melihcolpan/tap/reqstorm` on macOS and Linux; the formula follows new releases automatically.
- Installation docs show each way to install, library or command, side by side.

### Changed
- Clearer package description, keywords and classifiers on PyPI.

## 2.4.1

### Fixed
- A SOCKS proxy that cannot be reached, times out or refuses a connection is now a retryable error, like an HTTP proxy failure already was. With `retries` and a proxy pool, the next attempt goes through the next proxy instead of the request failing on the first one. aiohttp-socks raises its own exception classes, which were not recognised before.

## 2.4.0

### Added
- **Logging:** reqstorm logs each retry with its reason and wait, hosts paused after a 429 or an exhausted rate limit, token refreshes, retry rounds, rejected schema records and requests that failed after all attempts, plus every attempt at `DEBUG`. Without options it logs to the standard `"reqstorm"` logger and follows the application's configuration.
- **Per-run logging:** `log_level=` gives a run its own logger, independent of the application's logging configuration; `log_file=` sends it to a file and `log_format="json"` writes JSON lines with the event's fields. Log files are never overwritten: a directory or `{time}` gives a time-stamped file per run, and an existing name gets `-2`, `-3`, ... `Summary.log_file` names the file used. Proxy passwords are redacted.
- **Progress line:** refreshed on a timer even while no request finishes, and now shows requests in flight, retries, the rate over the last minute, the retry round, paused hosts and an ETA from the recent rate (plus any running pause). On a terminal, log messages print above the line without breaking it.
- `progress=` accepts a function that receives a `reqstorm.ProgressInfo` every two seconds and at the end. `stream()` now takes `progress=` too.
- `total=` gives the progress line a percentage and an ETA when the requests come from a generator.
- **Live output:** `flush_interval` (default 1 s) writes waiting results at least that often, also while no request finishes. JSON Lines and CSV batches are written in one piece, so readers never see half a line. SQLite files created by reqstorm use WAL mode, so they can be read during the run.
- Command line: `-v` / `-vv`, `--log-file`, `--log-json`, `--flush-interval`; warnings and errors are logged by default and `-q` limits them to errors. The progress line is shown when results go to standard output too, and its total is counted from the input file.

### Fixed
- Invalid options of `fetch_to_file` no longer leave the output file open.

## 2.3.0

### Added
- **SOCKS proxies:** `proxy=` and `Request(proxy=...)` accept `socks5://`, `socks5h://`, `socks4://` and `socks4a://` URLs, with a username and password if needed. The `h` / `a` variants let the proxy resolve host names (for Tor and internal networks). SOCKS and HTTP proxies can be mixed in one pool, and HTTPS through SOCKS verifies the real host's certificate. Install with `reqstorm[socks]` (aiohttp-socks); a clear error explains this when it is missing.
- **`max_backoff`** (default 30 s) caps the wait between retries. Before, the wait doubled without limit: `retries=10` with the default `backoff=0.5` waited 256 s before the last attempt.
- **`jitter`** (default on) waits a random time between half and all of the computed delay, so requests that failed together do not retry together.
- Command line: `--max-backoff`, `--no-jitter`, and SOCKS URLs for `--proxy`.

### Changed
- Waits between retries are now capped at 30 s and jittered by default. Pass `max_backoff=float("inf"), jitter=False` for the previous behaviour. A server's `Retry-After` is still followed as given.

## 2.2.0

### Added
- **Adaptive rate limit:** `rate_limit="auto"` follows the server. A 429 pauses the host for `Retry-After` (or until the window resets) and doubles the spacing between its requests; `X-RateLimit-Remaining` / `X-RateLimit-Reset` (also `RateLimit-*` and `X-Rate-Limit-*`) spread the remaining requests over the window; successful responses bring the rate back up. 429 responses are retried up to 10 times without using up `retries`.
- **Pagination:** `paginate=` follows each starting URL through its pages with `NextLink("links.next")`, `LinkHeader()`, `Cursor("meta.next_cursor", param="cursor")` or `PageNumber("page", items="data")`, or your own `Paginator`. Results carry `page` and `seed_index`. Works with schemas and `explode`, so every page becomes typed rows. `max_pages` (default 1000) stops endless APIs, and a next page equal to the current one ends pagination.
- **Command line:** the `reqstorm` command (also `python -m reqstorm`) sends the URLs in a file or on standard input and writes JSON lines, CSV or SQLite. It supports rate limits (`--rate 100/min` or `auto`), retries, retry rounds, resume, `--estimate`, `--template`, `--paginate`, `--schema` files, `--infer-schema`, `--cache`, `--proxy`, `--bearer` / `REQSTORM_TOKEN` and `--report`. The exit status is 1 when requests failed.
- **Token refresh:** `auth=reqstorm.BearerAuth(token, refresh=get_token)` sends the token and, on a 401, gets a new one and sends the request again once. Concurrent 401s share one refresh; `refresh` may be a coroutine function.
- **Requests from data:** `reqstorm.from_template(url, rows, json=, params=)` makes one request per row, with percent-encoded `{field}` placeholders. `reqstorm.read_csv(path)` and `reqstorm.read_sql(connection, query)` read rows lazily.
- **Run report:** `Results.report()` gives response time percentiles (min, p50, p90, p95, p99, max, mean), counts by status and error type, attempts, retries, cached results and per-host figures. `summary()` now also includes `latency`, `statuses` and `hosts`, and `Summary.report` has the same report for `fetch_to_file` / `fetch_to_db`.
- **Caching:** `cache=reqstorm.Cache("responses.sqlite", ttl=None)` stores successful GET responses and revalidates them with `If-None-Match` / `If-Modified-Since`; a 304 reuses the stored response. With `ttl`, recent responses skip the network. Cached results have `from_cache=True`.
- **Proxies:** `proxy=` takes one proxy URL or a list used in turn, attempt by attempt; `Request(proxy=...)` sets one per request.
- **Pydantic models as schemas:** pass a Pydantic 2 model as `schema=` to `fetch_to_db`, `fetch_to_file` or `extract`. Fields become columns, `json_schema_extra={"path": ..., "key": True}` sets paths and keys, and records are validated by the model. Install with `reqstorm[pydantic]`.

### Changed
- `Result.to_dict()` and JSONL/CSV output include `from_cache` and `page`.

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
