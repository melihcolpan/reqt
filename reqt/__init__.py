"""reqt: send large numbers of HTTP requests concurrently with asyncio.

import asyncio
import reqt

async def main():
    results = await reqt.fetch_all(["https://example.com", "https://example.org"])
    for result in results:
        print(result.url, result.status if result.ok else result.error)

asyncio.run(main())
"""

from ._client import DEFAULT_RETRY_STATUSES, HTTPStatusError, Request, Result, fetch_all, stream
from ._legacy import Reqt

__version__ = "2.0.0"

__all__ = [
    "DEFAULT_RETRY_STATUSES",
    "HTTPStatusError",
    "Request",
    "Reqt",
    "Result",
    "fetch_all",
    "stream",
    "__version__",
]
