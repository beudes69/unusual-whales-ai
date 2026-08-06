"""Détection et journalisation des anomalies de données."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from okx_ai_pro.data.cache import MemoryCache
from okx_ai_pro.data.models import (
    CandleRecord,
    ContractMetadata,
    DataRecord,
    FundingRateSnapshot,
    OpenInterestSnapshot,
    OrderBookSnapshot,
    record_identity,
)
from okx_ai_pro.logging_config import get_logger
from okx_ai_pro.settings import DataQualitySettings


class AnomalyKind(StrEnum):
    MISSING = "missing"
    DUPLICATE = "duplicate"
    INCOHERENT = "incoherent"
    INVALID_TIMESTAMP = "invalid_timestamp"
    INCOMPLETE_CANDLE = "incomplete_candle"


@dataclass(frozen=True, slots=True)
class DataAnomaly:
    kind: AnomalyKind
    identity: str
    message: str
    fatal: bool


class DataQualityValidator:
    """Contrôles déterministes sans dépendance au stockage."""

    def __init__(
        self,
        settings: DataQualitySettings,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._now = now or (lambda: datetime.now(UTC))

    def inspect(self, record: DataRecord) -> tuple[DataAnomaly, ...]:
        identity = record_identity(record)
        anomalies: list[DataAnomaly] = []
        timestamp = record.observed_at if isinstance(record, ContractMetadata) else record.timestamp
        if timestamp < self._settings.minimum_timestamp or timestamp > (
            self._now() + timedelta(seconds=self._settings.maximum_future_seconds)
        ):
            anomalies.append(
                DataAnomaly(
                    AnomalyKind.INVALID_TIMESTAMP,
                    identity,
                    "Timestamp hors des bornes configurées.",
                    True,
                )
            )

        if isinstance(record, CandleRecord) and not record.complete:
            anomalies.append(
                DataAnomaly(
                    AnomalyKind.INCOMPLETE_CANDLE,
                    identity,
                    "Bougie encore incomplète.",
                    False,
                )
            )
        if isinstance(record, OrderBookSnapshot):
            if not record.asks or not record.bids:
                anomalies.append(
                    DataAnomaly(
                        AnomalyKind.MISSING,
                        identity,
                        "Un côté du carnet est vide.",
                        False,
                    )
                )
            elif record.bids[0].price > record.asks[0].price:
                anomalies.append(
                    DataAnomaly(
                        AnomalyKind.INCOHERENT,
                        identity,
                        "Le meilleur bid dépasse le meilleur ask.",
                        True,
                    )
                )
        if isinstance(record, OpenInterestSnapshot) and (
            record.currency is None or record.usd is None
        ):
            anomalies.append(
                DataAnomaly(
                    AnomalyKind.MISSING,
                    identity,
                    "Une unité d'open interest est absente.",
                    False,
                )
            )
        if (
            isinstance(record, FundingRateSnapshot)
            and record.next_funding_time is not None
            and record.next_funding_time < record.funding_time
        ):
            anomalies.append(
                DataAnomaly(
                    AnomalyKind.INCOHERENT,
                    identity,
                    "La prochaine échéance de funding précède l'échéance courante.",
                    True,
                )
            )
        return tuple(anomalies)


class DataQualityGate:
    """Déduplique, journalise puis accepte ou refuse une observation."""

    def __init__(
        self,
        validator: DataQualityValidator,
        seen_records: MemoryCache[str, DataRecord],
    ) -> None:
        self._validator = validator
        self._seen_records = seen_records
        self._logger = get_logger("data.quality")

    async def accept(self, record: DataRecord) -> bool:
        identity = record_identity(record)
        previous = await self._seen_records.get(identity)
        if previous is not None and not self._is_candle_completion(previous, record):
            self._log(
                DataAnomaly(
                    AnomalyKind.DUPLICATE,
                    identity,
                    "Observation dupliquée.",
                    True,
                )
            )
            return False

        anomalies = self._validator.inspect(record)
        for anomaly in anomalies:
            self._log(anomaly)
        if any(anomaly.fatal for anomaly in anomalies):
            return False
        await self._seen_records.set(identity, record)
        return True

    def _log(self, anomaly: DataAnomaly) -> None:
        self._logger.warning(
            "Anomalie données kind=%s identity=%s fatal=%s message=%s",
            anomaly.kind,
            anomaly.identity,
            anomaly.fatal,
            anomaly.message,
        )

    @staticmethod
    def _is_candle_completion(previous: DataRecord, current: DataRecord) -> bool:
        return (
            isinstance(previous, CandleRecord)
            and isinstance(current, CandleRecord)
            and not previous.complete
            and current.complete
        )
