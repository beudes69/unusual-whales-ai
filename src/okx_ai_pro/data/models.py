"""Modèles normalisés et immuables du moteur de données."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PositiveDecimal = Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]
NonNegativeDecimal = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
FiniteDecimal = Annotated[Decimal, Field(allow_inf_nan=False)]


class DataSource(StrEnum):
    """Origine contrôlée d'une observation."""

    REST = "rest"
    WEBSOCKET = "websocket"


class DataModel(BaseModel):
    """Base stricte des objets qui peuvent circuler hors des transports."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class TimestampedRecord(DataModel):
    """Observation horodatée en UTC."""

    timestamp: datetime
    received_at: datetime
    source: DataSource

    @field_validator("timestamp", "received_at")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        """Refuse les dates naïves et normalise les autres en UTC."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Le timestamp doit contenir un fuseau horaire.")
        return value.astimezone(UTC)


class ContractMetadata(DataModel):
    """Métadonnées normalisées d'un contrat USDT-SWAP."""

    instrument_id: str = Field(min_length=1)
    instrument_family: str | None
    underlying: str | None
    settle_currency: str = Field(min_length=1)
    contract_type: str | None
    contract_value: Decimal | None
    contract_value_currency: str | None
    tick_size: PositiveDecimal
    lot_size: PositiveDecimal
    minimum_size: PositiveDecimal
    maximum_leverage: Decimal | None
    listing_time: datetime | None
    expiry_time: datetime | None
    state: str = Field(min_length=1)
    observed_at: datetime

    @field_validator("listing_time", "expiry_time", "observed_at")
    @classmethod
    def normalize_optional_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Les dates de contrat doivent contenir un fuseau horaire.")
        return value.astimezone(UTC)


class TickerSnapshot(TimestampedRecord):
    """Prix et volumes glissants 24 h."""

    instrument_id: str = Field(min_length=1)
    last_price: PositiveDecimal
    volume_contracts_24h: NonNegativeDecimal
    volume_currency_24h: NonNegativeDecimal


class MarkPriceSnapshot(TimestampedRecord):
    """Mark price d'un contrat."""

    instrument_id: str = Field(min_length=1)
    mark_price: PositiveDecimal


class IndexPriceSnapshot(TimestampedRecord):
    """Prix de l'indice sous-jacent."""

    index_id: str = Field(min_length=1)
    index_price: PositiveDecimal


class OpenInterestSnapshot(TimestampedRecord):
    """Open interest dans les trois unités fournies par OKX."""

    instrument_id: str = Field(min_length=1)
    contracts: NonNegativeDecimal
    currency: NonNegativeDecimal | None
    usd: NonNegativeDecimal | None


class FundingRateSnapshot(TimestampedRecord):
    """Funding courant et prochaine échéance annoncée."""

    instrument_id: str = Field(min_length=1)
    rate: FiniteDecimal
    funding_time: datetime
    next_rate: Decimal | None
    next_funding_time: datetime | None

    @field_validator("funding_time", "next_funding_time")
    @classmethod
    def normalize_funding_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Les dates de funding doivent contenir un fuseau horaire.")
        return value.astimezone(UTC)


class OrderBookLevel(DataModel):
    """Niveau normalisé du carnet."""

    price: PositiveDecimal
    size: NonNegativeDecimal
    order_count: Annotated[int, Field(ge=0)]


class OrderBookSnapshot(TimestampedRecord):
    """Instantané borné et ordonné du carnet."""

    instrument_id: str = Field(min_length=1)
    sequence_id: int
    previous_sequence_id: int | None
    asks: tuple[OrderBookLevel, ...]
    bids: tuple[OrderBookLevel, ...]


class CandleRecord(TimestampedRecord):
    """Bougie OHLCV normalisée."""

    instrument_id: str = Field(min_length=1)
    timeframe: str = Field(min_length=1)
    open: PositiveDecimal
    high: PositiveDecimal
    low: PositiveDecimal
    close: PositiveDecimal
    volume_contracts: NonNegativeDecimal
    volume_currency: NonNegativeDecimal
    volume_quote: NonNegativeDecimal
    complete: bool

    @model_validator(mode="after")
    def validate_price_range(self) -> CandleRecord:
        """Garantit la cohérence structurelle OHLC."""
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("Le plus haut de la bougie est incohérent.")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("Le plus bas de la bougie est incohérent.")
        return self


DataRecord: TypeAlias = (
    ContractMetadata
    | TickerSnapshot
    | MarkPriceSnapshot
    | IndexPriceSnapshot
    | OpenInterestSnapshot
    | FundingRateSnapshot
    | OrderBookSnapshot
    | CandleRecord
)


def record_identity(record: DataRecord) -> str:
    """Construit une identité stable utilisée pour la déduplication."""
    if isinstance(record, ContractMetadata):
        return f"contract:{record.instrument_id}:{record.observed_at.isoformat()}"
    if isinstance(record, CandleRecord):
        return (
            f"candle:{record.instrument_id}:{record.timeframe}:"
            f"{record.timestamp.isoformat()}"
        )
    if isinstance(record, OrderBookSnapshot):
        return f"book:{record.instrument_id}:{record.sequence_id}"
    identifier = (
        record.index_id
        if isinstance(record, IndexPriceSnapshot)
        else record.instrument_id
    )
    return f"{type(record).__name__}:{identifier}:{record.timestamp.isoformat()}"
