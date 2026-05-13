"""Process-local cache for active conversation summaries.

Neon remains the durable source of truth. This cache only avoids repeated
summary SELECTs for active sessions while the backend process is warm.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256

from cafe.config import Settings, get_settings


@dataclass(frozen=True)
class SummaryCacheLookup:
    """Result of a summary cache lookup."""

    found: bool
    summary: str = ""


@dataclass
class _SummaryCacheEntry:
    summary: str
    expires_at: float | None


_CACHE: OrderedDict[str, _SummaryCacheEntry] = OrderedDict()
_LOCK = asyncio.Lock()


def _cache_key(user_id: str, session_id: str) -> str:
    """Return a stable non-PII summary cache key.

    Args:
        - user_id: str - The user id value.
        - session_id: str - The session id value.

    Returns:
        - return str - The cache key.
    """
    return sha256(f"{user_id}\0{session_id}".encode("utf-8")).hexdigest()


def _expires_at(settings: Settings) -> float | None:
    """Return the expiry timestamp for new entries.

    Args:
        - settings: Settings - The settings value.

    Returns:
        - return float | None - The expiry timestamp.
    """
    ttl = settings.memory_summary_cache_ttl_seconds
    return time.time() + ttl if ttl > 0 else None


def _is_expired(entry: _SummaryCacheEntry) -> bool:
    """Return whether a cache entry has expired.

    Args:
        - entry: _SummaryCacheEntry - The cache entry.

    Returns:
        - return bool - Whether the entry is expired.
    """
    return entry.expires_at is not None and entry.expires_at <= time.time()


def _evict_over_limit(settings: Settings) -> None:
    """Remove least-recently-used entries above the configured cap.

    Args:
        - settings: Settings - The settings value.

    Returns:
        - return None - This function has no return value.
    """
    limit = max(settings.memory_summary_cache_max_entries, 1)
    while len(_CACHE) > limit:
        _CACHE.popitem(last=False)


async def get_cached_summary(
    user_id: str,
    session_id: str,
) -> SummaryCacheLookup:
    """Return a cached summary, including cached empty-summary misses.

    Args:
        - user_id: str - The user id value.
        - session_id: str - The session id value.

    Returns:
        - return SummaryCacheLookup - The lookup result.
    """
    key = _cache_key(user_id, session_id)
    async with _LOCK:
        entry = _CACHE.get(key)
        if entry is None:
            return SummaryCacheLookup(found=False)
        if _is_expired(entry):
            _CACHE.pop(key, None)
            return SummaryCacheLookup(found=False)
        _CACHE.move_to_end(key)
        return SummaryCacheLookup(found=True, summary=entry.summary)


async def set_cached_summary(
    user_id: str,
    session_id: str,
    summary: str,
    settings: Settings | None = None,
) -> None:
    """Store a summary for a warm active session.

    Args:
        - user_id: str - The user id value.
        - session_id: str - The session id value.
        - summary: str - The summary text. Empty strings are cached too.
        - settings: Settings | None - Optional settings override.

    Returns:
        - return None - This function has no return value.
    """
    settings = settings or get_settings()
    key = _cache_key(user_id, session_id)
    async with _LOCK:
        _CACHE[key] = _SummaryCacheEntry(
            summary=summary,
            expires_at=_expires_at(settings),
        )
        _CACHE.move_to_end(key)
        _evict_over_limit(settings)


async def delete_cached_summary(user_id: str, session_id: str) -> None:
    """Delete one cached summary.

    Args:
        - user_id: str - The user id value.
        - session_id: str - The session id value.

    Returns:
        - return None - This function has no return value.
    """
    async with _LOCK:
        _CACHE.pop(_cache_key(user_id, session_id), None)


async def clear_summary_cache() -> None:
    """Clear all cached summaries.

    Returns:
        - return None - This function has no return value.
    """
    async with _LOCK:
        _CACHE.clear()


def clear_summary_cache_sync() -> None:
    """Clear all cached summaries from synchronous reset hooks.

    Returns:
        - return None - This function has no return value.
    """
    _CACHE.clear()
