"""Normalisation immédiate des réponses REST et messages WebSocket OKX."""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
)

from okx_ai_pro.data.exceptions import DataNormalizationError, DataSequenceError
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
from okx_ai_pro.okx.models import Candle, Instrument, WebSocketMessage
from okx_ai_pro.settings import CollectionSettings


def _timestamp(value: object) -> datetime:
    return datetime.fromtimestamp(int(str(value)) / 1000, tz=UTC)


def _optional_decimal(value: object) -> Decimal | None:
    return None if value in {None, ""} else Decimal(str(value))


Timestamp = Annotated[datetime, BeforeValidator(_timestamp)]
OptionalDecimal = Annotated[Decimal | None, BeforeValidator(_optional_decimal)]


class _WireModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class _TickerWire(_WireModel):
    instrument_id: str = Field(alias="instId")
    last_price: Decimal = Field(alias="last")
    volume_contracts: Decimal = Field(alias="vol24h")
    volume_currency: Decimal = Field(alias="volCcy24h")
    timestamp: Timestamp = Field(alias="ts")


class _MarkPriceWire(_WireModel):
    instrument_id: str = Field(alias="instId")
    mark_price: Decimal = Field(alias="markPx")
    timestamp: Timestamp = Field(alias="ts")


class _IndexPriceWire(_WireModel):
    index_id: str = Field(alias="instId")
    index_price: Decimal = Field(alias="idxPx")
    timestamp: Timestamp = Field(alias="ts")


class _OpenInterestWire(_WireModel):
    instrument_id: str = Field(alias="instId")
    contracts: Decimal = Field(alias="oi")
    currency: OptionalDecimal = Field(alias="oiCcy")
    usd: OptionalDecimal = Field(alias="oiUsd")
    timestamp: Timestamp = Field(alias="ts")


class _FundingRateWire(_WireModel):
    instrument_id: str = Field(alias="instId")
    rate: Decimal = Field(alias="fundingRate")
    funding_time: Timestamp = Field(alias="fundingTime")
    next_rate: OptionalDecimal = Field(default=None, alias="nextFundingRate")
    next_funding_time: Timestamp | None = Field(default=None, alias="nextFundingTime")
    timestamp: Timestamp = Field(alias="ts")


class _OrderBookWire(_WireModel):
    asks: list[list[str]]
    bids: list[list[str]]
    timestamp: Timestamp = Field(alias="ts")
    sequence_id: int = Field(alias="seqId")
    previous_sequence_id: int | None = Field(default=None, alias="prevSeqId")
    checksum: int | None = None


@dataclass(frozen=True, slots=True)
class _RawLevel:
    price_text: str
    size_text: str
    order_count: int

    @property
    def price(self) -> Decimal:
        return Decimal(self.price_text)

    def to_domain(self) -> OrderBookLevel:
        return OrderBookLevel(
            price=self.price,
            size=Decimal(self.size_text),
            order_count=self.order_count,
        )


@dataclass(slots=True)
class _BookState:
    asks: dict[Decimal, _RawLevel]
    bids: dict[Decimal, _RawLevel]
    sequence_id: int


