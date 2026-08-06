"""Modèles publics, immuables et validés des données OKX."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, TypeAlias

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
)


def _empty_to_none(value: object) -> object:
    return None if value == "" else value


def _milliseconds_to_datetime(value: object) -> datetime | None:
    if value in {None, ""}:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromtimestamp(int(str(value)) / 1000, tz=UTC)


OptionalDecimal = Annotated[Decimal | None, BeforeValidator(_empty_to_none)]
OptionalTimestamp = Annotated[datetime | None, BeforeValidator(_milliseconds_to_datetime)]
Timestamp = Annotated[datetime, BeforeValidator(_milliseconds_to_datetime)]
WebSocketPayload: TypeAlias = dict[str, object] | tuple[object, ...]


class CandleBar(StrEnum):
    """Granularités de bougies acceptées par l'API OKX."""

    M1 = "1m"
    M3 = "3m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1H"
    H2 = "2H"
    H4 = "4H"
    D1 = "1D"
    H6_UTC = "6Hutc"
    H12_UTC = "12Hutc"
    D1_UTC = "1Dutc"
    D2_UTC = "2Dutc"
    D3_UTC = "3Dutc"
    W1_UTC = "1Wutc"
    MONTH1_UTC = "1Mutc"


class OkxModel(BaseModel):
    """Base tolérante aux champs ajoutés par OKX, mais stricte sur les champs utilisés."""

    model_config = ConfigDict(
        extra="ignore",
        frozen=True,
        populate_by_name=True,
    )


class Instrument(OkxModel):
    """Métadonnées utiles d'un contrat perpétuel."""

    instrument_id: str = Field(alias="instId", min_length=1)
    instrument_type: str = Field(alias="instType", min_length=1)
    instrument_family: str | None = Field(default=None, alias="instFamily")
    underlying: str | None = Field(default=None, alias="uly")
    settle_currency: str = Field(alias="settleCcy", min_length=1)
    base_currency: str | None = Field(default=None, alias="baseCcy")
    quote_currency: str | None = Field(default=None, alias="quoteCcy")
    contract_type: str | None = Field(default=None, alias="ctType")
    contract_value: OptionalDecimal = Field(default=None, alias="ctVal")
    contract_multiplier: OptionalDecimal = Field(default=None, alias="ctMult")
    contract_value_currency: str | None = Field(default=None, alias="ctValCcy")
    tick_size: Decimal = Field(alias="tickSz", gt=0)
    lot_size: Decimal = Field(alias="lotSz", gt=0)
    minimum_size: Decimal = Field(alias="minSz", gt=0)
    maximum_leverage: OptionalDecimal = Field(default=None, alias="lever")
    listing_time: OptionalTimestamp = Field(default=None, alias="listTime")
    expiry_time: OptionalTimestamp = Field(default=None, alias="expTime")
    state: str = Field(min_length=1)

    @field_validator(
        "instrument_family",
        "underlying",
        "base_currency",
        "quote_currency",
        "contract_type",
        "contract_value_currency",
        mode="before",
    )
    @classmethod
    def empty_strings_to_none(cls, value: object) -> object:
        """Normalise les champs optionnels qu'OKX renvoie sous forme vide."""
        return _empty_to_none(value)


class Candle(OkxModel):
    """Bougie OHLCV normalisée."""

    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    volume_currency: Decimal
    volume_quote: Decimal
    confirmed: bool

    @classmethod
    def from_api(cls, row: list[object] | tuple[object, ...]) -> Candle:
        """Construit une bougie depuis le tableau positionnel d'OKX."""
        if len(row) != 9:
            raise ValueError("Une bougie OKX doit contenir exactement 9 valeurs.")
        timestamp = _milliseconds_to_datetime(row[0])
        if timestamp is None:
            raise ValueError("Le timestamp de bougie est absent.")
        return cls(
            timestamp=timestamp,
            open=Decimal(str(row[1])),
            high=Decimal(str(row[2])),
            low=Decimal(str(row[3])),
            close=Decimal(str(row[4])),
            volume=Decimal(str(row[5])),
            volume_currency=Decimal(str(row[6])),
            volume_quote=Decimal(str(row[7])),
            confirmed=str(row[8]) == "1",
        )


