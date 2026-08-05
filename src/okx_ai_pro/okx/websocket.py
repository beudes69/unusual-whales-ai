"""Client WebSocket public OKX avec heartbeat et reconnexion."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import cast

from pydantic import ValidationError
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, WebSocketException

from okx_ai_pro.logging_config import get_logger
from okx_ai_pro.okx.connection import ConnectionManager
from okx_ai_pro.okx.exceptions import (
    OkxCommunicationError,
    OkxInvalidDataError,
    OkxNetworkError,
    OkxSubscriptionError,
    OkxTimeoutError,
    OkxWebSocketClosedError,
    OkxWebSocketError,
)
from okx_ai_pro.okx.interfaces import (
    MessageCallback,
    RateLimiterProtocol,
    WebSocketConnectionProtocol,
    WebSocketConnectorProtocol,
)
from okx_ai_pro.okx.models import WebSocketMessage, WebSocketSubscription
from okx_ai_pro.okx.rate_limiter import RateLimiter
from okx_ai_pro.settings import OkxSettings

SleepCallable = Callable[[float], Awaitable[None]]


async def default_websocket_connector(
    uri: str,
    *,
    open_timeout: float,
    close_timeout: float,
    ping_interval: None,
) -> WebSocketConnectionProtocol:
    """Adapte la fabrique `websockets` à l'interface remplaçable du projet."""
    connection = await connect(
        uri,
        open_timeout=open_timeout,
        close_timeout=close_timeout,
        ping_interval=ping_interval,
    )
    return cast(WebSocketConnectionProtocol, connection)


