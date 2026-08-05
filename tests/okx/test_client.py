"""Tests de la façade unique OkxClient."""

from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest

from okx_ai_pro.okx.client import OkxClient
from okx_ai_pro.okx.interfaces import RestClientProtocol, WebSocketClientProtocol
from okx_ai_pro.okx.models import CandleBar, WebSocketSubscription
from okx_ai_pro.settings import Settings


@pytest.mark.asyncio
async def test_facade_delegates_every_public_operation() -> None:
    rest = Mock(spec=RestClientProtocol)
    websocket = Mock(spec=WebSocketClientProtocol)
    rest.get_usdt_swap_contracts = AsyncMock(return_value=())
    rest.get_contract = AsyncMock()
    rest.get_candles = AsyncMock(return_value=())
    rest.get_funding_rate = AsyncMock()
    rest.get_open_interest = AsyncMock()
    rest.get_order_book = AsyncMock()
    rest.get_mark_price = AsyncMock()
    rest.get_index_price = AsyncMock()
    rest.close = AsyncMock()
    websocket.start = AsyncMock()
    websocket.subscribe = AsyncMock()
    websocket.unsubscribe = AsyncMock()
    websocket.close = AsyncMock()
    websocket.is_connected = True
    client = OkxClient(
        cast(RestClientProtocol, rest),
        cast(WebSocketClientProtocol, websocket),
    )
    subscription = WebSocketSubscription(
        channel="tickers",
        instrument_id="BTC-USDT-SWAP",
    )

    callback = Mock()
    await client.start()
    assert client.is_connected
    await client.get_usdt_swap_contracts()
    await client.get_contract("BTC-USDT-SWAP")
    await client.get_candles(
        "BTC-USDT-SWAP",
        bar=CandleBar.M5,
        limit=20,
        after="1",
        before="2",
    )
    await client.get_funding_rate("BTC-USDT-SWAP")
    await client.get_open_interest("BTC-USDT-SWAP")
    await client.get_order_book("BTC-USDT-SWAP", depth=5)
    await client.get_mark_price("BTC-USDT-SWAP")
    await client.get_index_price("BTC-USDT")
    await client.subscribe(subscription, callback)
    await client.unsubscribe(subscription, callback)
    await client.close()

    rest.get_candles.assert_awaited_once_with(
        "BTC-USDT-SWAP",
        bar=CandleBar.M5,
        limit=20,
        after="1",
        before="2",
    )
    websocket.subscribe.assert_awaited_once_with(subscription, callback)
    websocket.close.assert_awaited_once()
    rest.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_factory_builds_replaceable_production_components(
    settings: Settings,
) -> None:
    client = OkxClient.from_settings(settings)

    assert not client.is_connected
    await client.close()
