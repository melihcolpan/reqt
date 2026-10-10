"""Keep the number of open connections within the process's open file limit."""

from __future__ import annotations

import sys
from typing import Callable, Optional, Tuple

# File descriptors kept free for everything else: log and output files, the cache,
# DNS lookups, the interpreter itself
RESERVED = 64

Notify = Callable[[int, str], None]  # (log level, message)


def _limits() -> Optional[Tuple[int, int]]:
    if sys.platform == "win32":
        return None  # no RLIMIT_NOFILE; Windows sockets are not bound by it
    try:
        import resource
    except ImportError:
        return None
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    return soft, hard


def _raise_to(target: int, hard: int) -> int:
    """Raise the soft limit towards ``target``; returns the soft limit now in effect."""
    import resource

    soft = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    candidates = [target]
    if sys.platform == "darwin":
        candidates.append(10240)  # macOS refuses more than OPEN_MAX for many processes
    for candidate in candidates:
        if hard != resource.RLIM_INFINITY:
            candidate = min(candidate, hard)
        if candidate <= soft:
            continue
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (candidate, hard))
            return candidate
        except (ValueError, OSError):
            continue
    return soft


def fit_concurrency(concurrency: int, pools: int, notify: Notify) -> int:
    """The concurrency to use so that connections never run out of file descriptors.

    Each connection is an open file. ``pools`` is the number of connection pools that may
    each hold ``concurrency`` connections (the main session plus one per SOCKS proxy).
    The soft limit is raised as far as the system allows; if that is still not enough,
    the concurrency is lowered and ``notify`` explains why.
    """
    limits = _limits()
    if limits is None:
        return concurrency
    soft, hard = limits
    needed = concurrency * max(pools, 1) + RESERVED
    if needed <= soft:
        return concurrency
    import logging

    available = _raise_to(needed, hard)
    if available >= needed:
        notify(
            logging.INFO,
            f"raised the open file limit from {soft} to {available} for concurrency {concurrency}",
        )
        return concurrency
    fitted = max(1, (available - RESERVED) // max(pools, 1))
    notify(
        logging.WARNING,
        f"concurrency lowered from {concurrency} to {fitted}: the open file limit is {available} "
        f"and each connection needs one. Raise it with `ulimit -n {needed}` to use {concurrency}.",
    )
    return fitted
