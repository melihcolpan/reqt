# Changelog

## 2.0.0

### Added
- `fetch_all` returns a `Result` per request, in input order, with `status`, `headers`, `body`, `text()`, `json()`, `error`, `attempts` and `elapsed`.
- `reqt.stream` yields results as they complete and consumes its input lazily, so generators of millions of URLs work.
- Per-attempt `timeout`, `retries` with exponential `backoff`, `Retry-After` support and configurable `retry_statuses`.
- `reqt.Request` for per-request method, headers, params, JSON or form data; `headers`, `params`, `json` and `data` defaults on `fetch_all`.
- `concurrency`, `callback`, `ssl` (custom `SSLContext`) and `session` options.
- `Result.raise_for_error()` and `reqt.HTTPStatusError`.
- Type hints (`py.typed`), support for Python 3.9 to 3.13, and a test suite that runs against a local server.

### Changed
- **TLS certificates are verified.** 1.x disabled verification for every request. Use `verify_ssl=False` to opt out for hosts you control.
- A failing request no longer stops the batch. In 1.x, any error other than a connection error was raised from `fetch_all` and the remaining results were lost.
- IPv6 hosts are reachable; 1.x forced IPv4.
- Packaging moved to `pyproject.toml`, and the project is licensed under MIT (the 1.x license file was empty).

### Deprecated
- Passing a callback as `method` (`fetch_all(urls=..., method=callback)`), the `request_type` and `semaphore_limit` arguments, and the `Reqt` class. They still work with a `DeprecationWarning` and will be removed in 3.0.

### Removed
- `reqt.helpers`, an internal module.

## 1.0.3 (2021-01-17)
- Last 1.x release.
