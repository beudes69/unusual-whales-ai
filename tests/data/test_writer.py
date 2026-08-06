"""Tests de la file d'écriture indépendante du dépôt."""

import asyncio
from collections.abc import Sequence

import pytest

from okx_ai_pro.data.exceptions import DataStorageError, DataWriterClosedError
from okx_ai_pro.data.models import CandleRecord, DataRecord
from okx_ai_pro.data.writer import AsyncDataWriter
from okx_ai_pro.settings import CollectionSettings, Settings


class FakeRepository:
    def __init__(self, *, fail: bool = False) -> None:
        self.initialized = False
        self.closed = False
        self.fail = fail
        self.batches: list[tuple[DataRecord, ...]] = []
        self.write_called = asyncio.Event()

    async def initialize(self) -> None:
        self.initialized = True

    async def write(self, records: Sequence[DataRecord]) -> None:
        self.write_called.set()
        if self.fail:
            raise DataStorageError("disk full")
        self.batches.append(tuple(records))

    async def latest_candles(
        self,
        instrument_id: str,
        timeframe: str,
        *,
        limit: int,
    ) -> tuple[CandleRecord, ...]:
        return ()

    async def close(self) -> None:
        self.closed = True


def _writer_settings(
    settings: Settings,
    *,
    batch_size: int,
    flush_interval: float,
) -> CollectionSettings:
    return settings.data.collection.model_copy(
        update={
            "writer_batch_size": batch_size,
            "writer_flush_interval_seconds": flush_interval,
        }
    )


@pytest.mark.asyncio
async def test_writer_batches_and_flushes_remaining_records(
    data_settings: Settings,
    sample_records: tuple[DataRecord, ...],
) -> None:
    repository = FakeRepository()
    writer = AsyncDataWriter(
        repository,
        _writer_settings(data_settings, batch_size=2, flush_interval=10.0),
    )

    await writer.start()
    await writer.start()
    await writer.submit(sample_records[0])
    await writer.submit(sample_records[1])
    await writer.submit(sample_records[2])
    await writer.close()
    await writer.close()

    assert repository.initialized
    assert repository.closed
    assert [len(batch) for batch in repository.batches] == [2, 1]
    with pytest.raises(DataWriterClosedError):
        await writer.submit(sample_records[0])


@pytest.mark.asyncio
async def test_writer_flushes_partial_batch_on_timeout(
    data_settings: Settings,
    sample_records: tuple[DataRecord, ...],
) -> None:
    repository = FakeRepository()
    writer = AsyncDataWriter(
        repository,
        _writer_settings(data_settings, batch_size=10, flush_interval=0.001),
    )

    await writer.start()
    await writer.submit(sample_records[0])
    await asyncio.wait_for(repository.write_called.wait(), 1)
    await writer.close()

    assert len(repository.batches) == 1


@pytest.mark.asyncio
async def test_writer_surfaces_repository_failure(
    data_settings: Settings,
    sample_records: tuple[DataRecord, ...],
) -> None:
    repository = FakeRepository(fail=True)
    writer = AsyncDataWriter(
        repository,
        _writer_settings(data_settings, batch_size=1, flush_interval=1.0),
    )

    await writer.start()
    await writer.submit(sample_records[0])
    await asyncio.wait_for(repository.write_called.wait(), 1)
    await asyncio.sleep(0)
    with pytest.raises(DataStorageError, match="échec"):
        await writer.submit(sample_records[1])
    with pytest.raises(DataStorageError, match="arrêté"):
        await writer.close()
    assert repository.closed