class OrderBookAssembler:
    """Transforme snapshots/deltas OKX en instantanés typés et bornés."""

    def __init__(self, depth: int) -> None:
        if not 1 <= depth <= 400:
            raise ValueError("La profondeur doit être comprise entre 1 et 400.")
        self._depth = depth
        self._states: dict[str, _BookState] = {}

    def apply(
        self,
        instrument_id: str,
        action: str,
        payload: object,
        *,
        received_at: datetime,
    ) -> OrderBookSnapshot:
        try:
            wire = _OrderBookWire.model_validate(payload)
            if action == "snapshot":
                state = _BookState(asks={}, bids={}, sequence_id=wire.sequence_id)
                self._apply_side(state.asks, wire.asks)
                self._apply_side(state.bids, wire.bids)
                self._states[instrument_id] = state
            elif action == "update":
                existing_state = self._states.get(instrument_id)
                if existing_state is None:
                    raise DataSequenceError(
                        f"Delta carnet reçu avant snapshot pour {instrument_id}."
                    )
                state = existing_state
                if wire.previous_sequence_id != state.sequence_id:
                    self._states.pop(instrument_id, None)
                    raise DataSequenceError(f"Rupture de séquence carnet pour {instrument_id}.")
                self._apply_side(state.asks, wire.asks)
                self._apply_side(state.bids, wire.bids)
                state.sequence_id = wire.sequence_id
            else:
                raise DataNormalizationError(f"Action carnet inconnue : {action}")

            if wire.checksum is not None and self._checksum(state) != wire.checksum:
                self._states.pop(instrument_id, None)
                raise DataSequenceError(f"Checksum carnet invalide pour {instrument_id}.")
            asks = tuple(
                level.to_domain() for _, level in sorted(state.asks.items())[: self._depth]
            )
            bids = tuple(
                level.to_domain()
                for _, level in sorted(state.bids.items(), reverse=True)[: self._depth]
            )
            return OrderBookSnapshot(
                instrument_id=instrument_id,
                sequence_id=wire.sequence_id,
                previous_sequence_id=wire.previous_sequence_id,
                asks=asks,
                bids=bids,
                timestamp=wire.timestamp,
                received_at=received_at,
                source=DataSource.WEBSOCKET,
            )
        except (ArithmeticError, TypeError, ValueError, ValidationError) as exc:
            if isinstance(exc, DataNormalizationError):
                raise
            raise DataNormalizationError("Payload de carnet OKX invalide.") from exc

    @staticmethod
    def _apply_side(
        side: dict[Decimal, _RawLevel],
        rows: list[list[str]],
    ) -> None:
        for row in rows:
            if len(row) != 4:
                raise DataNormalizationError(
                    "Un niveau de carnet doit contenir exactement 4 valeurs."
                )
            price = Decimal(row[0])
            size = Decimal(row[1])
            if row[2] != "0":
                raise DataNormalizationError("Le champ déprécié du carnet doit valoir zéro.")
            if size == 0:
                side.pop(price, None)
            else:
                side[price] = _RawLevel(
                    price_text=row[0],
                    size_text=row[1],
                    order_count=int(row[3]),
                )

    @staticmethod
    def _checksum(state: _BookState) -> int:
        asks = [level for _, level in sorted(state.asks.items())[:25]]
        bids = [level for _, level in sorted(state.bids.items(), reverse=True)[:25]]
        values: list[str] = []
        for position in range(max(len(asks), len(bids))):
            if position < len(bids):
                values.extend([bids[position].price_text, bids[position].size_text])
            if position < len(asks):
                values.extend([asks[position].price_text, asks[position].size_text])
        checksum = zlib.crc32(":".join(values).encode())
        return checksum if checksum < 2**31 else checksum - 2**32


