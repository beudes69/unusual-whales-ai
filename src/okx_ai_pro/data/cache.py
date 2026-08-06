"""Cache mémoire asynchrone TTL/LRU avec purge automatique."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from okx_ai_pro.settings import CacheSettings

K = TypeVar("K")
V = TypeVar("V")


class CacheClockProtocol(Protocol):
    """Horloge monotone injectable."""

    def now(self) -> float: ...

    async def sleep(self, delay: float) -> None: ...


class SystemCacheClock:
    def now(self) -> float:
        return time.monotonic()

    async def sleep(self, delay: float) -> None:
        await asyncio.sleep(delay)


@dataclass(slots=True)
class _CacheEntry(Generic[V]):
    value: V
    expires_at: float


class MemoryCache(Generic[K, V]):
    """Cache borné empêchant aussi les chargements concurrents identiques."""

    def __init__(
        self,
        settings: CacheSettings,
        *,
        clock: CacheClockProtocol | None = None,
    ) -> None:
        self._settings = settings
        self._clock = clock or SystemCacheClock()
        self._entries: OrderedDict[K, _CacheEntry[V]] = OrderedDict()
        self._inflight: dict[K, asyncio.Task[V]] = {}
        self._lock = asyncio.Lock()
        self._purger: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Démarre la purge périodique de façon idempotente."""
        if self._purger is None or self._purger.done():
            self._purger = asyncio.create_task(
                self._purge_loop(),
                name="okx-data-cache-purge",
            )

    async def close(self) -> None:
        """Arrête la purge sans supprimer les valeurs encore valides."""
        purger = self._purger
        self._purger = None
        if purger is not None:
            purger.cancel()
            try:
                await purger
            except asyncio.CancelledError:
                pass

    async def get(self, key: K) -> V | None:
        """Retourne une valeur valide et actualise sa récence LRU."""
        async with self._lock:
            return self._get_locked(key)

    async def set(self, key: K, value: V, *, ttl_seconds: float | None = None) -> None:
        """Insère une valeur avec un TTL explicite ou celui de la configuration."""
        ttl = self._settings.ttl_seconds if ttl_seconds is None else ttl_seconds
        if ttl <= 0:
            raise ValueError("Le TTL du cache doit être strictement positif.")
        async with self._lock:
            self._entries[key] = _CacheEntry(
                value=value,
                expires_at=self._clock.now() + ttl,
            )
            self._entries.move_to_end(key)
            while len(self._entries) > self._settings.maximum_entries:
                self._entries.popitem(last=False)

    async def get_or_set(
        self,
        key: K,
        factory: Callable[[], Awaitable[V]],
        *,
        ttl_seconds: float | None = None,
    ) -> V:
        """Charge une clé absente une seule fois, même sous concurrence."""
        cached = await self.get(key)
        if cached is not None:
            return cached

        async with self._lock:
            cached = self._get_locked(key)
            if cached is not None:
                return cached
            task = self._inflight.get(key)
            owner = task is None
            if task is None:
                task = asyncio.create_task(_await_factory(factory))
                self._inflight[key] = task

        try:
            value = await asyncio.shield(task)
        finally:
            if owner:
                async with self._lock:
                    if self._inflight.get(key) is task:
                        self._inflight.pop(key)
        if owner:
            await self.set(key, value, ttl_seconds=ttl_seconds)
        return value

    async def delete(self, key: K) -> None:
        async with self._lock:
            self._entries.pop(key, None)

    async def purge_expired(self) -> int:
        """Supprime les entrées expirées et retourne leur nombre."""
        async with self._lock:
            now = self._clock.now()
            expired = [
                key
                for key, entry in self._entries.items()
                if entry.expires_at <= now
            ]
            for key in expired:
                self._entries.pop(key)
            return len(expired)

    async def _purge_loop(self) -> None:
        try:
            while True:
                await self._clock.sleep(self._settings.purge_interval_seconds)
                await self.purge_expired()
        except asyncio.CancelledError:
            pass

    def _get_locked(self, key: K) -> V | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at <= self._clock.now():
            self._entries.pop(key)
            return None
        self._entries.move_to_end(key)
        return entry.value


async def _await_factory(factory: Callable[[], Awaitable[V]]) -> V:
    return await factory()
