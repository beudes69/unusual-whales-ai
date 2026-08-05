"""Tests hors ligne de la robustesse WebSocket."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import Callable

import pytest

from okx_ai_pro.okx.exceptions import (
    OkxNetworkError,
    OkxTimeoutError,
    OkxWebSocketClosedError,
    OkxWebSocketError,
)
from okx_ai_pro.okx.interfaces import WebSocketConnectionProtocol
from okx_ai_pro.okx.models import WebSocketMessage, WebSocketSubscription
from okx_ai_pro.okx.websocket import WebSocketClient, default_websocket_connector
from okx_ai_pro.settings import OkxSettings


class NoOpLimiter:
    async def acquire(self) -> None:
        return


class MockConnection:
    def __init__(
        self,
        messages: list[str | bytes | BaseException] | None = None,
        *,
        send_error: BaseException | None = None,
    ) -> None:
        self.incoming: asyncio.Queue[str | bytes | BaseException] = asyncio.Queue()
        for message in messages or []:
            self.incoming.put_nowait(message)
        self.sent: list[str] = []
        self.sent_event = asyncio.Event()
        self.received_event = asyncio.Event()
        self.send_error = send_error
        self.closed = False

    async def send(self, message: str) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(message)
        self.sent_event.set()

    async def recv(self, decode: bool | None = None) -> str | bytes:
        message = await self.incoming.get()
        self.received_event.set()
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
        user_agent_header: str,
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
    connection.incoming.put_nowait(b"\xff")
    connection.incoming.put_nowait(json.dumps([]))
    connection.incoming.put_nowait(json.dumps({"arg": {}, "data": "invalid"}))
    connection.incoming.put_nowait(json.dumps({"arg": {"channel": "tickers"}, "data": [1]}))
    connection.incoming.put_nowait(json.dumps({"arg": {"channel": ""}, "data": [{}]}))
    connection.incoming.put_nowait(json.dumps({"event": "subscribe"}))
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
        user_agent_header: str,
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


@pytest.mark.asyncio
async def test_default_connector_disables_protocol_ping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = MockConnection()
    captured: dict[str, object] = {}

    async def fake_connect(uri: str, **kwargs: object) -> MockConnection:
        captured["uri"] = uri
        captured.update(kwargs)
        return connection

    monkeypatch.setattr("okx_ai_pro.okx.websocket.connect", fake_connect)

    result = await default_websocket_connector(
        "wss://example.test/public",
        open_timeout=2.0,
        close_timeout=3.0,
        ping_interval=None,
        user_agent_header="OKX-AI-PRO/test",
    )

    assert result is connection
    assert captured == {
        "uri": "wss://example.test/public",
        "open_timeout": 2.0,
        "close_timeout": 3.0,
        "ping_interval": None,
        "user_agent_header": "OKX-AI-PRO/test",
    }


@pytest.mark.asyncio
async def test_accepts_binary_utf8_pong(
    okx_settings: OkxSettings,
) -> None:
    connection = MockConnection([b"pong"])
    client = WebSocketClient(
        _fast_settings(okx_settings),
        connector=SequenceConnector([connection]),
        subscription_limiter=NoOpLimiter(),
    )

    await client.start()
    await asyncio.wait_for(connection.received_event.wait(), 1)
    await client.close()

    assert connection.closed


@pytest.mark.asyncio
async def test_subscription_operations_handle_duplicates_and_send_failure(
    okx_settings: OkxSettings,
) -> None:
    connection = MockConnection()
    client = WebSocketClient(
        _fast_settings(okx_settings),
        connector=SequenceConnector([connection]),
        subscription_limiter=NoOpLimiter(),
    )
    subscription = WebSocketSubscription(
        channel="tickers",
        instrument_id="BTC-USDT-SWAP",
    )
    unknown = WebSocketSubscription(
        channel="tickers",
        instrument_id="ETH-USDT-SWAP",
    )

    def first(message: WebSocketMessage) -> None:
        return

    def second(message: WebSocketMessage) -> None:
        return

    await client.start()
    await client.unsubscribe(unknown)
    await client.subscribe(subscription, first)
    await client.subscribe(subscription, first)
    await client.subscribe(subscription, second)
    await client.unsubscribe(subscription, first)
    await client.unsubscribe(subscription, first)
    await client.unsubscribe(subscription, second)
    await client.close()

    assert [json.loads(message)["op"] for message in connection.sent] == [
        "subscribe",
        "unsubscribe",
    ]

    failing = MockConnection(send_error=OSError("closed"))
    failing_client = WebSocketClient(
        _fast_settings(okx_settings),
        connector=SequenceConnector([failing]),
        subscription_limiter=NoOpLimiter(),
    )
    await failing_client.start()
    with pytest.raises(OkxWebSocketClosedError):
        await failing_client.subscribe(subscription, first)
    await failing_client.close()


def test_transport_errors_are_always_normalized() -> None:
    assert isinstance(
        WebSocketClient._translate_transport_error(TimeoutError()),
        OkxTimeoutError,
    )
    assert isinstance(
        WebSocketClient._translate_transport_error(OSError()),
        OkxWebSocketClosedError,
    )
    assert isinstance(
        WebSocketClient._translate_transport_error(OkxNetworkError("offline")),
        OkxWebSocketClosedError,
    )
    existing = OkxWebSocketError("known")
    assert WebSocketClient._translate_transport_error(existing) is existing
    assert isinstance(
        WebSocketClient._translate_transport_error(ValueError()),
        OkxWebSocketError,
    )


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
