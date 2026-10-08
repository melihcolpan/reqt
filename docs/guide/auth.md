# Tokens, proxies and caching

## Tokens that expire

A long run can outlive its access token. `BearerAuth` sends the token and, when a response is `401 Unauthorized`, asks your function for a new one and sends that request again, once:

```python
def get_token() -> str:
    response = httpx.post("https://auth.example.com/token", data={...})
    return response.json()["access_token"]

auth = reqstorm.BearerAuth(refresh=get_token)
results = reqstorm.fetch_all_sync(urls, auth=auth)
print(auth.refreshes)  # how many times the token was renewed
```

- Without a starting token, `refresh` is called before the first request.
- When many requests get a 401 at the same moment, they share a single refresh.
- `refresh` may be a coroutine function, so it can use `aiohttp` or `httpx.AsyncClient`.
- A second 401 for the same request is returned as the result rather than retried again, so a revoked credential cannot cause a loop.
- `header` and `scheme` change where the token goes: `BearerAuth(key, header="X-Api-Key", scheme="")` sends `X-Api-Key: <key>`.

Without `refresh`, a 401 is simply a failed result.

## Proxies

```python
reqstorm.fetch_all_sync(urls, proxy="http://user:pass@proxy.example.com:8080")

# A pool: each request uses the next proxy in turn
reqstorm.fetch_all_sync(urls, proxy=[
    "http://proxy-1.example.com:8080",
    "http://proxy-2.example.com:8080",
])

# One request through a specific proxy
reqstorm.Request("https://api.example.com/eu-only", proxy="http://eu.proxy.example.com:8080")
```

Each attempt takes the next proxy from the pool, so a retry goes through a different proxy. HTTP proxies are supported, also for HTTPS URLs (through `CONNECT`).

## Caching and conditional requests

`Cache` keeps successful GET responses in an SQLite file. On the next run, reqstorm sends `If-None-Match` (with the stored `ETag`) and `If-Modified-Since`; when the server answers `304 Not Modified`, the stored response is used and no body is downloaded:

```python
with reqstorm.Cache("responses.sqlite") as cache:
    results = reqstorm.fetch_all_sync(urls, cache=cache)
    print(cache.revalidated, "unchanged")
```

With `ttl`, a response younger than that many seconds is used without asking the server at all:

```python
cache = reqstorm.Cache("responses.sqlite", ttl=3600)  # one hour
```

- Results served from the cache have `from_cache=True`; their `history` shows the 304, or is empty when no request was made.
- Only `GET` and `HEAD` responses with status 200 are stored.
- The cache key is the method, the URL, the query parameters and the `Accept`, `Accept-Language` and `Authorization` headers.
- `cache.clear()` empties the cache; `cache.close()` (or leaving the `with` block) closes the file.