class DataNormalizer:
    """Frontière garantissant qu'aucun dictionnaire brut ne sort du module."""

    def __init__(
        self,
        settings: CollectionSettings,
        *,
        order_books: OrderBookAssembler | None = None,
    ) -> None:
        self._settings = settings
        self._order_books = order_books or OrderBookAssembler(settings.order_book_depth)

    def contracts(
        self,
        instruments: tuple[Instrument, ...],
        *,
        observed_at: datetime,
    ) -> tuple[ContractMetadata, ...]:
        try:
            return tuple(
                ContractMetadata(
                    instrument_id=item.instrument_id,
                    instrument_family=item.instrument_family,
                    underlying=item.underlying,
                    settle_currency=item.settle_currency,
                    contract_type=item.contract_type,
                    contract_value=item.contract_value,
                    contract_value_currency=item.contract_value_currency,
                    tick_size=item.tick_size,
                    lot_size=item.lot_size,
                    minimum_size=item.minimum_size,
                    maximum_leverage=item.maximum_leverage,
                    listing_time=item.listing_time,
                    expiry_time=item.expiry_time,
                    state=item.state,
                    observed_at=observed_at,
                )
                for item in instruments
            )
        except (ArithmeticError, TypeError, ValueError, ValidationError) as exc:
            raise DataNormalizationError("Métadonnées de contrats invalides.") from exc

    def candles(
        self,
        instrument_id: str,
        timeframe: str,
        candles: tuple[Candle, ...],
        *,
        received_at: datetime,
        source: DataSource = DataSource.REST,
    ) -> tuple[CandleRecord, ...]:
        try:
            return tuple(
                CandleRecord(
                    instrument_id=instrument_id,
                    timeframe=timeframe,
                    open=item.open,
                    high=item.high,
                    low=item.low,
                    close=item.close,
                    volume_contracts=item.volume,
                    volume_currency=item.volume_currency,
                    volume_quote=item.volume_quote,
                    complete=item.confirmed,
                    timestamp=item.timestamp,
                    received_at=received_at,
                    source=source,
                )
                for item in candles
            )
        except (ArithmeticError, TypeError, ValueError, ValidationError) as exc:
            raise DataNormalizationError("Bougies OKX invalides.") from exc

    def websocket(
        self,
        message: WebSocketMessage,
        *,
        received_at: datetime,
    ) -> tuple[DataRecord, ...]:
        channel = message.subscription.channel
        try:
            if channel == self._settings.ticker_channel:
                return tuple(self._ticker(item, received_at) for item in message.data)
            if channel == self._settings.mark_price_channel:
                return tuple(self._mark(item, received_at) for item in message.data)
            if channel == self._settings.index_ticker_channel:
                return tuple(self._index(item, received_at) for item in message.data)
            if channel == self._settings.open_interest_channel:
                return tuple(self._interest(item, received_at) for item in message.data)
            if channel == self._settings.funding_rate_channel:
                return tuple(self._funding(item, received_at) for item in message.data)
            if channel == self._settings.order_book_channel:
                instrument_id = message.subscription.instrument_id
                if instrument_id is None or message.action is None:
                    raise DataNormalizationError("Message de carnet sans instrument ou action.")
                return tuple(
                    self._order_books.apply(
                        instrument_id,
                        message.action,
                        item,
                        received_at=received_at,
                    )
                    for item in message.data
                )
            if channel.startswith(self._settings.candle_channel_prefix):
                instrument_id = message.subscription.instrument_id
                if instrument_id is None:
                    raise DataNormalizationError("Message de bougie sans instrument.")
                timeframe = channel.removeprefix(self._settings.candle_channel_prefix)
                rows = tuple(Candle.from_api(self._row(item)) for item in message.data)
                return self.candles(
                    instrument_id,
                    timeframe,
                    rows,
                    received_at=received_at,
                    source=DataSource.WEBSOCKET,
                )
            raise DataNormalizationError(f"Canal de données inconnu : {channel}")
        except (ArithmeticError, TypeError, ValueError, ValidationError) as exc:
            if isinstance(exc, DataNormalizationError):
                raise
            raise DataNormalizationError(f"Payload WebSocket invalide pour {channel}.") from exc

    @staticmethod
    def _ticker(payload: object, received_at: datetime) -> TickerSnapshot:
        item = _TickerWire.model_validate(payload)
        return TickerSnapshot(
            instrument_id=item.instrument_id,
            last_price=item.last_price,
            volume_contracts_24h=item.volume_contracts,
            volume_currency_24h=item.volume_currency,
            timestamp=item.timestamp,
            received_at=received_at,
            source=DataSource.WEBSOCKET,
        )

    @staticmethod
    def _mark(payload: object, received_at: datetime) -> MarkPriceSnapshot:
        item = _MarkPriceWire.model_validate(payload)
        return MarkPriceSnapshot(
            instrument_id=item.instrument_id,
            mark_price=item.mark_price,
            timestamp=item.timestamp,
            received_at=received_at,
            source=DataSource.WEBSOCKET,
        )

    @staticmethod
    def _index(payload: object, received_at: datetime) -> IndexPriceSnapshot:
        item = _IndexPriceWire.model_validate(payload)
        return IndexPriceSnapshot(
            index_id=item.index_id,
            index_price=item.index_price,
            timestamp=item.timestamp,
            received_at=received_at,
            source=DataSource.WEBSOCKET,
        )

    @staticmethod
    def _interest(payload: object, received_at: datetime) -> OpenInterestSnapshot:
        item = _OpenInterestWire.model_validate(payload)
        return OpenInterestSnapshot(
            instrument_id=item.instrument_id,
            contracts=item.contracts,
            currency=item.currency,
            usd=item.usd,
            timestamp=item.timestamp,
            received_at=received_at,
            source=DataSource.WEBSOCKET,
        )

    @staticmethod
    def _funding(payload: object, received_at: datetime) -> FundingRateSnapshot:
        item = _FundingRateWire.model_validate(payload)
        return FundingRateSnapshot(
            instrument_id=item.instrument_id,
            rate=item.rate,
            funding_time=item.funding_time,
            next_rate=item.next_rate,
            next_funding_time=item.next_funding_time,
            timestamp=item.timestamp,
            received_at=received_at,
            source=DataSource.WEBSOCKET,
        )

    @staticmethod
    def _row(payload: object) -> list[object] | tuple[object, ...]:
        if not isinstance(payload, (list, tuple)):
            raise DataNormalizationError("Ligne de bougie WebSocket invalide.")
        return payload
