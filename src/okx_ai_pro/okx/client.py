"""Façade unique de toutes les communications publiques avec OKX."""

from __future__ import annotations

from collections.abc import Sequence

from okx_ai_pro.okx.interfaces import (
    MessageCallback,
    RestClientProtocol,
    WebSocketClientProtocol,
)
from okx_ai_pro.okx.models import (
    Candle,
    CandleBar,
    FundingRate,
    IndexPrice,
    Instrument,
    MarkPrice,
    OpenInterest,
    OrderBook,
    WebSocketSubscription,
)
from okx_ai_pro.okx.rest import RestClient
from okx_ai_pro.okx.websocket import WebSocketClient
from okx_ai_pro.settings import Settings


class OkxClient:
    """Point d'accès public empêchant les couches métier de joindre OKX directement."""

    def __init__(
        self,
        rest_client: RestClientProtocol,
        websocket_client: WebSocketClientProtocol,
    ) -> None:
        self._rest = rest_client
        self._websocket = websocket_client

    @classmethod
    def from_settings(cls, settings: Settings) -> OkxClient:
        """Assemble les implémentations de production depuis la configuration."""
        return cls(
            RestClient(settings.okx),
            WebSocketClient(settings.okx),
        )

    @property
    def is_connected(self) -> bool:
        return self._websocket.is_connected

    async def start(self) -> None:
        """Démarre le transport temps réel."""
        await self._websocket.start()

    async def get_usdt_swap_contracts(self) -> tuple[Instrument, ...]:
        return await self._rest.get_usdt_swap_contracts()

    async def get_contract(self, instrument_id: str) -> Instrument:
        return await self._rest.get_contract(instrument_id)

    async def get_candles(
        self,
        instrument_id: str,
        *,
        bar: CandleBar = CandleBar.M1,
        limit: int = 100,
        after: str | None = None,
        before: str | None = None,
    ) -> tuple[Candle, ...]:
        return await self._rest.get_candles(
            instrument_id,
            bar=bar,
            limit=limit,
            after=after,
            before=before,
        )

    async def get_funding_rate(self, instrument_id: str) -> FundingRate:
        return await self._rest.get_funding_rate(instrument_id)

    async def get_open_interest(self, instrument_id: str) -> OpenInterest:
        return await self._rest.get_open_interest(instrument_id)

    async def get_order_book(self, instrument_id: str, *, depth: int = 20) -> OrderBook:
        return await self._rest.get_order_book(instrument_id, depth=depth)

    async def get_mark_price(self, instrument_id: str) -> MarkPrice:
        return await self._rest.get_mark_price(instrument_id)

    async def get_index_price(self, index_id: str) -> IndexPrice:
        return await self._rest.get_index_price(index_id)

    async def subscribe(
        self,
        subscription: WebSocketSubscription,
        callback: MessageCallback,
    ) -> None:
        await self._websocket.subscribe(subscription, callback)

    async def subscribe_many(
        self,
        subscriptions: Sequence[tuple[WebSocketSubscription, MessageCallback]],
    ) -> None:
        """Enregistre plusieurs abonnements en commandes groupées."""
        await self._websocket.subscribe_many(subscriptions)

    async def unsubscribe(
        self,
        subscription: WebSocketSubscription,
        callback: MessageCallback | None = None,
    ) -> None:
        await self._websocket.unsubscribe(subscription, callback)

    async def close(self) -> None:
        """Ferme les deux transports même si l'un d'eux échoue."""
        try:
            await self._websocket.close()
        finally:
            await self._rest.close()

    async def __aenter__(self) -> OkxClient:
        await self.start()
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        await self.close()
