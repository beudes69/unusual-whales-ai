"""Politique générique de nouvelles tentatives asynchrones."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

from okx_ai_pro.logging_config import get_logger
from okx_ai_pro.okx.exceptions import (
    OkxApiUnavailableError,
    OkxNetworkError,
    OkxRateLimitError,
    OkxTimeoutError,
)
from okx_ai_pro.settings import RetrySettings

T = TypeVar("T")
SleepCallable = Callable[[float], Awaitable[None]]
RandomCallable = Callable[[float, float], float]

_RETRYABLE_ERRORS = (
    OkxTimeoutError,
    OkxNetworkError,
    OkxApiUnavailableError,
    OkxRateLimitError,
)


class RetryManager:
    """Réessaie uniquement les erreurs explicitement considérées transitoires."""

    def __init__(
        self,
        settings: RetrySettings,
        *,
        sleep: SleepCallable = asyncio.sleep,
        random_uniform: RandomCallable = random.uniform,
    ) -> None:
        self._settings = settings
        self._sleep = sleep
        self._random_uniform = random_uniform
        self._logger = get_logger("okx.retry")

    async def execute(self, operation: Callable[[], Awaitable[T]]) -> T:
        """Exécute l'opération selon un backoff exponentiel borné."""
        for attempt in range(1, self._settings.max_attempts + 1):
            try:
                return await operation()
            except _RETRYABLE_ERRORS as exc:
                if attempt >= self._settings.max_attempts:
                    raise
                delay = self._compute_delay(attempt, exc)
                self._logger.warning(
                    "Communication OKX temporairement indisponible ; nouvelle tentative "
                    "%d/%d dans %.3f s (%s)",
                    attempt + 1,
                    self._settings.max_attempts,
                    delay,
                    type(exc).__name__,
                )
                await self._sleep(delay)

        raise RuntimeError("Boucle de retry terminée dans un état impossible.")

    def _compute_delay(
        self,
        attempt: int,
        error: OkxTimeoutError | OkxNetworkError | OkxApiUnavailableError | OkxRateLimitError,
    ) -> float:
        exponential = self._settings.initial_delay_seconds * (
            self._settings.multiplier ** (attempt - 1)
        )
        delay = min(exponential, self._settings.maximum_delay_seconds)
        if isinstance(error, OkxRateLimitError) and error.retry_after is not None:
            delay = max(delay, error.retry_after)
        if self._settings.jitter_seconds:
            delay += self._random_uniform(0.0, self._settings.jitter_seconds)
        return delay
