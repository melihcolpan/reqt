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

`progress=True` prints progress to stderr. In a terminal the line updates in place; when output goes to a log, a new line is written every few seconds:

```text
reqstorm: 3500/7000 (50%)  ok 3493  failed 7  1.7 req/s  ETA 35m 00s
```

Pass a file object instead of `True` to write progress somewhere else.
