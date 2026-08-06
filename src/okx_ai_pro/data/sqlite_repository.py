"""Implémentation SQLite asynchrone de la couche de stockage."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

import aiosqlite

from okx_ai_pro.data.exceptions import DataStorageError
from okx_ai_pro.data.models import (
    CandleRecord,
    ContractMetadata,
    DataRecord,
    DataSource,
    FundingRateSnapshot,
    IndexPriceSnapshot,
    MarkPriceSnapshot,
    OpenInterestSnapshot,
    OrderBookSnapshot,
    TickerSnapshot,
)
from okx_ai_pro.settings import SQLiteSettings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    version INTEGER NOT NULL
);
INSERT INTO schema_meta(version)
SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM schema_meta);

CREATE TABLE IF NOT EXISTS contracts (
    instrument_id TEXT PRIMARY KEY,
    instrument_family TEXT,
    underlying TEXT,
    settle_currency TEXT NOT NULL,
    contract_type TEXT,
    contract_value TEXT,
    contract_value_currency TEXT,
    tick_size TEXT NOT NULL,
    lot_size TEXT NOT NULL,
    minimum_size TEXT NOT NULL,
    maximum_leverage TEXT,
    listing_time TEXT,
    expiry_time TEXT,
    state TEXT NOT NULL,
    observed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ticker_snapshots (
    instrument_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    last_price TEXT NOT NULL,
    volume_contracts_24h TEXT NOT NULL,
    volume_currency_24h TEXT NOT NULL,
    received_at TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (instrument_id, timestamp)
);

CREATE TABLE IF NOT EXISTS mark_prices (
    instrument_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    mark_price TEXT NOT NULL,
    received_at TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (instrument_id, timestamp)
);

CREATE TABLE IF NOT EXISTS index_prices (
    index_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    index_price TEXT NOT NULL,
    received_at TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (index_id, timestamp)
);

CREATE TABLE IF NOT EXISTS open_interest (
    instrument_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    contracts TEXT NOT NULL,
    currency TEXT,
    usd TEXT,
    received_at TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (instrument_id, timestamp)
);

CREATE TABLE IF NOT EXISTS funding_rates (
    instrument_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    rate TEXT NOT NULL,
    funding_time TEXT NOT NULL,
    next_rate TEXT,
    next_funding_time TEXT,
    received_at TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (instrument_id, timestamp)
);

CREATE TABLE IF NOT EXISTS order_books (
    instrument_id TEXT NOT NULL,
    sequence_id INTEGER NOT NULL,
    previous_sequence_id INTEGER,
    timestamp TEXT NOT NULL,
    received_at TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (instrument_id, sequence_id)
);

CREATE TABLE IF NOT EXISTS order_book_levels (
    instrument_id TEXT NOT NULL,
    sequence_id INTEGER NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('ask', 'bid')),
    position INTEGER NOT NULL,
    price TEXT NOT NULL,
    size TEXT NOT NULL,
    order_count INTEGER NOT NULL,
    PRIMARY KEY (instrument_id, sequence_id, side, position)
);

CREATE TABLE IF NOT EXISTS candles (
    instrument_id TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    open TEXT NOT NULL,
    high TEXT NOT NULL,
    low TEXT NOT NULL,
    close TEXT NOT NULL,
    volume_contracts TEXT NOT NULL,
    volume_currency TEXT NOT NULL,
    volume_quote TEXT NOT NULL,
    complete INTEGER NOT NULL CHECK(complete IN (0, 1)),
    received_at TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (instrument_id, timeframe, timestamp)
);

CREATE INDEX IF NOT EXISTS ix_candles_latest
ON candles(instrument_id, timeframe, timestamp DESC);
"""


