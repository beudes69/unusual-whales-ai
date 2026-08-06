"""Tests de normalisation REST/WebSocket et du carnet incrémental."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from okx_ai_pro.data.exceptions import DataNormalizationError, DataSequenceError
from okx_ai_pro.data.models import (
    CandleRecord,
    FundingRateSnapshot,
    IndexPriceSnapshot,
    MarkPriceSnapshot,
    OpenInterestSnapshot,
    OrderBookSnapshot,
    TickerSnapshot,
)
from okx_ai_pro.data.normalization import DataNormalizer, OrderBookAssembler
from okx_ai_pro.okx.models import (
    Candle,
    Instrument,
    WebSocketMessage,
    WebSocketPayload,
    WebSocketSubscription,
)
from okx_ai_pro.settings import Settings

NOW = datetime(2026, 8, 5, 12, tzinfo=UTC)
TIMESTAMP_MS = "1785931200000"


def _normalizer(settings: Settings) -> DataNormalizer:
    return DataNormalizer(settings.data.collection)


def _message(
    channel: str,
    data: tuple[WebSocketPayload, ...],
    *,
    instrument_id: str = "BTC-USDT-SWAP",
    action: str | None = None,
) -> WebSocketMessage:
    return WebSocketMessage(
        subscription=WebSocketSubscription(
            channel=channel,
            instrument_id=instrument_id,
        ),
        action=action,
        data=data,
    )


def test_normalizes_rest_contracts_and_candles(data_settings: Settings) -> None:
    normalizer = _normalizer(data_settings)
    instrument = Instrument.model_validate(
        {
            "instId": "BTC-USDT-SWAP",
            "instType": "SWAP",
            "instFamily": "BTC-USDT",
            "uly": "BTC-USDT",
            "settleCcy": "USDT",
            "ctType": "linear",
            "ctVal": "0.01",
            "ctValCcy": "BTC",
            "tickSz": "0.1",
            "lotSz": "0.01",
            "minSz": "0.01",
            "lever": "100",
            "listTime": TIMESTAMP_MS,
            "expTime": "",
            "state": "live",
        }
    )
    candle = Candle.from_api([TIMESTAMP_MS, "100", "110", "90", "105", "10", "1", "1000", "1"])

    contract = normalizer.contracts((instrument,), observed_at=NOW)[0]
    normalized_candle = normalizer.candles(
        instrument.instrument_id,
        "1m",
        (candle,),
        received_at=NOW,
    )[0]

    assert contract.underlying == "BTC-USDT"
    assert contract.tick_size == Decimal("0.1")
    assert normalized_candle.complete
    assert normalized_candle.timestamp.tzinfo is UTC


@pytest.mark.parametrize(
    ("channel", "payload", "expected_type"),
    [
        (
            "tickers",
            {
                "instId": "BTC-USDT-SWAP",
                "last": "100",
                "vol24h": "1000",
                "volCcy24h": "10",
                "ts": TIMESTAMP_MS,
            },
            TickerSnapshot,
        ),
        (
            "mark-price",
            {
                "instId": "BTC-USDT-SWAP",
                "markPx": "100.1",
                "ts": TIMESTAMP_MS,
            },
            MarkPriceSnapshot,
        ),
        (
            "index-tickers",
            {"instId": "BTC-USDT", "idxPx": "100.2", "ts": TIMESTAMP_MS},
            IndexPriceSnapshot,
        ),
        (
            "open-interest",
            {
                "instId": "BTC-USDT-SWAP",
                "oi": "500",
                "oiCcy": "5",
                "oiUsd": "50000",
                "ts": TIMESTAMP_MS,
            },
            OpenInterestSnapshot,
        ),
        (
            "funding-rate",
            {
                "instId": "BTC-USDT-SWAP",
                "fundingRate": "0.0001",
                "fundingTime": TIMESTAMP_MS,
                "nextFundingRate": "",
                "nextFundingTime": TIMESTAMP_MS,
                "ts": TIMESTAMP_MS,
            },
            FundingRateSnapshot,
        ),
    ],
)
def test_normalizes_each_object_websocket_channel(
    data_settings: Settings,
    channel: str,
    payload: dict[str, object],
    expected_type: type[object],
) -> None:
    records = _normalizer(data_settings).websocket(
        _message(channel, (payload,)),
        received_at=NOW,
    )

    assert len(records) == 1
    assert isinstance(records[0], expected_type)


def test_normalizes_business_candle_array(data_settings: Settings) -> None:
    row = (
        TIMESTAMP_MS,
        "100",
        "110",
        "90",
        "105",
        "10",
        "1",
        "1000",
        "0",
    )

    records = _normalizer(data_settings).websocket(
        _message("candle1m", (row,)),
        received_at=NOW,
    )

    assert isinstance(records[0], CandleRecord)
    assert records[0].timeframe == "1m"
    assert not records[0].complete


def test_order_book_assembler_applies_snapshot_and_delta() -> None:
    assembler = OrderBookAssembler(depth=2)
    snapshot = {
        "asks": [["101", "2", "0", "1"], ["102", "3", "0", "2"]],
        "bids": [["100", "4", "0", "2"], ["99", "5", "0", "1"]],
        "ts": TIMESTAMP_MS,
        "seqId": 10,
        "prevSeqId": -1,
    }
    update = {
        "asks": [["101", "0", "0", "0"], ["103", "1", "0", "1"]],
        "bids": [["100.5", "2", "0", "1"]],
        "ts": TIMESTAMP_MS,
        "seqId": 11,
        "prevSeqId": 10,
    }

    first = assembler.apply(
        "BTC-USDT-SWAP",
        "snapshot",
        snapshot,
        received_at=NOW,
    )
    second = assembler.apply(
        "BTC-USDT-SWAP",
        "update",
        update,
        received_at=NOW,
    )

    assert isinstance(first, OrderBookSnapshot)
    assert [level.price for level in second.asks] == [
        Decimal("102"),
        Decimal("103"),
    ]
    assert second.bids[0].price == Decimal("100.5")


def test_order_book_detects_sequence_checksum_and_schema_errors() -> None:
    assembler = OrderBookAssembler(depth=5)
    snapshot = {
        "asks": [["101", "2", "0", "1"]],
        "bids": [["100", "4", "0", "2"]],
        "ts": TIMESTAMP_MS,
        "seqId": 10,
        "prevSeqId": -1,
    }
    assembler.apply("BTC-USDT-SWAP", "snapshot", snapshot, received_at=NOW)

    with pytest.raises(DataSequenceError, match="séquence"):
        assembler.apply(
            "BTC-USDT-SWAP",
            "update",
            {**snapshot, "seqId": 12, "prevSeqId": 9},
            received_at=NOW,
        )
    with pytest.raises(DataSequenceError, match="avant snapshot"):
        assembler.apply(
            "ETH-USDT-SWAP",
            "update",
            {**snapshot, "seqId": 11, "prevSeqId": 10},
            received_at=NOW,
        )
    with pytest.raises(DataSequenceError, match="Checksum"):
        assembler.apply(
            "BTC-USDT-SWAP",
            "snapshot",
            {**snapshot, "checksum": 1},
            received_at=NOW,
        )
    with pytest.raises(DataNormalizationError, match="exactement 4"):
        assembler.apply(
            "BTC-USDT-SWAP",
            "snapshot",
            {**snapshot, "asks": [["101", "2"]]},
            received_at=NOW,
        )


@pytest.mark.parametrize(
    "message",
    [
        _message("unknown", ({},)),
        _message("books", ({},), action=None),
        _message("candle1m", ({"not": "a row"},)),
    ],
)
def test_normalizer_rejects_unknown_or_malformed_payloads(
    data_settings: Settings,
    message: WebSocketMessage,
) -> None:
    with pytest.raises(DataNormalizationError):
        _normalizer(data_settings).websocket(message, received_at=NOW)


def test_order_book_rejects_invalid_depth_and_action() -> None:
    with pytest.raises(ValueError, match="profondeur"):
        OrderBookAssembler(0)
    assembler = OrderBookAssembler(5)
    with pytest.raises(DataNormalizationError, match="Action"):
        assembler.apply(
            "BTC-USDT-SWAP",
            "invalid",
            {
                "asks": [],
                "bids": [],
                "ts": TIMESTAMP_MS,
                "seqId": 1,
            },
            received_at=NOW,
        )
