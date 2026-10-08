# Timeouts and retries

```python
results = reqstorm.fetch_all_sync(
    urls,
    timeout=10,             # seconds per attempt, including reading the body
    retries=3,              # retry right away, 0.5 s, 1 s and 2 s apart
    backoff=0.5,
    retry_rounds=2,         # then resend what still failed, after everything else
    retry_round_delay=30,   # waiting 30 s before each round
)
```

## Timeouts

`timeout` is the time allowed for one attempt, including reading the body (default 30 seconds). `timeout=None` turns it off. A slow server only fails its own requests; it never holds up the batch.

## Immediate retries

With `retries=N`, a request is tried up to N more times right away. The delay starts at `backoff` seconds and doubles each time. If the server sends a `Retry-After` header (in seconds, up to 60), that delay is used instead.

## Retry rounds

Some failures are temporary: an API that is briefly overloaded, or a rate limit you hit anyway. With `retry_rounds=N`, requests that still failed for a retryable reason are held back and sent again after the rest of the batch has finished, up to N more rounds, each after `retry_round_delay` seconds. Each request still produces exactly one result, with every attempt in its `history`.

## What is retried

| Retried | Not retried |
|---|---|
| Connection errors and dropped connections | Invalid URLs |
| Timeouts | 4xx statuses such as 400, 401, 403, 404 |
| Statuses in `retry_statuses` (default 429, 500, 502, 503, 504) | Other statuses |

Change `retry_statuses` to retry other statuses, for example `retry_statuses=(429, 503)`.
