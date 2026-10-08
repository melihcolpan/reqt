"""reqt: send large numbers of HTTP requests concurrently with asyncio.

import asyncio
import reqt

async def main():
    results = await reqt.fetch_all(["https://example.com", "https://example.org"])
    for result in results:
        print(result.url, result.status if result.ok else result.error)

asyncio.run(main())
"""

from ._client import (
    DEFAULT_RETRY_STATUSES,
    Attempt,
    HTTPStatusError,
    Request,
    Result,
    Results,
    fetch_all,
    stream,
)
from ._files import Summary, fetch_to_db, fetch_to_file
from ._legacy import Reqt
from ._limits import parse_rate
from ._plan import Estimate, estimate
from ._sync import fetch_all_sync, fetch_to_db_sync, fetch_to_file_sync, stream_sync

__version__ = "2.0.0"

__all__ = [
    "DEFAULT_RETRY_STATUSES",
    "Attempt",
    "Estimate",
    "HTTPStatusError",
    "Request",
    "Reqt",
    "Result",
    "Results",
    "Summary",
    "estimate",
    "fetch_all",
    "fetch_all_sync",
    "fetch_to_db",
    "fetch_to_db_sync",
    "fetch_to_file",
    "fetch_to_file_sync",
    "stream",
    "parse_rate",
    "stream_sync",
    "__version__",
]
