# Reqt

**reqt** is a simple, asynchronous library that you can use to send large numbers of requests.

```python
import asyncio
import reqt

async def custom_method(response):
    print(f"Status Code: {response.status}")
    print(f"Response: {response.content}")

async def main():
    urls = ["https://www.google.com"]
    await reqt.fetch_all(urls=urls, method=custom_method)

loop = asyncio.get_event_loop()
loop.run_until_complete(main())

```


Requests allows you to send HTTP/1.1 requests extremely easily. Just give `url addresses` and process your results. It continues to be developed with `new features` every day!

[![Version](https://img.shields.io/badge/reqt-v1.0.2-green)](https://pypi.org/project/reqt)

## Installing Reqt and Supported Versions

Reqt is available on PyPI:

```console
$ python -m pip install reqt
```

Reqt officially supports Python 3+.