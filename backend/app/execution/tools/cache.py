"""Small process-local TTL cache for idempotent research tool calls."""

from __future__ import annotations

import os
import threading
import time
from typing import Any


_CACHE: dict[str, tuple[float, Any]] = {}
_LOCK = threading.Lock()


def _ttl_seconds() -> float:
    try:
        return max(float(os.getenv("AGENTFLOW_TOOL_CACHE_TTL_SECONDS", "0")), 0.0)
    except ValueError:
        return 0.0


def get_cached(key: str) -> Any | None:
    ttl = _ttl_seconds()
    if ttl <= 0:
        return None
    now = time.monotonic()
    with _LOCK:
        cached = _CACHE.get(key)
        if cached is None:
            return None
        created_at, value = cached
        if now - created_at >= ttl:
            _CACHE.pop(key, None)
            return None
        return value


def set_cached(key: str, value: Any) -> None:
    if _ttl_seconds() <= 0:
        return
    with _LOCK:
        _CACHE[key] = (time.monotonic(), value)


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()
