"""Tests hors ligne de la robustesse WebSocket."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import Callable

import pytest

from okx_ai_pro.okx.exceptions import OkxTimeoutError
from okx_ai_pro.okx.interfaces import WebSocketConnectionProtocol
from okx_ai_pro.okx.models import WebSocketMessage, WebSocketSubscription
from okx_ai_pro.okx.websocket import WebSocketClient
from okx_ai_pro.settings import OkxSettings


class NoOpLimiter:
    async def acquire(self) -> None:
        return


class MockConnection:
    def __init__(self, messages: list[str | bytes | BaseException] | None = None) -> None:
        self.incoming: asyncio.Queue[str | bytes | BaseException] = asyncio.Queue()
        for message in messages or []:
            self.incoming.put_nowait(message)
        self.sent: list[str] = []
        self.sent_event = asyncio.Event()
        self.closed = False

    async def send(self, message: str) -> None:
        self.sent.append(message)
        self.sent_event.set()

    async def recv(self, decode: bool | None = None) -> str | bytes:
        message = await self.incoming.get()
        if isinstance(message, BaseException):
            raise message
        return message

    async def close(self) -> None:
        self.closed = True


class SequenceConnector:
    def __init__(
        self,
        results: list[WebSocketConnectionProtocol | BaseException],
    ) -> None:
        self._results = deque(results)
        self.calls = 0
        self.called = asyncio.Event()

    async def __call__(
        self,
        uri: str,
        *,
        open_timeout: float,
        close_timeout: float,
        ping_interval: None,
    ) -> WebSocketConnectionProtocol:
        self.calls += 1
        self.called.set()
        result = self._results.popleft()
        if isinstance(result, BaseException):
            raise result
        return result


def _fast_settings(settings: OkxSettings) -> OkxSettings:
    reconnect = settings.reconnect.model_copy(
        update={
            "initial_delay_seconds": 0.0,
            "maximum_delay_seconds": 0.01,
        }
    )
    return settings.model_copy(
        update={
            "websocket_open_timeout_seconds": 0.1,
            "websocket_receive_timeout_seconds": 0.1,
            "websocket_heartbeat_timeout_seconds": 0.05,
            "reconnect": reconnect,
        }
    )


@pytest.mark.asyncio
async def test_subscribes_dispatches_and_unsubscribes(
    okx_settings: OkxSettings,
) -> None:
    connection = MockConnection()
    connector = SequenceConnector([connection])
    client = WebSocketClient(
        _fast_settings(okx_settings),
        connector=connector,
        subscription_limiter=NoOpLimiter(),
    )
    subscription = WebSocketSubscription(
        channel="books5",
        instrument_id="BTC-USDT-SWAP",
    )
    received: list[WebSocketMessage] = []
    received_event = asyncio.Event()

    async def callback(message: WebSocketMessage) -> None:
        received.append(message)
        received_event.set()

    await client.subscribe(subscription, callback)
    await client.start()
    await asyncio.wait_for(connection.sent_event.wait(), 1)
    connection.incoming.put_nowait(
        json.dumps(
            {
                "arg": {"channel": "books5", "instId": "BTC-USDT-SWAP"},
                "action": "snapshot",
                "data": [{"asks": [], "bids": []}],
            }
        )
    )
    await asyncio.wait_for(received_event.wait(), 1)
    await client.unsubscribe(subscription, callback)
    await client.close()

    assert received[0].action == "snapshot"
    assert json.loads(connection.sent[0])["op"] == "subscribe"
    assert json.loads(connection.sent[1])["op"] == "unsubscribe"
    assert connection.closed
    assert not client.is_connected


@pytest.mark.asyncio
async def test_reconnects_and_restores_all_subscriptions(
    okx_settings: OkxSettings,
) -> None:
    first = MockConnection([OSError("déconnexion")])
    second = MockConnection()
    connector = SequenceConnector([first, second])
    client = WebSocketClient(
        _fast_settings(okx_settings),
        connector=connector,
        subscription_limiter=NoOpLimiter(),
        sleep=_no_sleep,
    )
    subscription = WebSocketSubscription(
        channel="mark-price",
        instrument_id="BTC-USDT-SWAP",
    )

    await client.subscribe(subscription, lambda message: None)
    await client.start()
    await _wait_until(lambda: connector.calls >= 2)
    await _wait_until(lambda: bool(second.sent))
    await client.close()

    assert json.loads(first.sent[0])["op"] == "subscribe"
    assert json.loads(second.sent[0])["op"] == "subscribe"
    assert first.closed
    assert second.closed


@pytest.mark.asyncio
async def test_heartbeat_sends_text_ping_on_idle_connection(
    okx_settings: OkxSettings,
) -> None:
    connection = MockConnection()
    connector = SequenceConnector([connection])
    fast = _fast_settings(okx_settings).model_copy(
        update={
            "websocket_receive_timeout_seconds": 0.005,
            "websocket_heartbeat_timeout_seconds": 0.1,
        }
    )
    client = WebSocketClient(
        fast,
        connector=connector,
        subscription_limiter=NoOpLimiter(),
    )

    await client.start()
    await asyncio.wait_for(connection.sent_event.wait(), 1)
    await client.close()

    assert connection.sent == ["ping"]


@pytest.mark.asyncio
async def test_invalid_messages_and_callback_errors_are_isolated(
    okx_settings: OkxSettings,
) -> None:
    connection = MockConnection()
    connector = SequenceConnector([connection])
    client = WebSocketClient(
        _fast_settings(okx_settings),
        connector=connector,
        subscription_limiter=NoOpLimiter(),
    )
    subscription = WebSocketSubscription(
        channel="tickers",
        instrument_id="BTC-USDT-SWAP",
    )
    delivered = asyncio.Event()

    def broken_callback(message: WebSocketMessage) -> None:
        raise RuntimeError("erreur utilisateur")

    def working_callback(message: WebSocketMessage) -> None:
        delivered.set()

    await client.subscribe(subscription, broken_callback)
    await client.subscribe(subscription, working_callback)
    await client.start()
    connection.incoming.put_nowait("not-json")
    connection.incoming.put_nowait(
        json.dumps({"event": "error", "code": "60012", "msg": "invalid"})
    )
    connection.incoming.put_nowait(
        json.dumps(
            {
                "arg": {"channel": "tickers", "instId": "BTC-USDT-SWAP"},
                "data": [{"last": "100"}],
            }
        )
    )
    await asyncio.wait_for(delivered.wait(), 1)
    await client.unsubscribe(subscription)
    await client.close()

    assert len(connection.sent) == 2


@pytest.mark.asyncio
async def test_recovers_when_initial_connector_fails(
    okx_settings: OkxSettings,
) -> None:
    connection = MockConnection()
    connector = SequenceConnector([OSError("offline"), connection])
    client = WebSocketClient(
        _fast_settings(okx_settings),
        connector=connector,
        subscription_limiter=NoOpLimiter(),
        sleep=_no_sleep,
    )

    await client.start()
    assert client.is_connected
    assert connector.calls == 2
    await client.start()
    await client.close()


@pytest.mark.asyncio
async def test_start_times_out_and_cancels_runner(
    okx_settings: OkxSettings,
) -> None:
    never = asyncio.Event()

    async def hanging_connector(
        uri: str,
        *,
        open_timeout: float,
        close_timeout: float,
        ping_interval: None,
    ) -> WebSocketConnectionProtocol:
        await never.wait()
        raise AssertionError("inaccessible")

    settings = _fast_settings(okx_settings).model_copy(
        update={"websocket_open_timeout_seconds": 0.005}
    )
    client = WebSocketClient(
        settings,
        connector=hanging_connector,
        subscription_limiter=NoOpLimiter(),
    )

    with pytest.raises(OkxTimeoutError):
        await client.start()
    assert not client.is_connected


async def _no_sleep(delay: float) -> None:
    await asyncio.sleep(0)


async def _wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = 1.0,
) -> None:
    async def wait() -> None:
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(wait(), timeout)