class FundingRate(OkxModel):
    """Taux de financement courant et prochain horaire."""

    instrument_id: str = Field(alias="instId", min_length=1)
    funding_rate: Decimal = Field(alias="fundingRate")
    funding_time: Timestamp = Field(alias="fundingTime")
    next_funding_rate: OptionalDecimal = Field(default=None, alias="nextFundingRate")
    next_funding_time: OptionalTimestamp = Field(default=None, alias="nextFundingTime")
    minimum_funding_rate: OptionalDecimal = Field(default=None, alias="minFundingRate")
    maximum_funding_rate: OptionalDecimal = Field(default=None, alias="maxFundingRate")


class OpenInterest(OkxModel):
    """Open interest d'un instrument."""

    instrument_id: str = Field(alias="instId", min_length=1)
    instrument_type: str = Field(alias="instType", min_length=1)
    open_interest: Decimal = Field(alias="oi")
    open_interest_currency: OptionalDecimal = Field(default=None, alias="oiCcy")
    open_interest_usd: OptionalDecimal = Field(default=None, alias="oiUsd")
    timestamp: Timestamp = Field(alias="ts")


class OrderBookLevel(OkxModel):
    """Un niveau de prix du carnet d'ordres."""

    price: Decimal
    size: Decimal
    liquidated_orders: int
    order_count: int

    @classmethod
    def from_api(cls, row: list[object] | tuple[object, ...]) -> OrderBookLevel:
        """Construit un niveau depuis le tableau positionnel d'OKX."""
        if len(row) != 4:
            raise ValueError("Un niveau de carnet OKX doit contenir exactement 4 valeurs.")
        return cls(
            price=Decimal(str(row[0])),
            size=Decimal(str(row[1])),
            liquidated_orders=int(str(row[2])),
            order_count=int(str(row[3])),
        )


class OrderBook(OkxModel):
    """Instantané validé du carnet d'ordres."""

    asks: tuple[OrderBookLevel, ...]
    bids: tuple[OrderBookLevel, ...]
    timestamp: datetime
    sequence_id: int | None = None
    previous_sequence_id: int | None = None

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> OrderBook:
        """Construit un carnet depuis la structure REST OKX."""
        timestamp = _milliseconds_to_datetime(payload.get("ts"))
        if timestamp is None:
            raise ValueError("Le timestamp du carnet est absent.")
        asks = payload.get("asks")
        bids = payload.get("bids")
        if not isinstance(asks, list) or not isinstance(bids, list):
            raise ValueError("Les côtés asks/bids du carnet sont invalides.")
        return cls(
            asks=tuple(OrderBookLevel.from_api(row) for row in asks),
            bids=tuple(OrderBookLevel.from_api(row) for row in bids),
            timestamp=timestamp,
            sequence_id=_optional_int(payload.get("seqId")),
            previous_sequence_id=_optional_int(payload.get("prevSeqId")),
        )


class MarkPrice(OkxModel):
    """Prix de référence (mark price) d'un contrat."""

    instrument_id: str = Field(alias="instId", min_length=1)
    instrument_type: str = Field(alias="instType", min_length=1)
    mark_price: Decimal = Field(alias="markPx")
    timestamp: Timestamp = Field(alias="ts")


class IndexPrice(OkxModel):
    """Prix de l'indice sous-jacent."""

    instrument_id: str = Field(alias="instId", min_length=1)
    index_price: Decimal = Field(alias="idxPx")
    high_24h: OptionalDecimal = Field(default=None, alias="high24h")
    low_24h: OptionalDecimal = Field(default=None, alias="low24h")
    open_24h: OptionalDecimal = Field(default=None, alias="open24h")
    timestamp: Timestamp = Field(alias="ts")


class WebSocketSubscription(OkxModel):
    """Description hashable d'un abonnement public OKX."""

    channel: str = Field(min_length=1)
    instrument_id: str | None = Field(
        default=None,
        validation_alias="instId",
        serialization_alias="instId",
    )
    instrument_type: str | None = Field(
        default=None,
        validation_alias="instType",
        serialization_alias="instType",
    )
    instrument_family: str | None = Field(
        default=None,
        validation_alias="instFamily",
        serialization_alias="instFamily",
    )

    def as_api_argument(self) -> dict[str, str]:
        """Retourne l'argument d'abonnement attendu par OKX."""
        return self.model_dump(by_alias=True, exclude_none=True)


class WebSocketMessage(OkxModel):
    """Message de données public reçu par WebSocket."""

    subscription: WebSocketSubscription
    action: str | None = None
    data: tuple[WebSocketPayload, ...]


def _optional_int(value: object) -> int | None:
    return None if value in {None, ""} else int(str(value))
