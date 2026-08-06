"""File d'écriture asynchrone bornée et groupée."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from okx_ai_pro.data.exceptions import DataStorageError, DataWriterClosedError
from okx_ai_pro.data.interfaces import DataRepositoryProtocol
from okx_ai_pro.data.models import DataRecord
from okx_ai_pro.logging_config import get_logger
from okx_ai_pro.settings import CollectionSettings


class _Stop:
    pass


_STOP = _Stop()


class AsyncDataWriter:
    """Découple la réception temps réel des transactions de stockage."""

    def __init__(
        self,
        repository: DataRepositoryProtocol,
        settings: CollectionSettings,
    ) -> None:
        self._repository = repository
        self._settings = settings
        self._queue: asyncio.Queue[DataRecord | _Stop] = asyncio.Queue(
            maxsize=settings.writer_queue_size
        )
        self._task: asyncio.Task[None] | None = None
        self._closed = False
        self._failure: BaseException | None = None
        self._logger = get_logger("data.writer")

    async def start(self) -> None:
        """Initialise le dépôt puis démarre un unique consommateur."""
        if self._task is not None and not self._task.done():
            return
        self._closed = False
        self._failure = None
        await self._repository.initialize()
        self._task = asyncio.create_task(self._run(), name="okx-data-writer")

    async def submit(self, record: DataRecord) -> None:
        """Applique la backpressure lorsque la file configurée est pleine."""
        if self._closed:
            raise DataWriterClosedError("Le writer de données est fermé.")
        if self._failure is not None:
            raise DataStorageError("Le writer de données est en échec.") from self._failure
        await self._queue.put(record)

    async def close(self) -> None:
        """Vide la file, arrête le consommateur puis ferme le dépôt."""
        if self._closed:
            return
        self._closed = True
        task = self._task
        self._task = None
        if task is not None and not task.done():
            await self._queue.put(_STOP)
            await task
        elif task is not None:
            with suppress(Exception):
                await task
        await self._repository.close()
        if self._failure is not None:
            raise DataStorageError("Le writer s'est arrêté après un échec.") from self._failure

    async def _run(self) -> None:
        batch: list[DataRecord] = []
        try:
            while True:
                item = await self._next_item(batch)
                if isinstance(item, _Stop):
                    if batch:
                        await self._repository.write(batch)
                    return
                batch.append(item)
                if len(batch) >= self._settings.writer_batch_size:
                    await self._repository.write(batch)
                    batch.clear()
        except Exception as exc:
            self._failure = exc
            self._logger.exception("Arrêt du writer après une erreur de stockage.")

    async def _next_item(self, batch: list[DataRecord]) -> DataRecord | _Stop:
        if not batch:
            return await self._queue.get()
        try:
            return await asyncio.wait_for(
                self._queue.get(),
                timeout=self._settings.writer_flush_interval_seconds,
            )
        except TimeoutError:
            await self._repository.write(batch)
            batch.clear()
            return await self._queue.get()
