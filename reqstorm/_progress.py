"""Formatting helpers for durations (the progress line itself is in _observe)."""

from __future__ import annotations


def format_duration(seconds: float) -> str:
    """``42s``, ``3m 05s``, ``1h 10m``."""
    seconds = max(int(round(seconds)), 0)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"
