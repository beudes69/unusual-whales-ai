"""Interfaces remplaçables du moteur de données."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from okx_ai_pro.data.models import CandleRecord, DataRecord


class DataRepositoryProtocol(Protocol):
    """Contrat indépendant du moteur SQL utilisé."""

    async def initialize(self) -> None: ...

    async def write(self, records: Sequence[DataRecord]) -> None: ...

    async def latest_candles(
        self,
        instrument_id: str,
        timeframe: str,
        *,
        limit: int,
    ) -> tuple[CandleRecord, ...]: ...

    async def close(self) -> None: ...


class DataWriterProtocol(Protocol):
    """Contrat d'une file d'écriture non bloquante."""

    async def start(self) -> None: ...

    async def submit(self, record: DataRecord) -> None: ...

    async def close(self) -> None: ...
