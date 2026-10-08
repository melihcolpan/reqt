"""Authentication that refreshes an expired token during a long run."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Awaitable, Callable, Dict, Optional, Union

__all__ = ["BearerAuth"]

TokenSource = Callable[[], Union[str, Awaitable[str]]]


class BearerAuth:
    """Send ``Authorization: Bearer <token>``, and get a new token when the server says it expired.

        auth = reqstorm.BearerAuth(refresh=get_token)
        reqstorm.fetch_all_sync(urls, auth=auth)

    Args:
        token: The current token. Leave it out to fetch one with ``refresh`` before the
            first request.
        refresh: A function or coroutine function that returns a new token. It is called
            when a response is 401 Unauthorized; the request is then sent again once with
            the new token. Concurrent 401s share a single refresh.
        header: Header to send the token in (default ``Authorization``).
        scheme: Prefix before the token (default ``Bearer``); ``""`` sends the token alone.
    """

    def __init__(
        self,
        token: Optional[str] = None,
        *,
        refresh: Optional[TokenSource] = None,
        header: str = "Authorization",
        scheme: str = "Bearer",
    ) -> None:
        if token is None and refresh is None:
            raise ValueError("BearerAuth needs a token, a refresh function, or both")
        self.token = token
        self._refresh = refresh
        self.header = header
        self.scheme = scheme
        self.refreshes = 0
        self._generation = 0
        self._lock: Optional[asyncio.Lock] = None

    @property
    def can_refresh(self) -> bool:
        return self._refresh is not None

    async def headers(self) -> Dict[str, str]:
        if self.token is None:
            await self.refresh(self._generation)
        value = f"{self.scheme} {self.token}" if self.scheme else str(self.token)
        return {self.header: value}

    @property
    def generation(self) -> int:
        """Changes each time the token is refreshed."""
        return self._generation

    async def refresh(self, seen_generation: int) -> None:
        """Get a new token, unless another request already did since ``seen_generation``."""
        if self._refresh is None:
            return
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self._generation != seen_generation and self.token is not None:
                return  # refreshed by a concurrent request while we waited
            token: Any = self._refresh()
            if inspect.isawaitable(token):
                token = await token
            if not isinstance(token, str) or not token:
                raise ValueError("the refresh function must return a non-empty token string")
            self.token = token
            self._generation += 1
            self.refreshes += 1
