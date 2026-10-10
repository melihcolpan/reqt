# Progress and logging

A batch of 7000 requests at 100 a minute runs for over an hour. While it runs you want to know three things: is it still going, how far along is it, and what went wrong. reqstorm answers them with a progress line, log messages and output files that can be read while they are written.

## The progress line

```python
results = reqstorm.fetch_all_sync(urls, rate_limit="100/min", retries=2, progress=True)
```

```text
reqstorm: 3500/7000 (50%)  ok 3493  failed 7  active 12  retries 41  1.7 req/s  ETA 34m 10s
```

| Part | Meaning |
|---|---|
| `3500/7000 (50%)` | Requests with a final result, of all requests |
| `ok`, `failed` | How those ended |
| `active` | Requests being sent, waiting for a response, or waiting to retry right now. With `concurrency="auto"`, shown as `active 12/16`: in flight / currently allowed |
| `retries` | Retries so far, of all requests |
| `1.7 req/s` | Finished requests per second over the last minute |
| `round 1/2` | With `retry_rounds`: the round being sent |
| `paused api.example.com:443 42s` | With `rate_limit="auto"`: a host the server asked to wait, and for how long |
| `ETA 34m 10s` | Time left at the recent rate, plus any pause still running |

The line is refreshed on a timer, not only when a request finishes, so it keeps moving while every request is waiting on a slow server or a paused host. If `active` is above zero, the run is alive.

- On a terminal, the line is rewritten in place twice a second. Log messages are printed above it without breaking it.
- When stderr goes to a file or a CI log, a new line is written every 10 seconds, so the log shows how the run progressed.
- At the end, a final line shows the totals and the duration.

`progress=` also takes a text stream (`progress=sys.stdout`) or a function. The function gets a `reqstorm.ProgressInfo` every two seconds and once at the end with `finished=True`. Use it to show progress in a notebook, a web page or your own log:

```python
def show(info: reqstorm.ProgressInfo) -> None:
    print(f"{info.done}/{info.total}, {info.failed} failed, ETA {info.eta or 0:.0f} s")

reqstorm.fetch_all_sync(urls, progress=show)
```

### Time left for generators

The total is counted for a list. For a generator it is unknown, so the line shows only a count and no ETA. Pass `total=` when you know it:

```python
reqstorm.fetch_to_file_sync(read_urls(), "out.jsonl", total=7000, progress=True)
```

With `paginate`, the number of pages is not known in advance, so there is no total.

## Log messages

| Level | What is logged |
|---|---|
| `DEBUG` | Every attempt: method, URL, status or error, duration, proxy. Cache hits |
| `INFO` | Start (number of requests and settings) and end (counts, duration, retries), retry rounds, token refreshes |
| `WARNING` | Each retry with its reason and wait, hosts paused after a 429 or an exhausted rate limit, rejected schema records |
| `ERROR` | Each request that still failed after all its attempts |

```text
14:32:05 INFO    reqstorm: starting 7000 requests (concurrency 100, rate_limit 100/min, retries 2)
14:32:41 WARNING reqstorm: GET https://api.example.com/items/412 failed (HTTP 503) on attempt 1 of 3; retrying in 0.4s
14:35:10 WARNING reqstorm: api.example.com:443: 429 Too Many Requests (Retry-After), spacing requests 0.20s apart, pausing for 30s
15:42:18 ERROR   reqstorm: GET https://api.example.com/items/913 failed after 3 attempts: HTTP 404
15:42:19 INFO    reqstorm: finished 7000 requests in 1h 10m: 6987 ok, 13 failed, 41 retries
```

Proxy passwords are never logged (`socks5://user:***@proxy:1080`), and neither are headers or bodies.

### Two ways to turn logging on

**Through your application's logging.** reqstorm logs to the standard `"reqstorm"` logger and configures nothing itself, so it follows whatever your application sets up:

```python
import logging

logging.basicConfig(level=logging.WARNING)
logging.getLogger("reqstorm").setLevel(logging.INFO)  # more detail from reqstorm only
```

