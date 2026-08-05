"""Tests des modèles de données OKX."""

from collections.abc import Callable
from datetime import UTC
from decimal import Decimal

import pytest
from pydantic import ValidationError

from okx_ai_pro.okx.models import (
    Candle,
    Instrument,
    OrderBook,
    OrderBookLevel,
    WebSocketSubscription,
)


def test_parses_instrument_and_normalizes_empty_fields(
    instrument_payload: dict[str, str],
) -> None:
    instrument = Instrument.model_validate(instrument_payload)

    assert instrument.instrument_id == "BTC-USDT-SWAP"
    assert instrument.contract_value == Decimal("0.01")
    assert instrument.base_currency is None
    assert instrument.expiry_time is None
    assert instrument.listing_time is not None
    assert instrument.listing_time.tzinfo is UTC


def test_parses_positional_candle_and_order_book() -> None:
    candle = Candle.from_api(
        [
            "1597026383085",
            "100",
            "110",
            "90",
            "105",
            "12",
            "1.2",
            "1260",
            "1",
        ]
    )
    book = OrderBook.from_api(
        {
            "asks": [["101", "2", "0", "3"]],
            "bids": [["100", "4", "0", "2"]],
            "ts": "1597026383085",
            "seqId": "12",
            "prevSeqId": "11",
        }
    )

    assert candle.close == Decimal("105")
    assert candle.confirmed is True
    assert book.asks[0] == OrderBookLevel(
        price=Decimal("101"),
        size=Decimal("2"),
        liquidated_orders=0,
        order_count=3,
    )
    assert book.sequence_id == 12


@pytest.mark.parametrize(
    "factory",
    [
        lambda: Candle.from_api(["1"]),
        lambda: OrderBookLevel.from_api(["1", "2"]),
        lambda: OrderBook.from_api({"asks": [], "ts": "1"}),
        lambda: OrderBook.from_api({"asks": [], "bids": [], "ts": ""}),
    ],
)
def test_rejects_malformed_positional_data(factory: Callable[[], object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        factory()


def test_subscription_serializes_with_okx_aliases() -> None:
    subscription = WebSocketSubscription(
        channel="books5",
        instrument_id="BTC-USDT-SWAP",
    )

    assert subscription.as_api_argument() == {
        "channel": "books5",
        "instId": "BTC-USDT-SWAP",
    }
    with pytest.raises(ValidationError):
        WebSocketSubscription(channel="")
