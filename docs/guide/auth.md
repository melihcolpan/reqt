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

Each attempt takes the next proxy from the pool, so a retry goes through a different proxy. HTTP proxies work for HTTPS URLs too (through `CONNECT`).

### SOCKS proxies

SOCKS4 and SOCKS5 proxies, such as Tor or an SSH tunnel (`ssh -D 1080 host`), need the optional `aiohttp-socks` package:

```console
$ python -m pip install "reqstorm[socks]"
```

```python
reqstorm.fetch_all_sync(urls, proxy="socks5h://127.0.0.1:9050")  # Tor

reqstorm.fetch_all_sync(urls, proxy="socks5://user:secret@proxy.example.com:1080")
```

| Scheme | Version | Host names are resolved by |
|---|---|---|
| `socks5h://` | SOCKS5 | the proxy |
| `socks5://` | SOCKS5 | your machine |
| `socks4a://` | SOCKS4 | the proxy |
| `socks4://` | SOCKS4 | your machine |

The "h" and "a" variants send the host name to the proxy, as curl does. Use them when the names only resolve on the proxy's side (Tor `.onion` addresses, an internal network) or when your own DNS lookups should not reveal which hosts you contact.

- Username and password go in the URL. Percent-encode special characters: `p@ss` becomes `p%40ss`.
- The port defaults to 1080.
- SOCKS and HTTP proxies can be mixed in one pool, and `Request(proxy="socks5://...")` works per request.
- HTTPS works through SOCKS, and the certificate is still verified against the real host.
- Each SOCKS proxy gets its own connection pool, with the same `concurrency` and `concurrency_per_host` limits, and connections are kept alive between requests.
- SOCKS proxies cannot be combined with `session=`, because reqstorm opens the session for each proxy itself.

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
