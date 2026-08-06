"""Tests du cache mémoire TTL/LRU."""

import asyncio

import pytest

from okx_ai_pro.data.cache import MemoryCache
from okx_ai_pro.settings import CacheSettings


class FakeClock:
    def __init__(self) -> None:
        self.current = 0.0

    def now(self) -> float:
        return self.current

    async def sleep(self, delay: float) -> None:
        self.current += delay
        await asyncio.sleep(0)


def _settings(*, maximum_entries: int = 2) -> CacheSettings:
    return CacheSettings(
        ttl_seconds=10.0,
        maximum_entries=maximum_entries,
        purge_interval_seconds=60.0,
    )


@pytest.mark.asyncio
async def test_cache_expires_and_evicts_least_recent_entry() -> None:
    clock = FakeClock()
    cache: MemoryCache[str, int] = MemoryCache(_settings(), clock=clock)

    await cache.set("a", 1)
    await cache.set("b", 2)
    assert await cache.get("a") == 1
    await cache.set("c", 3)

    assert await cache.get("b") is None
    assert await cache.get("a") == 1
    clock.current = 11.0
    assert await cache.get("a") is None
    assert await cache.purge_expired() == 1


@pytest.mark.asyncio
async def test_get_or_set_coalesces_concurrent_loads() -> None:
    cache: MemoryCache[str, int] = MemoryCache(_settings())
    calls = 0

    async def factory() -> int:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return 42

    first, second = await asyncio.gather(
        cache.get_or_set("answer", factory),
        cache.get_or_set("answer", factory),
    )
    cached = await cache.get_or_set("answer", factory)

    assert (first, second, cached) == (42, 42, 42)
    assert calls == 1
    await cache.delete("answer")
    assert await cache.get("answer") is None


@pytest.mark.asyncio
async def test_failed_factory_can_be_retried() -> None:
    cache: MemoryCache[str, int] = MemoryCache(_settings())
    calls = 0

    async def factory() -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary")
        return 7

    with pytest.raises(RuntimeError, match="temporary"):
        await cache.get_or_set("key", factory)
    assert await cache.get_or_set("key", factory) == 7


@pytest.mark.asyncio
async def test_cache_lifecycle_and_ttl_validation() -> None:
    cache: MemoryCache[str, int] = MemoryCache(_settings())

    await cache.start()
    await cache.start()
    with pytest.raises(ValueError, match="TTL"):
        await cache.set("invalid", 1, ttl_seconds=0)
    await cache.close()
    await cache.close()