class SQLiteRepository:
    """Dépôt transactionnel utilisant `aiosqlite` derrière une interface générique."""

    def __init__(self, settings: SQLiteSettings) -> None:
        self._settings = settings
        self._connection: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        async with self._lock:
            if self._connection is not None:
                return
            self._settings.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                connection = await aiosqlite.connect(self._settings.path)
                connection.row_factory = aiosqlite.Row
                await connection.execute(f"PRAGMA journal_mode={self._settings.journal_mode}")
                await connection.execute(f"PRAGMA synchronous={self._settings.synchronous}")
                await connection.execute(
                    f"PRAGMA busy_timeout={self._settings.busy_timeout_milliseconds}"
                )
                await connection.execute("PRAGMA foreign_keys=ON")
                await connection.executescript(_SCHEMA)
                await connection.commit()
            except (OSError, sqlite3.Error) as exc:
                raise DataStorageError("Initialisation SQLite impossible.") from exc
            self._connection = connection

    async def write(self, records: Sequence[DataRecord]) -> None:
        if not records:
            return
        async with self._lock:
            connection = self._require_connection()
            try:
                await connection.execute("BEGIN IMMEDIATE")
                await self._write_grouped(connection, records)
                await connection.commit()
            except (OSError, sqlite3.Error, ValueError) as exc:
                await connection.rollback()
                raise DataStorageError("Écriture transactionnelle SQLite impossible.") from exc

    async def latest_candles(
        self,
        instrument_id: str,
        timeframe: str,
        *,
        limit: int,
    ) -> tuple[CandleRecord, ...]:
        if limit < 1:
            raise ValueError("La limite doit être positive.")
        async with self._lock:
            connection = self._require_connection()
            try:
                cursor = await connection.execute(
                    """
                    SELECT * FROM candles
                    WHERE instrument_id = ? AND timeframe = ?
                    ORDER BY timestamp DESC LIMIT ?
                    """,
                    (instrument_id, timeframe, limit),
                )
                rows = await cursor.fetchall()
                await cursor.close()
            except sqlite3.Error as exc:
                raise DataStorageError("Lecture SQLite impossible.") from exc
        return tuple(self._candle_from_row(row) for row in rows)

    async def close(self) -> None:
        async with self._lock:
            connection = self._connection
            self._connection = None
            if connection is not None:
                try:
                    await connection.close()
                except sqlite3.Error as exc:
                    raise DataStorageError("Fermeture SQLite impossible.") from exc

    async def _write_grouped(
        self,
        connection: aiosqlite.Connection,
        records: Sequence[DataRecord],
    ) -> None:
        contracts: list[tuple[object, ...]] = []
        tickers: list[tuple[object, ...]] = []
        marks: list[tuple[object, ...]] = []
        indexes: list[tuple[object, ...]] = []
        interests: list[tuple[object, ...]] = []
        fundings: list[tuple[object, ...]] = []
        books: list[tuple[object, ...]] = []
        levels: list[tuple[object, ...]] = []
        candles: list[tuple[object, ...]] = []

        for record in records:
            if isinstance(record, ContractMetadata):
                contracts.append(self._contract_values(record))
            elif isinstance(record, TickerSnapshot):
                tickers.append(self._ticker_values(record))
            elif isinstance(record, MarkPriceSnapshot):
                marks.append(self._mark_values(record))
            elif isinstance(record, IndexPriceSnapshot):
                indexes.append(self._index_values(record))
            elif isinstance(record, OpenInterestSnapshot):
                interests.append(self._interest_values(record))
            elif isinstance(record, FundingRateSnapshot):
                fundings.append(self._funding_values(record))
            elif isinstance(record, OrderBookSnapshot):
                books.append(self._book_values(record))
                levels.extend(self._level_values(record))
            elif isinstance(record, CandleRecord):
                candles.append(self._candle_values(record))

        await self._execute_many(
            connection,
            """
            INSERT INTO contracts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(instrument_id) DO UPDATE SET
                instrument_family=excluded.instrument_family,
                underlying=excluded.underlying,
                settle_currency=excluded.settle_currency,
                contract_type=excluded.contract_type,
                contract_value=excluded.contract_value,
                contract_value_currency=excluded.contract_value_currency,
                tick_size=excluded.tick_size,
                lot_size=excluded.lot_size,
                minimum_size=excluded.minimum_size,
                maximum_leverage=excluded.maximum_leverage,
                listing_time=excluded.listing_time,
                expiry_time=excluded.expiry_time,
                state=excluded.state,
                observed_at=excluded.observed_at
            """,
            contracts,
        )
        await self._execute_many(
            connection,
            "INSERT OR IGNORE INTO ticker_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
            tickers,
        )
        await self._execute_many(
            connection,
            "INSERT OR IGNORE INTO mark_prices VALUES (?, ?, ?, ?, ?)",
            marks,
        )
        await self._execute_many(
            connection,
            "INSERT OR IGNORE INTO index_prices VALUES (?, ?, ?, ?, ?)",
            indexes,
        )
        await self._execute_many(
            connection,
            "INSERT OR IGNORE INTO open_interest VALUES (?, ?, ?, ?, ?, ?, ?)",
            interests,
        )
        await self._execute_many(
            connection,
            "INSERT OR IGNORE INTO funding_rates VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            fundings,
        )
        await self._execute_many(
            connection,
            "INSERT OR IGNORE INTO order_books VALUES (?, ?, ?, ?, ?, ?)",
            books,
        )
        await self._execute_many(
            connection,
            "INSERT OR REPLACE INTO order_book_levels VALUES (?, ?, ?, ?, ?, ?, ?)",
            levels,
        )
        await self._execute_many(
            connection,
            """
            INSERT INTO candles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(instrument_id, timeframe, timestamp) DO UPDATE SET
                open=excluded.open,
                high=excluded.high,
                low=excluded.low,
                close=excluded.close,
                volume_contracts=excluded.volume_contracts,
                volume_currency=excluded.volume_currency,
                volume_quote=excluded.volume_quote,
                complete=MAX(candles.complete, excluded.complete),
                received_at=excluded.received_at,
                source=excluded.source
            """,
            candles,
        )

    @staticmethod
    async def _execute_many(
        connection: aiosqlite.Connection,
        statement: str,
        values: list[tuple[object, ...]],
    ) -> None:
        if values:
            await connection.executemany(statement, values)

    @staticmethod
    def _contract_values(record: ContractMetadata) -> tuple[object, ...]:
        return (
            record.instrument_id,
            record.instrument_family,
            record.underlying,
            record.settle_currency,
            record.contract_type,
            _decimal(record.contract_value),
            record.contract_value_currency,
            str(record.tick_size),
            str(record.lot_size),
            str(record.minimum_size),
            _decimal(record.maximum_leverage),
            _datetime(record.listing_time),
            _datetime(record.expiry_time),
            record.state,
            _datetime(record.observed_at),
        )

    @staticmethod
    def _ticker_values(record: TickerSnapshot) -> tuple[object, ...]:
        return (
            record.instrument_id,
            _datetime(record.timestamp),
            str(record.last_price),
            str(record.volume_contracts_24h),
            str(record.volume_currency_24h),
            _datetime(record.received_at),
            record.source.value,
        )

    @staticmethod
    def _mark_values(record: MarkPriceSnapshot) -> tuple[object, ...]:
        return (
            record.instrument_id,
            _datetime(record.timestamp),
            str(record.mark_price),
            _datetime(record.received_at),
            record.source.value,
        )

    @staticmethod
    def _index_values(record: IndexPriceSnapshot) -> tuple[object, ...]:
        return (
            record.index_id,
            _datetime(record.timestamp),
            str(record.index_price),
            _datetime(record.received_at),
            record.source.value,
        )

    @staticmethod
    def _interest_values(record: OpenInterestSnapshot) -> tuple[object, ...]:
        return (
            record.instrument_id,
            _datetime(record.timestamp),
            str(record.contracts),
            _decimal(record.currency),
            _decimal(record.usd),
            _datetime(record.received_at),
            record.source.value,
        )

    @staticmethod
    def _funding_values(record: FundingRateSnapshot) -> tuple[object, ...]:
        return (
            record.instrument_id,
            _datetime(record.timestamp),
            str(record.rate),
            _datetime(record.funding_time),
            _decimal(record.next_rate),
            _datetime(record.next_funding_time),
            _datetime(record.received_at),
            record.source.value,
        )

    @staticmethod
    def _book_values(record: OrderBookSnapshot) -> tuple[object, ...]:
        return (
            record.instrument_id,
            record.sequence_id,
            record.previous_sequence_id,
            _datetime(record.timestamp),
            _datetime(record.received_at),
            record.source.value,
        )

    @staticmethod
    def _level_values(record: OrderBookSnapshot) -> list[tuple[object, ...]]:
        return [
            (
                record.instrument_id,
                record.sequence_id,
                side,
                position,
                str(level.price),
                str(level.size),
                level.order_count,
            )
            for side, side_levels in (("ask", record.asks), ("bid", record.bids))
            for position, level in enumerate(side_levels)
        ]

    @staticmethod
    def _candle_values(record: CandleRecord) -> tuple[object, ...]:
        return (
            record.instrument_id,
            record.timeframe,
            _datetime(record.timestamp),
            str(record.open),
            str(record.high),
            str(record.low),
            str(record.close),
            str(record.volume_contracts),
            str(record.volume_currency),
            str(record.volume_quote),
            int(record.complete),
            _datetime(record.received_at),
            record.source.value,
        )

    @staticmethod
    def _candle_from_row(row: aiosqlite.Row) -> CandleRecord:
        return CandleRecord(
            instrument_id=str(row["instrument_id"]),
            timeframe=str(row["timeframe"]),
            timestamp=datetime.fromisoformat(str(row["timestamp"])),
            open=Decimal(str(row["open"])),
            high=Decimal(str(row["high"])),
            low=Decimal(str(row["low"])),
            close=Decimal(str(row["close"])),
            volume_contracts=Decimal(str(row["volume_contracts"])),
            volume_currency=Decimal(str(row["volume_currency"])),
            volume_quote=Decimal(str(row["volume_quote"])),
            complete=bool(row["complete"]),
            received_at=datetime.fromisoformat(str(row["received_at"])),
            source=DataSource(str(row["source"])),
        )

    def _require_connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise DataStorageError("Le dépôt SQLite n'est pas initialisé.")
        return self._connection


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _datetime(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()
