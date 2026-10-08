"""Follow paginated APIs: next links, Link headers, cursors and page numbers."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Dict, Optional

from yarl import URL

if TYPE_CHECKING:
    from ._client import Request, Result

__all__ = ["Cursor", "LinkHeader", "NextLink", "PageNumber", "Paginator"]

_LINK_NEXT = re.compile(r'<([^>]*)>\s*;[^,]*\brel="?([^",;]*)"?', re.I)


def _value(result: Result, path: str) -> Any:
    """The value at ``path`` in the result's JSON body, or None."""
    from ._schema import _MISSING, _get, _parse_path, parse_json  # _schema imports _client

    document, problem = parse_json(result.body)
    if problem is not None:
        return None
    value = _get(document, _parse_path(path)[1])
    return None if value is _MISSING else value


def _with_param(request: Request, name: str, value: Any) -> Request:
    """The request with query parameter ``name`` set to ``value``, replacing any earlier value."""
    url = URL(request.url)
    url = url.with_query({key: val for key, val in url.query.items() if key != name})
    params: Dict[str, Any] = {key: val for key, val in (request.params or {}).items() if key != name}
    params[name] = value
    return replace(request, url=str(url), params=params)


class Paginator:
    """Base class: given a page's request and result, return the next page's request or None."""

    max_pages: int = 1000

    def next(self, request: Request, result: Result, page: int) -> Optional[Request]:
        raise NotImplementedError


@dataclass(frozen=True)
class NextLink(Paginator):
    """The response JSON holds the next page's URL, for example ``{"links": {"next": "..."}}``.

    Args:
        path: Dotted path to the URL (``"links.next"``, ``"next"``, ``"paging.next_url"``).
            A relative URL is resolved against the current page.
        max_pages: Stop after this many pages per starting URL.
    """

    path: str = "next"
    max_pages: int = 1000

    def next(self, request: Request, result: Result, page: int) -> Optional[Request]:
        target = _value(result, self.path)
        if not isinstance(target, str) or not target.strip():
            return None
        url = str(URL(result.final_url or request.url).join(URL(target.strip())))
        return replace(request, url=url, params=None)


@dataclass(frozen=True)
class LinkHeader(Paginator):
    """The next page is in the ``Link`` response header (RFC 8288), as GitHub and many APIs do.

    Args:
        rel: The relation to follow (default ``"next"``).
        max_pages: Stop after this many pages per starting URL.
    """

    rel: str = "next"
    max_pages: int = 1000

    def next(self, request: Request, result: Result, page: int) -> Optional[Request]:
        headers = result.headers
        values = headers.getall("Link", []) if hasattr(headers, "getall") else [headers.get("Link", "")]
        for header in values:
            for target, rel in _LINK_NEXT.findall(header or ""):
                if self.rel in rel.split():
                    url = str(URL(result.final_url or request.url).join(URL(target)))
                    return replace(request, url=url, params=None)
        return None


@dataclass(frozen=True)
class Cursor(Paginator):
    """The response JSON holds a cursor, sent back as a query parameter for the next page.

    Args:
        path: Dotted path to the cursor in the response (``"meta.next_cursor"``).
        param: Query parameter to send it in (``"cursor"``).
        max_pages: Stop after this many pages per starting URL.

    Pagination stops when the cursor is missing, null or an empty string.
    """

    path: str
    param: str = "cursor"
    max_pages: int = 1000

    def next(self, request: Request, result: Result, page: int) -> Optional[Request]:
        cursor = _value(result, self.path)
        if cursor is None or cursor == "" or isinstance(cursor, (dict, list)):
            return None
        return _with_param(request, self.param, cursor)


@dataclass(frozen=True)
class PageNumber(Paginator):
    """Request ``?page=1``, ``?page=2``, ... until a page comes back empty.

    Args:
        param: Query parameter holding the page number (``"page"``).
        start: Number of the first page, used when the starting URL has no such parameter.
        items: Dotted path to the array of results; pagination stops when it is empty or
            missing. Without it, it stops at the first non-2xx response.
        max_pages: Stop after this many pages per starting URL.
    """

    param: str = "page"
    start: int = 1
    items: Optional[str] = None
    max_pages: int = 1000

    def next(self, request: Request, result: Result, page: int) -> Optional[Request]:
        if self.items is not None:
            array = _value(result, self.items)
            if not isinstance(array, list) or not array:
                return None
        current = (request.params or {}).get(self.param)
        if current is None:
            current = URL(request.url).query.get(self.param)
        try:
            number = int(current) if current is not None else self.start
        except (TypeError, ValueError):
            return None
        return _with_param(request, self.param, number + 1)
