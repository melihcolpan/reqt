"""SOCKS4 and SOCKS5 proxies, through the optional aiohttp-socks package."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple
from urllib.parse import unquote, urlsplit

import aiohttp

SCHEMES = ("socks4", "socks4a", "socks5", "socks5h")
_MISSING = "SOCKS proxies need the aiohttp-socks package: pip install 'reqstorm[socks]'"


def is_socks(proxy: Optional[str]) -> bool:
    return proxy is not None and urlsplit(proxy).scheme.lower() in SCHEMES


def check_available() -> None:
    try:
        import aiohttp_socks  # noqa: F401
    except ImportError:
        raise ImportError(_MISSING) from None


def parse(proxy: str) -> Tuple[str, str, int, Optional[str], Optional[str], bool]:
    """(version, host, port, username, password, remote_dns) of a SOCKS proxy URL.

    As with curl, ``socks5h://`` and ``socks4a://`` let the proxy resolve host names, while
    ``socks5://`` and ``socks4://`` resolve them locally.
    """
    parts = urlsplit(proxy)
    scheme = parts.scheme.lower()
    if scheme not in SCHEMES:
        raise ValueError(f"not a SOCKS proxy URL: {proxy!r}")
    if not parts.hostname:
        raise ValueError(f"SOCKS proxy URL without a host: {proxy!r}")
    try:
        port = parts.port or 1080
    except ValueError:
        raise ValueError(f"invalid port in SOCKS proxy URL {proxy!r}") from None
    username = unquote(parts.username) if parts.username is not None else None
    password = unquote(parts.password) if parts.password is not None else None
    version = "socks5" if scheme.startswith("socks5") else "socks4"
    return version, parts.hostname, port, username, password, scheme in ("socks5h", "socks4a")


class SocksSessions:
    """One aiohttp session per SOCKS proxy, created on first use and closed together.

    A SOCKS proxy is a property of the connection, not of the request, so each proxy needs
    its own connector. The connection limits match those of the main session.
    """

    def __init__(self, limit: int, limit_per_host: int) -> None:
        self._limit = limit
        self._limit_per_host = limit_per_host
        self._sessions: Dict[str, aiohttp.ClientSession] = {}

    def session(self, proxy: str) -> aiohttp.ClientSession:
        existing = self._sessions.get(proxy)
        if existing is not None:
            return existing
        check_available()
        from aiohttp_socks import ProxyConnector, ProxyType

        version, host, port, username, password, remote_dns = parse(proxy)
        connector: Any = ProxyConnector(
            proxy_type=ProxyType.SOCKS5 if version == "socks5" else ProxyType.SOCKS4,
            host=host,
            port=port,
            username=username,
            password=password,
            rdns=remote_dns,
            limit=self._limit,
            limit_per_host=self._limit_per_host,
        )
        created = aiohttp.ClientSession(connector=connector)
        self._sessions[proxy] = created
        return created

    async def close(self) -> None:
        for session in self._sessions.values():
            await session.close()
        self._sessions.clear()
