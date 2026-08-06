"""Hiérarchie des erreurs contrôlées du moteur de communication OKX."""

from __future__ import annotations

from okx_ai_pro.exceptions import OkxAiProError


class OkxCommunicationError(OkxAiProError):
    """Base de toutes les erreurs de communication avec OKX."""


class OkxTimeoutError(OkxCommunicationError):
    """Un échange réseau n'a pas abouti dans le délai configuré."""


class OkxNetworkError(OkxCommunicationError):
    """Le transport réseau n'a pas pu joindre OKX."""


class OkxApiUnavailableError(OkxCommunicationError):
    """Le service OKX est temporairement indisponible."""


class OkxHttpError(OkxCommunicationError):
    """OKX a répondu avec un statut HTTP non récupérable."""

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class OkxApiError(OkxCommunicationError):
    """L'enveloppe OKX contient un code fonctionnel en erreur."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class OkxRateLimitError(OkxCommunicationError):
    """Une limite de débit locale ou distante a été atteinte."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class OkxInvalidDataError(OkxCommunicationError):
    """Une réponse reçue ne respecte pas le contrat de données attendu."""


class OkxRequestError(OkxCommunicationError):
    """Les paramètres fournis au client sont invalides."""


class OkxWebSocketError(OkxCommunicationError):
    """Base des erreurs du canal WebSocket public."""


class OkxWebSocketClosedError(OkxWebSocketError):
    """Le canal WebSocket a été fermé ou ne répond plus."""


class OkxSubscriptionError(OkxWebSocketError):
    """OKX a refusé un abonnement WebSocket."""
