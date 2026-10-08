# Requests, streaming and TLS

## Methods, headers and bodies

Options given to `fetch_all` apply to every request. Use `reqstorm.Request` to vary them per request:

```python
results = reqstorm.fetch_all_sync(
    [
        "https://api.example.com/items/1",
        reqstorm.Request("https://api.example.com/items", method="POST", json={"name": "new"}),
        reqstorm.Request("https://api.example.com/search", params={"q": "storm"}),
    ],
    headers={"Authorization": "Bearer ..."},   # merged with each Request's own headers
)
```

`fetch_all_sync(urls, "POST", json=...)` sends every plain URL as a POST. `params`, `json` and `data` work the same way. A `Request`'s own headers are merged on top of the shared ones; its other fields replace the shared value.

## Streaming results

`stream` (or `stream_sync`) yields each result as soon as it completes. Your URLs can come from a generator, which is read lazily, so millions of URLs never need to be in memory at once:

=== "Script or notebook"

    ```python
    def read_urls():
        with open("urls.txt") as file:
            for line in file:
                yield line.strip()

    for result in reqstorm.stream_sync(read_urls(), concurrency=100):
        save(result.url, result.status, result.body)
    ```

=== "async code"

    ```python
    async for result in reqstorm.stream(read_urls(), concurrency=100):
        await save(result.url, result.status, result.body)
    ```

Leaving the loop early (`break`) cancels the requests still in flight. `fetch_all` also accepts `callback=`, a function or coroutine function called with each result as it completes.

## TLS

Certificates are verified by default. To trust a private certificate authority, pass an `ssl.SSLContext`:

```python
import ssl

context = ssl.create_default_context(cafile="internal-ca.pem")
results = reqstorm.fetch_all_sync(urls, ssl=context)
```

`verify_ssl=False` turns verification off. Only use it for hosts you control: without verification, anyone in the network path can impersonate the server.

## Your own session

In async code, pass an existing `aiohttp.ClientSession` with `session=` to share cookies, connection pools or proxy settings. reqstorm does not close it. The blocking functions do not accept `session`, because a session belongs to one event loop.
