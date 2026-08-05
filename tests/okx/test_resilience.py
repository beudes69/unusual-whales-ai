"""Tests du limiteur, du retry et de l'état de connexion."""

from collections.abc import Awaitable, Callable

import pytest

from okx_ai_pro.okx.connection import ConnectionManager, ConnectionState
from okx_ai_pro.okx.exceptions import (
    OkxApiError,
    OkxNetworkError,
    OkxRateLimitError,
    OkxTimeoutError,
)
from okx_ai_pro.okx.rate_limiter import RateLimiter
from okx_ai_pro.okx.retry import RetryManager
from okx_ai_pro.settings import (
    RateLimitSettings,
    ReconnectSettings,
    RetrySettings,
)


class FakeClock:
    def __init__(self) -> None:
        self.current = 0.0
        self.delays: list[float] = []

    def now(self) -> float:
        return self.current

    async def sleep(self, delay: float) -> None:
        self.delays.append(delay)
        self.current += delay


@pytest.mark.asyncio
async def test_rate_limiter_waits_for_sliding_window() -> None:
    clock = FakeClock()
    limiter = RateLimiter(
        RateLimitSettings(max_requests=2, period_seconds=2.0),
        clock=clock,
    )

    await limiter.acquire()
    await limiter.acquire()
    await limiter.acquire()

    assert clock.delays == [2.0]
    assert clock.current == 2.0


@pytest.mark.asyncio
async def test_retry_uses_backoff_and_eventually_succeeds() -> None:
    delays: list[float] = []
    attempts = 0

    async def sleep(delay: float) -> None:
        delays.append(delay)

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise OkxNetworkError("temporaire")
        return "ok"

    manager = RetryManager(
        RetrySettings(
            max_attempts=3,
            initial_delay_seconds=0.5,
            maximum_delay_seconds=2.0,
            multiplier=2.0,
            jitter_seconds=0.0,
        ),
        sleep=sleep,
    )

    assert await manager.execute(operation) == "ok"
    assert delays == [0.5, 1.0]


@pytest.mark.asyncio
async def test_retry_honours_server_delay_and_stops_after_limit() -> None:
    delays: list[float] = []

    async def fail() -> None:
        raise OkxRateLimitError("quota", retry_after=3.0)

    manager = RetryManager(
        RetrySettings(
            max_attempts=2,
            initial_delay_seconds=0.1,
            maximum_delay_seconds=1.0,
            multiplier=2.0,
            jitter_seconds=0.0,
        ),
        sleep=_recording_sleep(delays),
    )

    with pytest.raises(OkxRateLimitError):
        await manager.execute(fail)
    assert delays == [3.0]


@pytest.mark.asyncio
async def test_retry_does_not_swallow_non_transient_error() -> None:
    manager = RetryManager(
        RetrySettings(
            max_attempts=3,
            initial_delay_seconds=0.0,
            maximum_delay_seconds=1.0,
            multiplier=1.0,
            jitter_seconds=0.0,
        )
    )

    async def fail() -> None:
        raise OkxApiError("contrat invalide", code="51000")

    with pytest.raises(OkxApiError):
        await manager.execute(fail)


@pytest.mark.asyncio
async def test_connection_manager_tracks_state_and_caps_backoff() -> None:
    manager = ConnectionManager(
        ReconnectSettings(
            initial_delay_seconds=1.0,
            maximum_delay_seconds=2.0,
            multiplier=2.0,
        )
    )

    await manager.mark_connecting()
    assert manager.state is ConnectionState.CONNECTING
    await manager.mark_connected()
    await manager.wait_until_connected(0.1)
    assert manager.is_connected
    assert await manager.next_reconnect_delay() == 1.0
    assert await manager.next_reconnect_delay() == 2.0
    assert await manager.next_reconnect_delay() == 2.0
    await manager.mark_stopping()
    await manager.mark_stopped()
    assert manager.state is ConnectionState.STOPPED


@pytest.mark.asyncio
async def test_connection_wait_times_out() -> None:
    manager = ConnectionManager(
        ReconnectSettings(
            initial_delay_seconds=0.0,
            maximum_delay_seconds=1.0,
            multiplier=1.0,
        )
    )

    with pytest.raises(OkxTimeoutError):
        await manager.wait_until_connected(0.001)
    await manager.mark_disconnected()
    assert manager.state is ConnectionState.DISCONNECTED


def _recording_sleep(delays: list[float]) -> Callable[[float], Awaitable[None]]:
    async def sleep(delay: float) -> None:
        delays.append(delay)

    return sleep
