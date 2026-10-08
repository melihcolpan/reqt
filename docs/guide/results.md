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
results.summary()     # {'total': 7000, 'ok': 6987, 'failed': 13, 'failures': {'HTTP 404': 9, 'TimeoutError': 4}}
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
