"""Tests des modèles normalisés et de la qualité des données."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from okx_ai_pro.data.cache import MemoryCache
from okx_ai_pro.data.models import (
    CandleRecord,
    ContractMetadata,
    DataRecord,
    DataSource,
    FundingRateSnapshot,
    IndexPriceSnapshot,
    OpenInterestSnapshot,
    OrderBookLevel,
    OrderBookSnapshot,
    TickerSnapshot,
    record_identity,
)
from okx_ai_pro.data.quality import AnomalyKind, DataQualityGate, DataQualityValidator
from okx_ai_pro.settings import CacheSettings, DataQualitySettings

NOW = datetime(2026, 8, 5, 12, tzinfo=UTC)


def _ticker(*, timestamp: datetime = NOW) -> TickerSnapshot:
    return TickerSnapshot(
        instrument_id="BTC-USDT-SWAP",
        last_price=Decimal("100"),
        volume_contracts_24h=Decimal("1000"),
        volume_currency_24h=Decimal("10"),
        timestamp=timestamp,
        received_at=NOW,
        source=DataSource.WEBSOCKET,
    )


def _candle(*, complete: bool) -> CandleRecord:
    return CandleRecord(
        instrument_id="BTC-USDT-SWAP",
        timeframe="1m",
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("90"),
        close=Decimal("105"),
        volume_contracts=Decimal("10"),
        volume_currency=Decimal("1"),
        volume_quote=Decimal("1000"),
        complete=complete,
        timestamp=NOW,
        received_at=NOW,
        source=DataSource.WEBSOCKET,
    )


def _quality_gate() -> DataQualityGate:
    cache: MemoryCache[str, DataRecord] = MemoryCache(
        CacheSettings(
            ttl_seconds=60.0,
            maximum_entries=100,
            purge_interval_seconds=10.0,
        )
    )
    validator = DataQualityValidator(
        DataQualitySettings(
            minimum_timestamp=datetime(2017, 1, 1, tzinfo=UTC),
            maximum_future_seconds=30.0,
        ),
        now=lambda: NOW,
    )
    return DataQualityGate(validator, cache)


@pytest.mark.asyncio
async def test_quality_gate_accepts_valid_data_and_rejects_duplicate() -> None:
    gate = _quality_gate()
    ticker = _ticker()

    assert await gate.accept(ticker)
    assert not await gate.accept(ticker)


@pytest.mark.asyncio
async def test_incomplete_candle_can_be_replaced_by_completed_version() -> None:
    gate = _quality_gate()
    incomplete = _candle(complete=False)
    complete = _candle(complete=True)

    assert await gate.accept(incomplete)
    assert await gate.accept(complete)
    assert not await gate.accept(complete)


@pytest.mark.asyncio
async def test_quality_gate_rejects_invalid_time_and_crossed_book() -> None:
    gate = _quality_gate()
    future = _ticker(timestamp=NOW + timedelta(minutes=1))
    crossed = OrderBookSnapshot(
        instrument_id="BTC-USDT-SWAP",
        sequence_id=1,
        previous_sequence_id=None,
        asks=(OrderBookLevel(price=Decimal("99"), size=Decimal("1"), order_count=1),),
        bids=(OrderBookLevel(price=Decimal("100"), size=Decimal("1"), order_count=1),),
        timestamp=NOW,
        received_at=NOW,
        source=DataSource.WEBSOCKET,
    )

    assert not await gate.accept(future)
    assert not await gate.accept(crossed)


def test_validator_reports_nonfatal_missing_and_incomplete_data() -> None:
    validator = DataQualityValidator(
        DataQualitySettings(
            minimum_timestamp=datetime(2017, 1, 1, tzinfo=UTC),
            maximum_future_seconds=30.0,
        ),
        now=lambda: NOW,
    )
    open_interest = OpenInterestSnapshot(
        instrument_id="BTC-USDT-SWAP",
        contracts=Decimal("10"),
        currency=None,
        usd=None,
        timestamp=NOW,
        received_at=NOW,
        source=DataSource.REST,
    )
    empty_book = OrderBookSnapshot(
        instrument_id="BTC-USDT-SWAP",
        sequence_id=2,
        previous_sequence_id=1,
        asks=(),
        bids=(),
        timestamp=NOW,
        received_at=NOW,
        source=DataSource.WEBSOCKET,
    )

    assert validator.inspect(open_interest)[0].kind is AnomalyKind.MISSING
    assert validator.inspect(empty_book)[0].fatal is False


def test_validator_detects_incoherent_funding_schedule() -> None:
    validator = DataQualityValidator(
        DataQualitySettings(
            minimum_timestamp=datetime(2017, 1, 1, tzinfo=UTC),
            maximum_future_seconds=30.0,
        ),
        now=lambda: NOW,
    )
    funding = FundingRateSnapshot(
        instrument_id="BTC-USDT-SWAP",
        rate=Decimal("0.0001"),
        funding_time=NOW,
        next_rate=None,
        next_funding_time=NOW - timedelta(hours=1),
        timestamp=NOW,
        received_at=NOW,
        source=DataSource.WEBSOCKET,
    )

    anomaly = validator.inspect(funding)[0]
    assert anomaly.kind is AnomalyKind.INCOHERENT
    assert anomaly.fatal


def test_models_reject_naive_dates_and_incoherent_numeric_values() -> None:
    with pytest.raises(ValidationError, match="fuseau"):
        _ticker(timestamp=datetime(2026, 8, 5, 12))  # noqa: DTZ001
    with pytest.raises(ValidationError, match="plus haut"):
        CandleRecord(
            instrument_id="BTC-USDT-SWAP",
            timeframe="1m",
            open=Decimal("100"),
            high=Decimal("99"),
            low=Decimal("90"),
            close=Decimal("100"),
            volume_contracts=Decimal("1"),
            volume_currency=Decimal("1"),
            volume_quote=Decimal("100"),
            complete=True,
            timestamp=NOW,
            received_at=NOW,
            source=DataSource.REST,
        )
    with pytest.raises(ValidationError):
        TickerSnapshot.model_validate(
            {
                **_ticker().model_dump(),
                "last_price": Decimal("-1"),
            }
        )


def test_record_identity_covers_contract_index_and_timed_records() -> None:
    contract = ContractMetadata(
        instrument_id="BTC-USDT-SWAP",
        instrument_family="BTC-USDT",
        underlying="BTC-USDT",
        settle_currency="USDT",
        contract_type="linear",
        contract_value=Decimal("0.01"),
        contract_value_currency="BTC",
        tick_size=Decimal("0.1"),
        lot_size=Decimal("0.01"),
        minimum_size=Decimal("0.01"),
        maximum_leverage=Decimal("100"),
        listing_time=NOW,
        expiry_time=None,
        state="live",
        observed_at=NOW,
    )
    index = IndexPriceSnapshot(
        index_id="BTC-USDT",
        index_price=Decimal("100"),
        timestamp=NOW,
        received_at=NOW,
        source=DataSource.WEBSOCKET,
    )

    assert record_identity(contract).startswith("contract:BTC-USDT-SWAP")
    assert record_identity(index).startswith("IndexPriceSnapshot:BTC-USDT")
    assert record_identity(_ticker()).startswith("TickerSnapshot:BTC-USDT-SWAP")
