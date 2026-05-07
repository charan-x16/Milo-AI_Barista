"""Small cache abstraction for hot-path deterministic responses.

The app uses the in-process cache by default. If REDIS_URL is configured and
redis-py is installed, startup can swap in Redis for multi-instance deployments.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Protocol


class CacheBackend(Protocol):
    async def get(self, key: str) -> Any | None:
        ...

    async def set(self, key: str, value: Any, *, ttl_seconds: int | None = None) -> None:
        ...

    async def clear(self) -> None:
        ...


@dataclass
class _CacheEntry:
    value: Any
    expires_at: float | None


class InMemoryCache:
    def __init__(self) -> None:
        self._values: dict[str, _CacheEntry] = {}

    async def get(self, key: str) -> Any | None:
        entry = self._values.get(key)
        if entry is None:
            return None
        if entry.expires_at is not None and entry.expires_at <= time.time():
            self._values.pop(key, None)
            return None
        return entry.value

    async def set(self, key: str, value: Any, *, ttl_seconds: int | None = None) -> None:
        expires_at = time.time() + ttl_seconds if ttl_seconds else None
        self._values[key] = _CacheEntry(value=value, expires_at=expires_at)

    async def clear(self) -> None:
        self._values.clear()


class RedisCache:
    def __init__(self, redis_url: str) -> None:
        from redis.asyncio import Redis

        self._client = Redis.from_url(redis_url, decode_responses=True)

    async def get(self, key: str) -> Any | None:
        value = await self._client.get(key)
        if value is None:
            return None
        return json.loads(value)

    async def set(self, key: str, value: Any, *, ttl_seconds: int | None = None) -> None:
        payload = json.dumps(value, ensure_ascii=False, default=str)
        await self._client.set(key, payload, ex=ttl_seconds)

    async def clear(self) -> None:
        # Namespaced keys are intentionally simple for this app cache.
        keys = [key async for key in self._client.scan_iter(match="milo:*")]
        if keys:
            await self._client.delete(*keys)


_cache: CacheBackend = InMemoryCache()


def configure_cache(redis_url: str | None = None) -> None:
    global _cache
    if not redis_url:
        _cache = InMemoryCache()
        return

    try:
        _cache = RedisCache(redis_url)
    except Exception:
        _cache = InMemoryCache()


def get_cache() -> CacheBackend:
    return _cache
