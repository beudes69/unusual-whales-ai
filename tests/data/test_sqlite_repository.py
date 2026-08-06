"""Tests d'intégration locale du dépôt SQLite."""

from pathlib import Path

import aiosqlite
import pytest

from okx_ai_pro.data.exceptions import DataStorageError
from okx_ai_pro.data.models import CandleRecord, DataRecord
from okx_ai_pro.data.sqlite_repository import SQLiteRepository
from okx_ai_pro.settings import Settings


@pytest.mark.asyncio
async def test_sqlite_stores_every_record_type_transactionally(
    data_settings: Settings,
    sample_records: tuple[DataRecord, ...],
) -> None:
    repository = SQLiteRepository(data_settings.data.sqlite)

    await repository.initialize()
    await repository.initialize()
    await repository.write(sample_records)
    await repository.write(sample_records)
    candles = await repository.latest_candles(
        "BTC-USDT-SWAP",
        "1m",
        limit=10,
    )
    await repository.write(())
    await repository.close()
    await repository.close()

    assert len(candles) == 1
    assert isinstance(candles[0], CandleRecord)
    assert candles[0].complete
    counts = await _table_counts(
        data_settings.data.sqlite.path,
        (
            "contracts",
            "ticker_snapshots",
            "mark_prices",
            "index_prices",
            "open_interest",
            "funding_rates",
            "order_books",
            "order_book_levels",
            "candles",
        ),
    )
    assert counts == {
        "contracts": 1,
        "ticker_snapshots": 1,
        "mark_prices": 1,
        "index_prices": 1,
        "open_interest": 1,
        "funding_rates": 1,
        "order_books": 1,
        "order_book_levels": 2,
        "candles": 1,
    }


@pytest.mark.asyncio
async def test_sqlite_requires_initialization_and_valid_limit(
    data_settings: Settings,
    sample_records: tuple[DataRecord, ...],
) -> None:
    repository = SQLiteRepository(data_settings.data.sqlite)

    with pytest.raises(DataStorageError, match="initialisé"):
        await repository.write(sample_records)
    with pytest.raises(DataStorageError, match="initialisé"):
        await repository.latest_candles("BTC-USDT-SWAP", "1m", limit=1)
    await repository.initialize()
    with pytest.raises(ValueError, match="positive"):
        await repository.latest_candles("BTC-USDT-SWAP", "1m", limit=0)
    assert await repository.latest_candles("ETH-USDT-SWAP", "1m", limit=1) == ()
    await repository.close()


async def _table_counts(
    path: Path,
    tables: tuple[str, ...],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    async with aiosqlite.connect(path) as connection:
        for table in tables:
            cursor = await connection.execute(f"SELECT COUNT(*) FROM {table}")
            row = await cursor.fetchone()
            await cursor.close()
            assert row is not None
            counts[table] = int(row[0])
    return counts
