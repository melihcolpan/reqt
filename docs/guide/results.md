# Results and reports

`fetch_all` returns a `Results` list with one `Result` per request, in input order.

## A result

| Attribute | Meaning |
|---|---|
| `ok` | `True` when a response arrived with a status below 400 |
| `status`, `headers`, `body` | The response. `status` is `None` when no response arrived |
| `text()`, `json()` | The body decoded as text (using the response charset) or parsed as JSON |
| `error` | The exception that prevented a response, or `None` |
| `attempts`, `elapsed` | How many attempts were made, and the total seconds they took |
| `history` | Every attempt: its number, status, error and duration |
| `url`, `method`, `final_url`, `index` | What was requested, where redirects ended, and its position in the input |
| `from_cache` | `True` when the response came from the [cache](auth.md#caching-and-conditional-requests) |
| `page`, `seed_index` | With [pagination](pagination.md): the page number and the position of the starting URL |

```python
result = results[0]
if not result.ok:
    for attempt in result.history:
        print(attempt.number, attempt.status, attempt.error, attempt.elapsed)
```

`result.raise_for_error()` raises the request's error, or `reqstorm.HTTPStatusError` for a status of 400 or above, when you prefer exceptions. `result.to_dict()` gives the same fields as plain data.

## Reports for the whole batch

```python
results.succeeded     # the successful results
results.failed        # the failed results
results.summary()     # counts, failures by reason, response times, statuses and hosts
results.errors()      # the failed requests as dicts: index, url, status, error, attempts, history
results.to_dicts()    # every result as a dict, ready for json.dump or a pandas DataFrame
```

`errors()` is plain data, so it can go straight into a log or a file:

```python
import json

with open("errors.json", "w") as file:
    json.dump(results.errors(), file, indent=2)
```

```json
[
  {
    "index": 12,
    "method": "GET",
    "url": "https://api.example.com/items/12",
    "status": null,
    "ok": false,
    "error": "TimeoutError",
    "attempts": 3,
    "elapsed": 31.502,
    "final_url": null,
    "history": [
      {"attempt": 1, "status": null, "error": "TimeoutError", "elapsed": 10.001},
      {"attempt": 2, "status": 503, "error": null, "elapsed": 0.412},
      {"attempt": 3, "status": null, "error": "TimeoutError", "elapsed": 10.002}
    ]
  }
]
```

## Response times, statuses and hosts

`results.report()` summarises how the batch went, for example to compare two APIs or to find a slow host:

```python
>>> report = results.report()
>>> report["latency"]
{'min': 0.081, 'p50': 0.214, 'p90': 0.502, 'p95': 0.733, 'p99': 1.902, 'max': 10.004, 'mean': 0.297}
>>> report["statuses"]
{200: 6987, 404: 9, 503: 2}
>>> report["errors"]
{'TimeoutError': 2}
>>> report["hosts"]["api.example.com:443"]
{'requests': 7000, 'ok': 6987, 'failed': 13, 'latency': {...}}
```

Times are in seconds per request, retries included; responses served from the cache are left out of them. The report also has `total`, `ok`, `failed`, `attempts`, `retries` and `from_cache`. `summary()` includes `latency`, `statuses` and `hosts` too, and `fetch_to_file` / `fetch_to_db` return the same report in `summary.report`.
