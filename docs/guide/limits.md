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
