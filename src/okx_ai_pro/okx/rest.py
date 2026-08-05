"""Client REST public OKX entièrement asynchrone."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from okx_ai_pro.exceptions import ConfigurationError
from okx_ai_pro.okx.exceptions import (
    OkxApiError,
    OkxApiUnavailableError,
    OkxHttpError,
    OkxInvalidDataError,
    OkxNetworkError,
    OkxRateLimitError,
    OkxRequestError,
    OkxTimeoutError,
)
from okx_ai_pro.okx.interfaces import RateLimiterProtocol, RetryManagerProtocol
from okx_ai_pro.okx.models import (
    Candle,
    CandleBar,
    FundingRate,
    IndexPrice,
    Instrument,
    MarkPrice,
    OpenInterest,
    OrderBook,
)
from okx_ai_pro.okx.rate_limiter import RateLimiter
from okx_ai_pro.okx.retry import RetryManager
from okx_ai_pro.settings import OkxSettings

ModelT = TypeVar("ModelT", bound=BaseModel)

_INSTRUMENTS_PATH = "/api/v5/public/instruments"
_CANDLES_PATH = "/api/v5/market/candles"
_FUNDING_RATE_PATH = "/api/v5/public/funding-rate"
_OPEN_INTEREST_PATH = "/api/v5/public/open-interest"
_ORDER_BOOK_PATH = "/api/v5/market/books"
_MARK_PRICE_PATH = "/api/v5/public/mark-price"
_INDEX_PRICE_PATH = "/api/v5/market/index-tickers"
_REQUIRED_RATE_LIMITS = frozenset(
    {
        "instruments",
        "candles",
        "funding_rate",
        "open_interest",
        "order_book",
        "mark_price",
        "index_price",
    }
)


class RestClient:
    """Implémentation publique REST, isolée derrière `OkxClient`."""

    def __init__(
        self,
        settings: OkxSettings,
        *,
        http_client: httpx.AsyncClient | None = None,
        rate_limiters: Mapping[str, RateLimiterProtocol] | None = None,
        retry_manager: RetryManagerProtocol | None = None,
    ) -> None:
        self._settings = settings
        self._owns_http_client = http_client is None
        self._http_client = http_client or httpx.AsyncClient(
            base_url=str(settings.rest_base_url),
            timeout=httpx.Timeout(settings.request_timeout_seconds),
            headers={
                "Accept": "application/json",
                "User-Agent": settings.user_agent,
            },
        )
        self._rate_limiters = (
            dict(rate_limiters)
            if rate_limiters is not None
            else {key: RateLimiter(policy) for key, policy in settings.rate_limits.items()}
        )
        missing = _REQUIRED_RATE_LIMITS - self._rate_limiters.keys()
        if missing:
            raise ConfigurationError(f"Limites REST OKX absentes : {', '.join(sorted(missing))}")
        self._retry = retry_manager or RetryManager(settings.retry)

    async def get_usdt_swap_contracts(self) -> tuple[Instrument, ...]:
        """Retourne tous les contrats perpétuels réglés en USDT."""
        rows = await self._get(
            _INSTRUMENTS_PATH,
            params={"instType": "SWAP"},
            rate_limit="instruments",
        )
        instruments = self._parse_many(Instrument, rows)
        return tuple(
            instrument
            for instrument in instruments
            if instrument.instrument_type == "SWAP"
            and instrument.settle_currency == "USDT"
            and instrument.instrument_id.endswith("-USDT-SWAP")
        )

    async def get_contract(self, instrument_id: str) -> Instrument:
        """Retourne les métadonnées d'un contrat USDT-SWAP."""
        self._validate_instrument_id(instrument_id, require_swap=True)
        rows = await self._get(
            _INSTRUMENTS_PATH,
            params={"instType": "SWAP", "instId": instrument_id},
            rate_limit="instruments",
        )
        instrument = self._parse_one(Instrument, rows, "instrument")
        if instrument.settle_currency != "USDT":
            raise OkxInvalidDataError(f"Le contrat {instrument_id} n'est pas réglé en USDT.")
        return instrument

    async def get_candles(
        self,
        instrument_id: str,
        *,
        bar: CandleBar = CandleBar.M1,
        limit: int = 100,
        after: str | None = None,
        before: str | None = None,
    ) -> tuple[Candle, ...]:
        """Retourne jusqu'à 300 bougies récentes."""
        self._validate_instrument_id(instrument_id)
        if not 1 <= limit <= 300:
            raise OkxRequestError("La limite de bougies doit être comprise entre 1 et 300.")
        params = self._without_none(
            {
                "instId": instrument_id,
                "bar": bar.value,
                "limit": str(limit),
                "after": after,
                "before": before,
            }
        )
        rows = await self._get(_CANDLES_PATH, params=params, rate_limit="candles")
        try:
            return tuple(Candle.from_api(self._expect_row(row)) for row in rows)
        except (ArithmeticError, TypeError, ValueError, ValidationError) as exc:
            raise OkxInvalidDataError("Bougies OKX invalides.") from exc

    async def get_funding_rate(self, instrument_id: str) -> FundingRate:
        """Retourne le taux de financement actuel."""
        self._validate_instrument_id(instrument_id, require_swap=True)
        rows = await self._get(
            _FUNDING_RATE_PATH,
            params={"instId": instrument_id},
            rate_limit="funding_rate",
        )
        return self._parse_one(FundingRate, rows, "funding")

    async def get_open_interest(self, instrument_id: str) -> OpenInterest:
        """Retourne l'open interest du contrat."""
        self._validate_instrument_id(instrument_id, require_swap=True)
        rows = await self._get(
            _OPEN_INTEREST_PATH,
            params={"instType": "SWAP", "instId": instrument_id},
            rate_limit="open_interest",
        )
        return self._parse_one(OpenInterest, rows, "open interest")

    async def get_order_book(self, instrument_id: str, *, depth: int = 20) -> OrderBook:
        """Retourne un instantané du carnet, jusqu'à 400 niveaux par côté."""
        self._validate_instrument_id(instrument_id)
        if not 1 <= depth <= 400:
            raise OkxRequestError("La profondeur du carnet doit être comprise entre 1 et 400.")
        rows = await self._get(
            _ORDER_BOOK_PATH,
            params={"instId": instrument_id, "sz": str(depth)},
            rate_limit="order_book",
        )
        payload = self._expect_mapping(self._single_row(rows, "carnet"))
        try:
            return OrderBook.from_api(payload)
        except (ArithmeticError, TypeError, ValueError, ValidationError) as exc:
            raise OkxInvalidDataError("Carnet d'ordres OKX invalide.") from exc

    async def get_mark_price(self, instrument_id: str) -> MarkPrice:
        """Retourne le mark price du contrat."""
        self._validate_instrument_id(instrument_id, require_swap=True)
        rows = await self._get(
            _MARK_PRICE_PATH,
            params={"instType": "SWAP", "instId": instrument_id},
            rate_limit="mark_price",
        )
        return self._parse_one(MarkPrice, rows, "mark price")

    async def get_index_price(self, index_id: str) -> IndexPrice:
        """Retourne le prix d'un indice, par exemple `BTC-USDT`."""
        self._validate_instrument_id(index_id)
        rows = await self._get(
            _INDEX_PRICE_PATH,
            params={"instId": index_id},
            rate_limit="index_price",
        )
        return self._parse_one(IndexPrice, rows, "index price")

    async def close(self) -> None:
        """Ferme le transport HTTP créé par ce client."""
        if self._owns_http_client:
            await self._http_client.aclose()

    async def _get(
        self,
        path: str,
        *,
        params: Mapping[str, str],
        rate_limit: str,
    ) -> list[object]:
        async def request() -> list[object]:
            await self._rate_limiters[rate_limit].acquire()
            try:
                response = await self._http_client.get(path, params=params)
            except httpx.TimeoutException as exc:
                raise OkxTimeoutError(f"Timeout REST OKX sur {path}.") from exc
            except httpx.RequestError as exc:
                raise OkxNetworkError(f"Erreur réseau REST OKX sur {path}.") from exc
            self._raise_for_http_status(response)
            return self._decode_envelope(response)

        return await self._retry.execute(request)

    @staticmethod
    def _raise_for_http_status(response: httpx.Response) -> None:
        if response.status_code == 429:
            retry_after = RestClient._parse_retry_after(response)
            raise OkxRateLimitError(
                "Limite de débit REST OKX atteinte.",
                retry_after=retry_after,
            )
        if response.status_code >= 500:
            raise OkxApiUnavailableError(f"API OKX indisponible (HTTP {response.status_code}).")
        if response.is_error:
            raise OkxHttpError(
                f"Erreur HTTP OKX {response.status_code}.",
                status_code=response.status_code,
            )

    @staticmethod
    def _decode_envelope(response: httpx.Response) -> list[object]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise OkxInvalidDataError("La réponse OKX n'est pas un JSON valide.") from exc
        if not isinstance(payload, dict):
            raise OkxInvalidDataError("L'enveloppe JSON OKX doit être un objet.")

        code = str(payload.get("code", ""))
        message = str(payload.get("msg", "")).strip() or "Erreur API OKX sans détail."
        if code == "50011":
            raise OkxRateLimitError(message)
        if code in {"50001", "50004"}:
            raise OkxApiUnavailableError(message)
        if code != "0":
            raise OkxApiError(message, code=code)

        data = payload.get("data")
        if not isinstance(data, list):
            raise OkxInvalidDataError("Le champ data de la réponse OKX doit être une liste.")
        return data

    @staticmethod
    def _parse_many(model: type[ModelT], rows: list[object]) -> tuple[ModelT, ...]:
        try:
            return tuple(model.model_validate(row) for row in rows)
        except ValidationError as exc:
            raise OkxInvalidDataError(f"Données {model.__name__} invalides.") from exc

    @classmethod
    def _parse_one(cls, model: type[ModelT], rows: list[object], label: str) -> ModelT:
        row = cls._single_row(rows, label)
        try:
            return model.model_validate(row)
        except ValidationError as exc:
            raise OkxInvalidDataError(f"Données {label} invalides.") from exc

    @staticmethod
    def _single_row(rows: list[object], label: str) -> object:
        if len(rows) != 1:
            raise OkxInvalidDataError(f"La réponse {label} doit contenir exactement un élément.")
        return rows[0]

    @staticmethod
    def _expect_row(row: object) -> list[object] | tuple[object, ...]:
        if not isinstance(row, (list, tuple)):
            raise OkxInvalidDataError("Une ligne positionnelle OKX est invalide.")
        return row

    @staticmethod
    def _expect_mapping(row: object) -> dict[str, object]:
        if not isinstance(row, dict) or not all(isinstance(key, str) for key in row):
            raise OkxInvalidDataError("Un objet de données OKX est invalide.")
        return row

    @staticmethod
    def _without_none(values: Mapping[str, str | None]) -> dict[str, str]:
        return {key: value for key, value in values.items() if value is not None}

    @staticmethod
    def _parse_retry_after(response: httpx.Response) -> float | None:
        value = response.headers.get("Retry-After")
        if value is None:
            return None
        try:
            return max(float(value), 0.0)
        except ValueError:
            return None

    @staticmethod
    def _validate_instrument_id(instrument_id: str, *, require_swap: bool = False) -> None:
        if not instrument_id or instrument_id != instrument_id.upper():
            raise OkxRequestError("L'identifiant OKX doit être non vide et en majuscules.")
        if require_swap and not instrument_id.endswith("-USDT-SWAP"):
            raise OkxRequestError("Cette méthode exige un identifiant de contrat `*-USDT-SWAP`.")
