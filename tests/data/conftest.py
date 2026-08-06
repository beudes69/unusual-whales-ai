"""Fixtures du stockage et de la collecte."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TypedDict

import pytest

from okx_ai_pro.data.models import (
    CandleRecord,
    ContractMetadata,
    DataRecord,
    DataSource,
    FundingRateSnapshot,
    IndexPriceSnapshot,
    MarkPriceSnapshot,
    OpenInterestSnapshot,
    OrderBookLevel,
    OrderBookSnapshot,
    TickerSnapshot,
)
from okx_ai_pro.settings import Settings, load_settings

TIMESTAMP = datetime(2026, 8, 5, 12, tzinfo=UTC)


class _CommonRecordValues(TypedDict):
    timestamp: datetime
    received_at: datetime
    source: DataSource


@pytest.fixture
def data_settings(tmp_path: Path) -> Settings:
    return load_settings(environ={}, base_directory=tmp_path)


@pytest.fixture
def sample_records() -> tuple[DataRecord, ...]:
    common: _CommonRecordValues = {
        "timestamp": TIMESTAMP,
        "received_at": TIMESTAMP,
        "source": DataSource.WEBSOCKET,
    }
    return (
        ContractMetadata(
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
            listing_time=TIMESTAMP,
            expiry_time=None,
            state="live",
            observed_at=TIMESTAMP,
        ),
        TickerSnapshot(
            instrument_id="BTC-USDT-SWAP",
            last_price=Decimal("100"),
            volume_contracts_24h=Decimal("1000"),
            volume_currency_24h=Decimal("10"),
            **common,
        ),
        MarkPriceSnapshot(
            instrument_id="BTC-USDT-SWAP",
            mark_price=Decimal("100.1"),
            **common,
        ),
        IndexPriceSnapshot(
            index_id="BTC-USDT",
            index_price=Decimal("100.2"),
            **common,
        ),
        OpenInterestSnapshot(
            instrument_id="BTC-USDT-SWAP",
            contracts=Decimal("500"),
            currency=Decimal("5"),
            usd=Decimal("50000"),
            **common,
        ),
        FundingRateSnapshot(
            instrument_id="BTC-USDT-SWAP",
            rate=Decimal("0.0001"),
            funding_time=TIMESTAMP,
            next_rate=Decimal("0.0002"),
            next_funding_time=TIMESTAMP,
            **common,
        ),
        OrderBookSnapshot(
            instrument_id="BTC-USDT-SWAP",
            sequence_id=10,
            previous_sequence_id=9,
            asks=(
                OrderBookLevel(
                    price=Decimal("101"),
                    size=Decimal("2"),
                    order_count=1,
                ),
            ),
            bids=(
                OrderBookLevel(
                    price=Decimal("100"),
                    size=Decimal("3"),
                    order_count=2,
                ),
            ),
            **common,
        ),
        CandleRecord(
            instrument_id="BTC-USDT-SWAP",
            timeframe="1m",
            open=Decimal("100"),
            high=Decimal("110"),
            low=Decimal("90"),
            close=Decimal("105"),
            volume_contracts=Decimal("10"),
            volume_currency=Decimal("1"),
            volume_quote=Decimal("1000"),
            complete=True,
            **common,
        ),
    )