class WebSocketClient:
    """Maintient un canal public et restaure ses abonnements après reconnexion."""

    def __init__(
        self,
        settings: OkxSettings,
        *,
        connector: WebSocketConnectorProtocol = default_websocket_connector,
        connection_manager: ConnectionManager | None = None,
        subscription_limiter: RateLimiterProtocol | None = None,
        sleep: SleepCallable = asyncio.sleep,
    ) -> None:
        self._settings = settings
        self._connector = connector
        self._connection_manager = connection_manager or ConnectionManager(settings.reconnect)
        self._subscription_limiter = subscription_limiter or RateLimiter(
            settings.subscription_limit
        )
        self._sleep = sleep
        self._subscriptions: dict[WebSocketSubscription, list[MessageCallback]] = {}
        self._subscriptions_lock = asyncio.Lock()
        self._connection: WebSocketConnectionProtocol | None = None
        self._runner: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._logger = get_logger("okx.websocket")

    @property
    def is_connected(self) -> bool:
        return self._connection_manager.is_connected

    async def start(self) -> None:
        """Démarre la boucle réseau et attend la première connexion."""
        if self._runner is not None and not self._runner.done():
            return
        self._stopping.clear()
        self._runner = asyncio.create_task(
            self._run(),
            name="okx-public-websocket",
        )
        try:
            await self._connection_manager.wait_until_connected(
                self._settings.websocket_open_timeout_seconds
            )
        except OkxTimeoutError:
            await self.close()
            raise

    async def subscribe(
        self,
        subscription: WebSocketSubscription,
        callback: MessageCallback,
    ) -> None:
        """Enregistre un callback et abonne le canal s'il est connecté."""
        should_send = False
        async with self._subscriptions_lock:
            callbacks = self._subscriptions.setdefault(subscription, [])
            if callback not in callbacks:
                should_send = not callbacks
                callbacks.append(callback)
        if should_send and self.is_connected:
            await self._send_operation("subscribe", subscription)

    async def unsubscribe(
        self,
        subscription: WebSocketSubscription,
        callback: MessageCallback | None = None,
    ) -> None:
        """Retire un callback, ou tous les callbacks de l'abonnement."""
        should_send = False
        async with self._subscriptions_lock:
            callbacks = self._subscriptions.get(subscription)
            if callbacks is None:
                return
            if callback is None:
                self._subscriptions.pop(subscription)
                should_send = True
            elif callback in callbacks:
                callbacks.remove(callback)
                if not callbacks:
                    self._subscriptions.pop(subscription)
                    should_send = True
        if should_send and self.is_connected:
            await self._send_operation("unsubscribe", subscription)

    async def close(self) -> None:
        """Arrête proprement la reconnexion et ferme le canal actif."""
        self._stopping.set()
        await self._connection_manager.mark_stopping()
        connection = self._connection
        if connection is not None:
            with suppress(Exception):
                await connection.close()
        runner = self._runner
        self._runner = None
        if runner is not None and runner is not asyncio.current_task():
            runner.cancel()
            with suppress(asyncio.CancelledError):
                await runner
        self._connection = None
        await self._connection_manager.mark_stopped()

    async def _run(self) -> None:
        first_connection = True
        try:
            while not self._stopping.is_set():
                if first_connection:
                    await self._connection_manager.mark_connecting()
                    first_connection = False
                try:
                    connection = await self._connector(
                        str(self._settings.websocket_public_url),
                        open_timeout=self._settings.websocket_open_timeout_seconds,
                        close_timeout=self._settings.websocket_close_timeout_seconds,
                        ping_interval=None,
                    )
                    self._connection = connection
                    await self._connection_manager.mark_connected()
                    self._logger.info("WebSocket public OKX connecté.")
                    await self._restore_subscriptions()
                    await self._receive_loop(connection)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    error = self._translate_transport_error(exc)
                    self._logger.warning("Connexion WebSocket OKX interrompue : %s", error)
                finally:
                    await self._close_current_connection()

                if self._stopping.is_set():
                    break
                delay = await self._connection_manager.next_reconnect_delay()
                self._logger.info("Reconnexion WebSocket OKX dans %.3f s.", delay)
                await self._sleep(delay)
        except asyncio.CancelledError:
            pass
        finally:
            await self._connection_manager.mark_stopped()

    async def _receive_loop(self, connection: WebSocketConnectionProtocol) -> None:
        while not self._stopping.is_set():
            try:
                raw_message = await self._receive_with_heartbeat(connection)
            except OkxInvalidDataError as exc:
                self._logger.warning("Message WebSocket OKX ignoré : %s", exc)
                continue
            if raw_message == "pong":
                continue
            try:
                await self._process_message(raw_message)
            except (OkxInvalidDataError, OkxSubscriptionError) as exc:
                self._logger.warning("Message WebSocket OKX ignoré : %s", exc)

    async def _receive_with_heartbeat(self, connection: WebSocketConnectionProtocol) -> str:
        try:
            raw = await asyncio.wait_for(
                connection.recv(),
                timeout=self._settings.websocket_receive_timeout_seconds,
            )
        except TimeoutError:
            try:
                await connection.send("ping")
                raw = await asyncio.wait_for(
                    connection.recv(),
                    timeout=self._settings.websocket_heartbeat_timeout_seconds,
                )
            except TimeoutError as exc:
                raise OkxWebSocketClosedError("Aucun pong reçu du WebSocket OKX.") from exc
            except (ConnectionClosed, OSError, WebSocketException) as exc:
                raise OkxWebSocketClosedError("WebSocket OKX fermé pendant le heartbeat.") from exc
        except (ConnectionClosed, OSError, WebSocketException) as exc:
            raise OkxWebSocketClosedError("WebSocket OKX fermé.") from exc

        if isinstance(raw, bytes):
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise OkxInvalidDataError("Message WebSocket binaire non UTF-8.") from exc
        return raw

    async def _process_message(self, raw_message: str) -> None:
        try:
            payload = json.loads(raw_message)
        except json.JSONDecodeError as exc:
            raise OkxInvalidDataError("Message WebSocket non JSON.") from exc
        if not isinstance(payload, dict):
            raise OkxInvalidDataError("Le message WebSocket doit être un objet.")

        event = payload.get("event")
        if event is not None:
            if event in {"error", "channel-conn-count-error"}:
                code = str(payload.get("code", ""))
                error_message = str(payload.get("msg", "Abonnement refusé par OKX."))
                raise OkxSubscriptionError(f"{code} {error_message}".strip())
            return

        argument = payload.get("arg")
        data = payload.get("data")
        if not isinstance(argument, dict) or not isinstance(data, list):
            raise OkxInvalidDataError("Message de données WebSocket incomplet.")
        if not all(isinstance(item, dict) for item in data):
            raise OkxInvalidDataError("Données WebSocket invalides.")

        try:
            subscription = WebSocketSubscription.model_validate(argument)
            message = WebSocketMessage(
                subscription=subscription,
                action=(str(payload["action"]) if payload.get("action") is not None else None),
                data=tuple(cast(dict[str, object], item) for item in data),
            )
        except ValidationError as exc:
            raise OkxInvalidDataError("Contrat WebSocket OKX invalide.") from exc

        async with self._subscriptions_lock:
            callbacks = tuple(self._subscriptions.get(subscription, ()))
        for callback in callbacks:
            try:
                result = callback(message)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                self._logger.exception(
                    "Erreur isolée dans un callback WebSocket (%s).",
                    subscription.channel,
                )

    async def _restore_subscriptions(self) -> None:
        async with self._subscriptions_lock:
            subscriptions = tuple(self._subscriptions)
        for subscription in subscriptions:
            await self._send_operation("subscribe", subscription)

    async def _send_operation(self, operation: str, subscription: WebSocketSubscription) -> None:
        connection = self._connection
        if connection is None:
            raise OkxWebSocketClosedError("WebSocket OKX non connecté.")
        await self._subscription_limiter.acquire()
        payload = json.dumps(
            {
                "op": operation,
                "args": [subscription.as_api_argument()],
            },
            separators=(",", ":"),
        )
        try:
            await connection.send(payload)
        except (ConnectionClosed, OSError, WebSocketException) as exc:
            raise OkxWebSocketClosedError(f"Impossible d'envoyer l'opération {operation}.") from exc

    async def _close_current_connection(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is not None:
            with suppress(Exception):
                await connection.close()
        await self._connection_manager.mark_disconnected()

    @staticmethod
    def _translate_transport_error(error: Exception) -> OkxCommunicationError:
        if isinstance(error, OkxWebSocketError):
            return error
        if isinstance(error, TimeoutError):
            return OkxTimeoutError("Timeout de connexion WebSocket OKX.")
        if isinstance(error, (ConnectionClosed, WebSocketException, OSError)):
            return OkxWebSocketClosedError("Connexion WebSocket OKX interrompue.")
        if isinstance(error, OkxNetworkError):
            return OkxWebSocketClosedError(str(error))
        return OkxWebSocketError(f"Erreur WebSocket contrôlée : {type(error).__name__}.")