**On its own, with one parameter.** Application-wide logging is often configured somewhere else, by a framework, or not at all; and turning on `DEBUG` everywhere just to see reqstorm floods the log with other libraries. `log_level=` gives one run its own logger, independent of the application:

```python
reqstorm.fetch_to_file_sync(urls, "out.jsonl", log_level="INFO")                         # to stderr
reqstorm.fetch_to_file_sync(urls, "out.jsonl", log_level="DEBUG", log_file="logs/")      # to a file
reqstorm.fetch_to_file_sync(urls, "out.jsonl", log_level="INFO", log_format="json")      # JSON lines
```

- The application's logging configuration does not change it, and its messages do not appear in the application's log.
- Two runs at the same time can use different levels and files.
- Leaving `log_level` out uses the standard logger described above.

### Log files

`log_file=` never overwrites a file and never mixes two runs in one file:

| `log_file=` | The run writes to |
|---|---|
| `"logs/"` (a directory) | `logs/reqstorm-2026-10-09_14-32-05.123.log`: a new time-stamped file per run |
| `"campaign-{time}.log"` | `campaign-2026-10-09_14-32-05.123.log` |
| `"run.log"` | `run.log`; if it exists, `run-2.log`, then `run-3.log`, ... |
| `sys.stdout` or another stream | That stream |

Directories are created when needed. `Summary.log_file` (from `fetch_to_file` / `fetch_to_db`) tells you which file was used.

### JSON lines

`log_format="json"` writes one object per line with the time, level, message and the event's fields, ready for a log system or `jq`:

```json
{"time": "2026-10-09T14:32:41.208", "level": "WARNING", "message": "GET https://api.example.com/items/412 failed (HTTP 503) on attempt 1 of 3; retrying in 0.4s", "event": "retry", "url": "https://api.example.com/items/412", "reason": "HTTP 503", "attempt": 1, "delay": 0.412}
```

Events: `start`, `attempt`, `retry`, `pause`, `token_refresh`, `cache`, `rejected`, `retry_round`, `failed`, `finish`.

## Reading the output while it is written

`fetch_to_file` and `fetch_to_db` do not keep results in memory: each result goes into a small batch that is written as soon as it holds `batch_size` records (100), and at least every `flush_interval` seconds (1) while records are waiting. The timer runs even when no request finishes, for example while a host is paused, so the output is never more than about a second behind.

- **JSON Lines and CSV:** each batch is written in one piece and flushed, so another program (`tail -f results.jsonl`, a notebook, a dashboard) never sees half a line.
- **SQLite files:** reqstorm opens them in WAL mode, so other programs can read the database while it is being written instead of getting "database is locked". For a connection you pass to `fetch_to_db`, run `PRAGMA journal_mode=WAL` yourself if you need this.
- **PostgreSQL and MySQL:** rows are committed with each batch, so other sessions see them right away.

`flush_interval=0` writes every record immediately. It roughly doubles the cost of writing (about 5 µs instead of 2.5 µs a record in a quick measurement); writing still costs almost nothing next to the requests themselves. reqstorm does not call `fsync`: data handed to the operating system is visible to other programs at once. `fsync` only matters if the machine itself loses power, and it costs about 0.3 ms per call, a hundred times more than writing a record.

## On the command line

The `reqstorm` command shows the progress line and logs warnings and errors by default:

| Option | Effect |
|---|---|
| `-v` | Also log `INFO`: start, end, retry rounds, token refreshes |
| `-vv` | Also log `DEBUG`: every attempt |
| `-q` | No progress line; log errors only |
| `--log-file PATH` | Log to a file (a directory or `{time}` gives time-stamped files; existing files are never overwritten) |
| `--log-json` | Log JSON lines |
| `--flush-interval SECONDS` | How often waiting results are written to `-o` |

```console
$ reqstorm urls.txt -o results.jsonl --rate 100/min -v --log-file logs/
```

When the input is a file, the command counts its lines first, so the progress line shows a percentage and the time left.
