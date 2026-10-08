# reqt

[![PyPI](https://img.shields.io/pypi/v/reqt)](https://pypi.org/project/reqt/)
[![Python](https://img.shields.io/pypi/pyversions/reqt)](https://pypi.org/project/reqt/)
[![CI](https://github.com/melihcolpan/reqt/actions/workflows/ci.yml/badge.svg)](https://github.com/melihcolpan/reqt/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

**reqt** sends large numbers of HTTP requests concurrently with asyncio, and gives you one result per request.

```python
import asyncio
import reqt

async def main():
    urls = [f"https://httpbin.org/get?page={page}" for page in range(1, 101)]
    results = await reqt.fetch_all(urls, concurrency=20, timeout=10, retries=2)

    for result in results:
        if result.ok:
            print(result.url, result.status, len(result.body))
        else:
            print(result.url, "failed:", result.error or result.status)

asyncio.run(main())
```

- **Every request gets a result.** A timeout, a dropped connection or an invalid URL is recorded on that request's `Result` and never stops the others.
- **Results come back in the order you gave**, or one by one as they finish with `reqt.stream`.
- **Bounded concurrency, timeouts and retries** with exponential backoff, honouring `Retry-After`.
- **TLS certificates are verified** by default.
- **Large inputs stream.** `urls` can be a generator; requests are created lazily, so millions of URLs do not need to fit in memory.
- **Fully typed**, with a single dependency: [aiohttp](https://docs.aiohttp.org).

## Installation

```console
$ python -m pip install reqt
```

reqt supports Python 3.9 and newer.

## Usage

### Results

`fetch_all` returns a list of `Result` objects, one per request, in input order:

| Attribute | Meaning |
|---|---|
| `ok` | `True` when a response arrived with a status below 400 |
| `status`, `headers`, `body` | The response (`status` is `None` if none arrived) |
| `text()`, `json()` | The body decoded as text (using the response charset) or JSON |
| `error` | The exception that prevented a response, or `None` |
| `attempts`, `elapsed` | How many attempts were made and how long they took in seconds |
| `url`, `final_url`, `request`, `index` | What was requested, where redirects ended, and its position in the input |

`result.raise_for_error()` raises the request's error, or `reqt.HTTPStatusError` for a status of 400 or above.

### Methods, headers and bodies

Options given to `fetch_all` apply to every request. Use `reqt.Request` to vary them per request:

```python
results = await reqt.fetch_all(
    [
        "https://api.example.com/items/1",
        reqt.Request("https://api.example.com/items", method="POST", json={"name": "new"}),
    ],
    headers={"Authorization": "Bearer ..."},  # merged with each Request's own headers
)
```

`fetch_all(urls, "POST", json=...)` sends every plain URL as a POST. `params`, `json` and `data` work the same way.

### Concurrency, timeouts and retries

```python
results = await reqt.fetch_all(
    urls,
    concurrency=50,  # at most 50 requests in flight
    timeout=10,      # seconds per attempt, including the body; None disables it
    retries=3,       # retry connection errors, timeouts, 429 and 5xx
    backoff=0.5,     # 0.5 s, 1 s, 2 s between attempts; Retry-After takes precedence
)
```

Choose which statuses are retried with `retry_statuses=` (default: 429, 500, 502, 503, 504). Invalid URLs are never retried.

### Streaming results

`reqt.stream` yields each result as soon as it completes, so you can process or save results while the rest are still running:

```python
async for result in reqt.stream(read_urls_from_file(), concurrency=100):
    save(result.url, result.status, result.body)
```

`fetch_all` also accepts `callback=`, a function or coroutine function called with each result as it completes.

### TLS

Certificates are verified by default. To trust a private certificate authority, pass an `ssl.SSLContext`:

```python
import ssl

context = ssl.create_default_context(cafile="internal-ca.pem")
results = await reqt.fetch_all(urls, ssl=context)
```

`verify_ssl=False` turns verification off. Only use it for hosts you control.

### Using your own session

Pass an existing `aiohttp.ClientSession` with `session=` to share cookies, connection pools or proxy settings. reqt will not close it.

## Upgrading from reqt 1.x

reqt 1.x called a function with each raw response and returned nothing. That style still works in 2.0, with a `DeprecationWarning`:

```python
async def handle(response):           # reqt 1.x
    print(response.status)

await reqt.fetch_all(urls=urls, method=handle)
```

Two things changed even in the 1.x style:

- **TLS certificates are now verified.** 1.x silently skipped verification, which let anyone in the network path impersonate the server. Pass `verify_ssl=False` only if you really need the old behaviour for hosts you control.
- **Every failed request is logged** to the `reqt` logger instead of only connection errors; other errors no longer abort the whole batch.

The 2.0 equivalent of the example above is:

```python
results = await reqt.fetch_all(urls)
for result in results:
    print(result.status)
```

The 1.x style will be removed in reqt 3.0.

## Development

```console
$ python -m pip install -e ".[test,lint]"
$ pytest
$ ruff check . && ruff format --check . && mypy reqt
```

The tests run against a local server and need no network access.

## License

MIT, see [LICENSE](LICENSE).
