"""reqstorm: send large numbers of HTTP requests concurrently with asyncio.

import asyncio
import reqstorm

async def main():
    results = await reqstorm.fetch_all(["https://example.com", "https://example.org"])
    for result in results:
        print(result.url, result.status if result.ok else result.error)

asyncio.run(main())
"""

from ._auth import BearerAuth
from ._cache import Cache
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
from ._paginate import Cursor, LinkHeader, NextLink, PageNumber, Paginator
from ._plan import Estimate, estimate
from ._schema import Field, Schema, SchemaError, extract, infer_schema
from ._sync import fetch_all_sync, fetch_to_db_sync, fetch_to_file_sync, stream_sync
from ._template import from_template, read_csv, read_sql

__version__ = "2.3.0"

__all__ = [
    "Attempt",
    "BearerAuth",
    "Cache",
    "Cursor",
    "DEFAULT_RETRY_STATUSES",
    "Estimate",
    "Field",
    "HTTPStatusError",
    "LinkHeader",
    "NextLink",
    "PageNumber",
    "Paginator",
    "Reqt",
    "Request",
    "Result",
    "Results",
    "Schema",
    "SchemaError",
    "Summary",
    "estimate",
    "extract",
    "fetch_all",
    "fetch_all_sync",
    "fetch_to_db",
    "fetch_to_db_sync",
    "fetch_to_file",
    "fetch_to_file_sync",
    "from_template",
    "infer_schema",
    "parse_rate",
    "read_csv",
    "read_sql",
    "stream",
    "stream_sync",
    "__version__",
]
