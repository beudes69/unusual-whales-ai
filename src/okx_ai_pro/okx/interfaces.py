"""Interfaces remplaçables du moteur de communication."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol, TypeVar

from okx_ai_pro.okx.models import (
    Candle,
    CandleBar,
    FundingRate,
    IndexPrice,
    Instrument,
    MarkPrice,
    OpenInterest,
    OrderBook,
    WebSocketMessage,
    WebSocketSubscription,
)

T = TypeVar("T")
MessageCallback = Callable[[WebSocketMessage], Awaitable[None] | None]


class RateLimiterProtocol(Protocol):
    """Contrat d'un limiteur asynchrone réutilisable."""

    async def acquire(self) -> None:
        """Attend jusqu'à ce qu'une requête soit autorisée."""
        ...


class RetryManagerProtocol(Protocol):
    """Contrat du gestionnaire de nouvelles tentatives."""

    async def execute(self, operation: Callable[[], Awaitable[T]]) -> T:
        """Exécute une opération avec la politique de retry."""
        ...


class ConnectionManagerProtocol(Protocol):
    """Contrat remplaçable de l'état de connexion."""

    @property
    def is_connected(self) -> bool: ...

    async def mark_connecting(self) -> None: ...

    async def mark_connected(self) -> None: ...

    async def mark_disconnected(self) -> None: ...

    async def mark_stopping(self) -> None: ...

    async def mark_stopped(self) -> None: ...

    async def next_reconnect_delay(self) -> float: ...

    async def wait_until_connected(self, timeout: float) -> None: ...


class RestClientProtocol(Protocol):
    """Contrat des données publiques disponibles par REST."""

    async def get_usdt_swap_contracts(self) -> tuple[Instrument, ...]: ...

    async def get_contract(self, instrument_id: str) -> Instrument: ...

    async def get_candles(
        self,
        instrument_id: str,
        *,
        bar: CandleBar,
        limit: int,
        after: str | None = None,
        before: str | None = None,
    ) -> tuple[Candle, ...]: ...

    async def get_funding_rate(self, instrument_id: str) -> FundingRate: ...

    async def get_open_interest(self, instrument_id: str) -> OpenInterest: ...

    async def get_order_book(self, instrument_id: str, *, depth: int) -> OrderBook: ...

    async def get_mark_price(self, instrument_id: str) -> MarkPrice: ...

    async def get_index_price(self, index_id: str) -> IndexPrice: ...

    async def close(self) -> None: ...


class WebSocketClientProtocol(Protocol):
    """Contrat du transport temps réel public."""

    @property
    def is_connected(self) -> bool: ...

    async def start(self) -> None: ...

    async def subscribe(
        self, subscription: WebSocketSubscription, callback: MessageCallback
    ) -> None: ...

    async def subscribe_many(
        self,
        subscriptions: Sequence[tuple[WebSocketSubscription, MessageCallback]],
    ) -> None: ...

    async def unsubscribe(
        self,
        subscription: WebSocketSubscription,
        callback: MessageCallback | None = None,
    ) -> None: ...

    async def close(self) -> None: ...


class WebSocketConnectionProtocol(Protocol):
    """Sous-ensemble testable d'une connexion `websockets`."""

    async def send(self, message: str) -> None: ...

    async def recv(self, decode: bool | None = None) -> str | bytes: ...

    async def close(self) -> None: ...


class WebSocketConnectorProtocol(Protocol):
    """Fabrique asynchrone de connexions WebSocket."""

    def __call__(
        self,
        uri: str,
        *,
        open_timeout: float,
        close_timeout: float,
        ping_interval: None,
        user_agent_header: str,
    ) -> Awaitable[WebSocketConnectionProtocol]: ...


class OkxClientProtocol(RestClientProtocol, WebSocketClientProtocol, Protocol):
    """Point de passage unique de toutes les communications OKX."""
