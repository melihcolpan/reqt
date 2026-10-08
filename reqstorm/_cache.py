"""A persistent response cache with conditional requests (ETag / Last-Modified)."""

from __future__ import annotations

import hashlib
import json as jsonlib
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from multidict import CIMultiDict, CIMultiDictProxy

__all__ = ["Cache"]

_CACHEABLE_METHODS = ("GET", "HEAD")


@dataclass(frozen=True)
class CachedResponse:
    status: int
    headers: CIMultiDictProxy
    body: bytes
    final_url: Optional[str]
    stored_at: float

    @property
    def validators(self) -> Dict[str, str]:
        """Conditional request headers that ask the server to answer 304 if nothing changed."""
        conditional: Dict[str, str] = {}
        if "ETag" in self.headers:
            conditional["If-None-Match"] = self.headers["ETag"]
        if "Last-Modified" in self.headers:
            conditional["If-Modified-Since"] = self.headers["Last-Modified"]
        return conditional


class Cache:
    """Keep successful GET responses on disk and reuse them on the next run.

    Args:
        path: SQLite file to keep the responses in (created if missing).
        ttl: Seconds a cached response is used without asking the server. ``None`` (the
            default) always asks, but with ``If-None-Match`` / ``If-Modified-Since``, so an
            unchanged resource costs a 304 with no body. ``0`` behaves the same.

    Only GET and HEAD responses with status 200 are cached. A result served from the cache
    has ``from_cache=True``.

        cache = reqstorm.Cache("responses.sqlite", ttl=3600)
        reqstorm.fetch_all_sync(urls, cache=cache)
    """

    def __init__(
        self, path: str | os.PathLike[str] = "reqstorm-cache.sqlite", ttl: Optional[float] = None
    ) -> None:
        if ttl is not None and ttl < 0:
            raise ValueError("ttl must not be negative")
        self.path = os.fspath(path)
        self.ttl = ttl
        self.hits = 0
        self.revalidated = 0
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS responses (key TEXT PRIMARY KEY, status INTEGER, headers TEXT, "
            "body BLOB, final_url TEXT, stored_at REAL)"
        )
        self._connection.commit()

    @staticmethod
    def key(
        method: str, url: str, params: Optional[Mapping[str, Any]], headers: Optional[Mapping[str, str]]
    ) -> Optional[str]:
        if method.upper() not in _CACHEABLE_METHODS:
            return None
        parts = {
            "method": method.upper(),
            "url": url,
            "params": sorted((str(k), str(v)) for k, v in (params or {}).items()),
            # Responses can differ by these request headers, so they are part of the key
            "vary": sorted((k.lower(), v) for k, v in (headers or {}).items()
                           if k.lower() in ("accept", "accept-language", "authorization")),
        }  # fmt: skip
        return hashlib.sha256(jsonlib.dumps(parts).encode()).hexdigest()

    def get(self, key: str) -> Optional[CachedResponse]:
        with self._lock:
            row = self._connection.execute(
                "SELECT status, headers, body, final_url, stored_at FROM responses WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        headers = CIMultiDictProxy(CIMultiDict(jsonlib.loads(row[1])))
        return CachedResponse(row[0], headers, row[2], row[3], row[4])

    def is_fresh(self, entry: CachedResponse) -> bool:
        return bool(self.ttl) and time.time() - entry.stored_at < self.ttl  # type: ignore[operator]

    def put(
        self, key: str, status: int, headers: Mapping[str, str], body: bytes, final_url: Optional[str]
    ) -> None:
        if status != 200:
            return
        with self._lock:
            self._connection.execute(
                "INSERT OR REPLACE INTO responses (key, status, headers, body, final_url, stored_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (key, status, jsonlib.dumps(list(headers.items())), body, final_url, time.time()),
            )
            self._connection.commit()

    def touch(self, key: str) -> None:
        """Mark a revalidated response as fresh again."""
        with self._lock:
            self._connection.execute("UPDATE responses SET stored_at = ? WHERE key = ?", (time.time(), key))
            self._connection.commit()

    def clear(self) -> None:
        with self._lock:
            self._connection.execute("DELETE FROM responses")
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __bool__(self) -> bool:
        return True  # an empty cache is still a cache

    def __len__(self) -> int:
        with self._lock:
            return int(self._connection.execute("SELECT count(*) FROM responses").fetchone()[0])
