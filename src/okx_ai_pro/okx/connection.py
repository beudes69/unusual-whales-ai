"""État et politique de reconnexion indépendants du transport WebSocket."""

from __future__ import annotations

import asyncio
from enum import StrEnum

from okx_ai_pro.okx.exceptions import OkxTimeoutError
from okx_ai_pro.settings import ReconnectSettings


class ConnectionState(StrEnum):
    """États observables d'une connexion longue durée."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    STOPPING = "stopping"
    STOPPED = "stopped"


class ConnectionManager:
    """Synchronise l'état et calcule un backoff de reconnexion borné."""

    def __init__(self, settings: ReconnectSettings) -> None:
        self._settings = settings
        self._state = ConnectionState.DISCONNECTED
        self._attempt = 0
        self._connected = asyncio.Event()
        self._lock = asyncio.Lock()

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def is_connected(self) -> bool:
        return self._state is ConnectionState.CONNECTED

    async def mark_connecting(self) -> None:
        await self._set_state(ConnectionState.CONNECTING)

    async def mark_connected(self) -> None:
        async with self._lock:
            self._state = ConnectionState.CONNECTED
            self._attempt = 0
            self._connected.set()

    async def mark_disconnected(self) -> None:
        async with self._lock:
            self._state = ConnectionState.DISCONNECTED
            self._connected.clear()

    async def mark_stopping(self) -> None:
        await self._set_state(ConnectionState.STOPPING)

    async def mark_stopped(self) -> None:
        await self._set_state(ConnectionState.STOPPED)

    async def next_reconnect_delay(self) -> float:
        """Passe en reconnexion et retourne le prochain délai exponentiel."""
        async with self._lock:
            self._state = ConnectionState.RECONNECTING
            self._connected.clear()
            delay = self._settings.initial_delay_seconds * (
                self._settings.multiplier**self._attempt
            )
            self._attempt += 1
            return min(delay, self._settings.maximum_delay_seconds)

    async def wait_until_connected(self, timeout: float) -> None:
        """Attend une connexion initiale sans pouvoir bloquer indéfiniment."""
        try:
            async with asyncio.timeout(timeout):
                await self._connected.wait()
        except TimeoutError as exc:
            raise OkxTimeoutError(
                f"Connexion WebSocket non établie après {timeout:g} secondes."
            ) from exc

    async def _set_state(self, state: ConnectionState) -> None:
        async with self._lock:
            self._state = state
            if state is not ConnectionState.CONNECTED:
                self._connected.clear()
