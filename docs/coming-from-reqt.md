# Coming from reqt

reqstorm is the next version of **reqt**, renamed because another project already uses the reqt name.

```console
$ python -m pip install reqstorm
```

Then replace `import reqt` with `import reqstorm`. The `reqt` package on PyPI now only installs reqstorm and re-exports it with a deprecation warning, so existing code keeps working while you switch.

## The reqt 1.x calling style

reqt 1.x called a function with each raw response and returned nothing:

```python
async def handle(response):   # reqt 1.x
    print(response.status)

await reqstorm.fetch_all(urls=urls, method=handle)
```

This still works, with a `DeprecationWarning`, and will be removed in reqstorm 3.0. The equivalent today is:

```python
results = reqstorm.fetch_all_sync(urls)
for result in results:
    print(result.status)
```

Two things changed even in the 1.x style:

!!! warning "TLS certificates are now verified"
    reqt 1.x silently skipped certificate verification, which let anyone in the network path impersonate the server. Pass `verify_ssl=False` only if you need the old behaviour for hosts you control.

!!! note "Failures no longer stop the batch"
    Every failed request is logged to the `reqstorm` logger. In reqt 1.x, any error other than a connection error was raised from `fetch_all` and the remaining results were lost.
