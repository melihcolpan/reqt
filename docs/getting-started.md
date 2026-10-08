# Getting started

## Install

```console
$ python -m pip install reqstorm
```

reqstorm supports Python 3.9 and newer. Its only dependency is [aiohttp](https://docs.aiohttp.org). For PostgreSQL or MySQL output, install the database driver you already use (see [Writing to databases](guide/databases.md)).

## Your first batch

Every function comes in two forms with the same options: a blocking one ending in `_sync` for scripts and notebooks, and an async one for code that already runs an event loop.

=== "Script or notebook"

    ```python
    import reqstorm

    urls = [f"https://httpbin.org/get?page={page}" for page in range(1, 101)]
    results = reqstorm.fetch_all_sync(urls, concurrency=20, timeout=10, retries=2)

    for result in results:
        if result.ok:
            print(result.url, result.status, len(result.body))
        else:
            print(result.url, "failed:", result.error or result.status)
    ```

=== "async code"

    ```python
    import asyncio
    import reqstorm

    async def main():
        urls = [f"https://httpbin.org/get?page={page}" for page in range(1, 101)]
        results = await reqstorm.fetch_all(urls, concurrency=20, timeout=10, retries=2)
        for result in results:
            print(result.url, result.status if result.ok else result.error)

    asyncio.run(main())
    ```

`fetch_all` returns one result per URL, in the order you gave them. A failed request never raises: its `error` or `status` tells you what went wrong, and the other requests carry on.

## Plan a large batch

Before sending thousands of requests to an API with a rate limit, check how long it will take:

```python
>>> print(reqstorm.estimate(7000, rate_limit="100/min"))
7000 requests: about 1h 10m (limited by rate_limit)
>>> print(reqstorm.estimate(7000, rate_limit="100/min", hosts=7))
7000 requests: about 10m 00s (limited by rate_limit)
```

Then run it with progress and send the results straight to a file, so nothing is lost if it stops halfway:

```python
summary = reqstorm.fetch_to_file_sync(
    urls,
    "results.jsonl",
    rate_limit="100/min",
    retries=2,
    progress=True,   # reqstorm: 3500/7000 (50%)  ok 3493  failed 7  1.7 req/s  ETA 35m 00s
    resume=True,     # run it again after an interruption: finished requests are skipped
)
print(summary.ok, summary.failed)
```

## The functions

| Blocking | async | What it does |
|---|---|---|
| `fetch_all_sync` | `fetch_all` | Returns a `Results` list, one `Result` per request |
| `stream_sync` | `stream` | Yields each result as soon as it completes |
| `fetch_to_file_sync` | `fetch_to_file` | Writes results to JSONL, CSV or SQLite as they complete |
| `fetch_to_db_sync` | `fetch_to_db` | Inserts results into SQLite, PostgreSQL or MySQL |
| `estimate` | | Predicts how long a batch takes |
