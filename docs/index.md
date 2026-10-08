---
hide:
  - navigation
  - toc
---

<div class="rs-hero" markdown>

# reqstorm

<p class="rs-tagline">Send thousands of HTTP requests without writing the plumbing. Rate limits, retries, progress, and results straight to a file or a database, with one result per request.</p>

[Get started](getting-started.md){ .md-button .md-button--primary }
[View on GitHub](https://github.com/melihcolpan/reqstorm){ .md-button }

</div>

```console
$ python -m pip install reqstorm
```

```python
import reqstorm

urls = [f"https://api.example.com/items/{i}" for i in range(7000)]

print(reqstorm.estimate(len(urls), rate_limit="100/min"))
# 7000 requests: about 1h 10m (limited by rate_limit)

results = reqstorm.fetch_all_sync(urls, rate_limit="100/min", retries=2, progress=True)
print(results.summary())
# {'total': 7000, 'ok': 6987, 'failed': 13, 'failures': {'HTTP 404': 9, 'TimeoutError': 4}}
```

<div class="grid cards" markdown>

-   :material-check-all:{ .lg .middle } **One result per request**

    ---

    A timeout or a dropped connection is recorded on that request and never stops the others. Every attempt is kept in its history.

    [:octicons-arrow-right-24: Results and reports](guide/results.md)

-   :material-speedometer:{ .lg .middle } **Rate limits in any unit**

    ---

    `"10/s"`, `"100/min"`, `"1000/h"`, per host, plus overall and per-host concurrency. Know how long a batch takes before you start it.

    [:octicons-arrow-right-24: Rate limits](guide/limits.md)

-   :material-refresh:{ .lg .middle } **Retries that make sense**

    ---

    Immediate retries with backoff and `Retry-After`, and retry rounds that resend what still failed at the end. 404s are not retried.

    [:octicons-arrow-right-24: Timeouts and retries](guide/retries.md)

-   :material-database-arrow-down:{ .lg .middle } **Straight to a file or a database**

    ---

    JSONL, CSV, SQLite, PostgreSQL or MySQL, written as results arrive, so millions of results never fill your memory. Resume an interrupted run.

    [:octicons-arrow-right-24: Files](guide/files.md) · [Databases](guide/databases.md)

-   :material-language-python:{ .lg .middle } **With or without asyncio**

    ---

    Blocking functions for scripts and Jupyter notebooks, async functions for code that already runs an event loop. Same options for both.

    [:octicons-arrow-right-24: Getting started](getting-started.md)

-   :material-shield-check:{ .lg .middle } **Safe defaults**

    ---

    TLS certificates verified, timeouts on by default, bounded concurrency. Fully typed, one dependency: aiohttp.

    [:octicons-arrow-right-24: Requests and TLS](guide/requests.md)

</div>
