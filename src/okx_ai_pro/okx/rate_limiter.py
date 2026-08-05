"""Limitation asynchrone par fenêtre glissante, indépendante d'OKX."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Protocol

from okx_ai_pro.settings import RateLimitSettings


class AsyncClockProtocol(Protocol):
    """Horloge injectable pour rendre l'attente déterministe en test."""

    def now(self) -> float: ...

    async def sleep(self, delay: float) -> None: ...


class SystemAsyncClock:
    """Horloge monotone de production."""

    def now(self) -> float:
        return time.monotonic()

    async def sleep(self, delay: float) -> None:
        await asyncio.sleep(delay)


class RateLimiter:
    """Respecte un quota maximal sur une fenêtre temporelle glissante."""

    def __init__(
        self,
        settings: RateLimitSettings,
        *,
        clock: AsyncClockProtocol | None = None,
    ) -> None:
        self._max_requests = settings.max_requests
        self._period = settings.period_seconds
        self._clock = clock or SystemAsyncClock()
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Attend sans bloquer la boucle jusqu'à la disponibilité d'un quota."""
        while True:
            async with self._lock:
                now = self._clock.now()
                threshold = now - self._period
                while self._timestamps and self._timestamps[0] <= threshold:
                    self._timestamps.popleft()

                if len(self._timestamps) < self._max_requests:
                    self._timestamps.append(now)
                    return

                delay = max(self._timestamps[0] + self._period - now, 0.0)
            await self._clock.sleep(delay)
