# Timeouts and retries

```python
results = reqstorm.fetch_all_sync(
    urls,
    timeout=10,             # seconds per attempt, including reading the body
    retries=3,              # retry right away, about 0.5 s, 1 s and 2 s apart
    backoff=0.5,            # the first wait, doubled after each retry
    max_backoff=30,         # never wait longer than this between two attempts
    jitter=True,            # spread the waits so retries do not arrive together
    retry_rounds=2,         # then resend what still failed, after everything else
    retry_round_delay=30,   # waiting 30 s before each round
)
```

## Timeouts

`timeout` is the time allowed for one attempt, including reading the body (default 30 seconds). `timeout=None` turns it off. A slow server only fails its own requests; it never holds up the batch.

## Immediate retries

With `retries=N`, a request is tried up to N more times right away. Between two attempts reqstorm waits, and the wait grows with each retry (exponential backoff). This applies both when a response comes back with a retryable status and when no response comes back at all (a timeout or a dropped connection).

| Retry | Wait without jitter (`backoff=0.5`, `max_backoff=30`) | With jitter (default) |
|---|---|---|
| 1st | 0.5 s | 0.25–0.5 s |
| 2nd | 1 s | 0.5–1 s |
| 3rd | 2 s | 1–2 s |
| 4th | 4 s | 2–4 s |
| 5th | 8 s | 4–8 s |
| 6th | 16 s | 8–16 s |
| 7th and later | 30 s (`max_backoff`) | 15–30 s |

- **`backoff`** is the first wait in seconds; it doubles after each retry. `backoff=0` retries without waiting.
- **`max_backoff`** caps the wait (default 30 seconds). Without a cap, `retries=10` would wait 256 seconds before the last attempt.
- **`jitter`** (on by default) waits a random time between half and all of the computed delay. When a server has a hiccup, thousands of requests fail at the same moment; without jitter they would all retry at the same moment too, and hit the server again together. `jitter=False` waits exactly.
- **`Retry-After`**: when the server sends this header (in seconds, up to 60), that wait is used instead, exactly as given. `max_backoff` and jitter do not apply to it, because the server said when to come back.

The waits come on top of the attempts themselves: with `timeout=10` and `retries=3`, a server that never answers costs at most 4 × 10 s of attempts plus about 3.5 s of waiting.

## Retry rounds

Some failures are temporary: an API that is briefly overloaded, or a rate limit you hit anyway. With `retry_rounds=N`, requests that still failed for a retryable reason are held back and sent again after the rest of the batch has finished, up to N more rounds, each after `retry_round_delay` seconds. Each request still produces exactly one result, with every attempt in its `history`.

## What is retried

| Retried | Not retried |
|---|---|
| Connection errors and dropped connections | Invalid URLs |
| Timeouts | 4xx statuses such as 400, 401, 403, 404 |
| Statuses in `retry_statuses` (default 429, 500, 502, 503, 504) | Other statuses |

Change `retry_statuses` to retry other statuses, for example `retry_statuses=(429, 503)`.
