# Rate limits and concurrency

```python
results = reqstorm.fetch_all_sync(
    urls,
    rate_limit="100/min",     # per host
    concurrency=50,           # at most 50 requests in flight in total
    concurrency_per_host=10,  # and at most 10 to the same host
)
```

## Rate limit formats

| Value | Means |
|---|---|
| `5` or `0.5` | Requests per second |
| `"10/s"`, `"10/sec"` | 10 per second |
| `"100/min"` | 100 per minute |
| `"30/5min"` | 30 every 5 minutes |
| `"1000/h"` | 1000 per hour |
| `"2/day"` | 2 per day |
| `(100, 60)` | 100 every 60 seconds |

The limit applies to each host (`host:port`) separately, so requests to different APIs do not slow each other down. Requests to one host are spaced evenly, and retries count towards the limit too.

## Following the server: `rate_limit="auto"`

When you do not know an API's limit, let the server tell you:

```python
results = reqstorm.fetch_all_sync(urls, rate_limit="auto")
```

Requests start without a limit. Then, per host:

- A `429 Too Many Requests` pauses the host for `Retry-After` (seconds or an HTTP date) or until the rate-limit window resets, doubles the spacing between its requests, and sends the request again. These 429 retries (up to 10 per request) are not counted in `retries`.
- `X-RateLimit-Remaining` / `X-RateLimit-Reset` (also `RateLimit-*` and `X-Rate-Limit-*`) spread the remaining requests evenly over the rest of the window, and pause the host when none are left. A reset value larger than 10^9 is read as a Unix time, as GitHub sends it; smaller values are seconds.
- Each successful response without such headers shortens the spacing by 10 %, so the rate recovers when the server stops pushing back.

Pauses are capped at 5 minutes. `concurrency` and `concurrency_per_host` still apply.

## Concurrency

`concurrency` caps how many requests are in flight at once (default 100). `concurrency_per_host` caps it per host (default: no per-host cap). Requests are spread over a fixed number of workers that read your URLs lazily, so a generator of millions of URLs is fine.

Concurrency is not tied to the number of CPU cores. reqstorm runs every request on one thread with asyncio, and a request spends almost all its time waiting for the network, so one core easily keeps hundreds of requests in flight. What limits a batch is the server, and on your side the number of open connections, both covered below.

### Finding the concurrency automatically: `concurrency="auto"`

When you don't know how much a server can take, let reqstorm find out:

```python
results = reqstorm.fetch_all_sync(urls, concurrency="auto", retries=3)
```

It works like TCP congestion control:

- It starts with 8 attempts on the wire and **doubles** that after each healthy window of time (about three times the response time, at least a quarter of a second).
- It **backs off** to three quarters as soon as the servers push back: 429, 502, 503 or 504 responses, timeouts or dropped connections on more than 5 % of recent attempts. It **eases off** to nine tenths when responses take more than twice as long as that host's normal.
- After the first backoff it grows by about 10 % per window, stops just below the level where the servers pushed back, and tries that level again only after a few healthy windows.
- It grows only when the whole limit is in use, and a request waiting to retry does not hold a place, so the limit is what the servers actually see.

`max_concurrency` (default 500) is the highest it may go. With `progress=True` the line shows the current limit as `active 12/16` (in flight / allowed), and the log reports every change at `DEBUG` and a summary at `INFO`.

How it compares with the default `concurrency=100`, measured against a local server that rejects with a 503 anything over its capacity (600 requests, `retries=3`):

| Server capacity | `concurrency=100` | `concurrency="auto"` |
|---|---|---|
| 3 at a time | 494 of 600 succeed, 105 % extra attempts | 600 succeed, 2 % extra; settles at 3 |
| 12 at a time | 600 succeed, 46 % extra | 600 succeed, 1 % extra, faster; settles at 12 |
| 40 at a time | 600 succeed, 20 % extra | 600 succeed, 6 % extra; settles at 39 |
| 100 at a time | 600 succeed, 0.2 s | 600 succeed, 1.0 s (it starts at 8 and needs time to grow) |
| never rejects, slows down under load | 100 requests at once | at most 9 at once |

So `"auto"` is gentle with servers that are already busy and close to ideal for unknown capacity. For a short batch against a server that easily handles your fixed number, a fixed number is faster, because `"auto"` spends the first second or so finding out. It treats the batch as a whole; to limit one busy host among several, combine it with `concurrency_per_host`.

### Open file limit

Every connection is an open file, and the operating system limits how many a process may have (`ulimit -n`; some macOS shells start at 256). Asking for more connections than that used to fail with "Too many open files". Now, before sending anything, reqstorm compares the concurrency with the limit:

- If the limit is too low, it is raised as far as the system allows, and an `INFO` message says so.
- If it cannot be raised far enough, the concurrency is lowered to fit, with a `WARNING` that names the `ulimit -n` value to use for the full concurrency.

64 file descriptors are kept free for log and output files, and each SOCKS proxy (which has its own connection pool) counts separately. Windows has no such limit, so nothing changes there.

## Estimating a batch

`reqstorm.estimate` tells you how long a batch should take and which setting is the bottleneck, without sending anything:

```python
>>> print(reqstorm.estimate(7000, rate_limit="100/min"))
7000 requests: about 1h 10m (limited by rate_limit)
>>> print(reqstorm.estimate(7000, concurrency=50, latency=0.3))
7000 requests: about 42s (limited by concurrency)
```

`latency` is the typical response time in seconds (0.5 by default) and `hosts` is how many hosts the requests are spread over. Retries are not included.

## Progress

`progress=True` prints a progress line to stderr, refreshed even while no request finishes:

```text
reqstorm: 3500/7000 (50%)  ok 3493  failed 7  active 12  retries 41  1.7 req/s  ETA 34m 10s
```

See [Progress and logging](observability.md) for what each part means, progress functions and log messages.
