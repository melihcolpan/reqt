# Writing to files

`fetch_to_file` writes each result as soon as it is final, so results never pile up in memory. It is the right choice for large batches.

```python
summary = reqstorm.fetch_to_file_sync(urls, "results.jsonl", rate_limit="10/s", progress=True)
print(summary.total, summary.ok, summary.failed, summary.skipped)
print(summary.errors[:5])   # the failed records, without bodies
```

## Formats

The format follows the file name:

| File name | Format |
|---|---|
| `results.jsonl` (or anything else) | JSON Lines: one JSON object per line |
| `results.csv` | CSV with a header row |
| `results.db`, `.sqlite`, `.sqlite3` | An SQLite database, see [Writing to databases](databases.md) |

Pass `format="jsonl"`, `"csv"` or `"sqlite"` to override it.

Each record has `index` (the position in your input), `method`, `url`, `status`, `ok`, `error`, `attempts`, `elapsed`, `final_url`, `history` and `body`:

```json
{"index": 0, "method": "GET", "url": "https://api.example.com/items/0", "status": 200, "ok": true, "error": null, "attempts": 1, "elapsed": 0.231, "final_url": "https://api.example.com/items/0", "history": [{"attempt": 1, "status": 200, "error": null, "elapsed": 0.231}], "body": "{\"id\": 0}"}
```

| Option | Effect |
|---|---|
| `body="text"` | The body decoded as text (default) |
| `body="base64"` | The body base64-encoded, for binary responses |
| `body="none"` | Leave the body out |
| `include_headers=True` | Add the response headers |

## Order

Records are written as requests complete. With `ordered=True` they are written in input order instead; a record that finishes early then waits in memory until the ones before it are done, so ordered output uses more memory when a few requests are slow.

## Resuming an interrupted run

With `resume=True`, requests already recorded as successful are skipped, and the others (including earlier failures) are sent again and appended:

```python
reqstorm.fetch_to_file_sync(urls, "results.jsonl", resume=True)   # first run, stopped halfway
reqstorm.fetch_to_file_sync(urls, "results.jsonl", resume=True)   # sends only what is missing
```

A line cut short by an interruption is ignored. Without `resume`, a JSONL or CSV file is overwritten. When a request appears more than once in a resumed file, its last record is the latest attempt.

## Other options

`fetch_to_file` takes every option of `fetch_all`, such as `method`, `headers`, `concurrency`, `timeout`, `retries`, `retry_rounds` and `rate_limit`, plus `batch_size` and `flush_interval`: results are written when 100 are waiting, and at least once a second, so other programs can read the file while it grows. See [Reading the output while it is written](observability.md#reading-the-output-while-it-is-written).
