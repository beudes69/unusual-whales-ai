"""Tests hors ligne du client REST OKX."""

from collections.abc import Callable
from decimal import Decimal

import httpx
import pytest

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
from okx_ai_pro.okx.models import CandleBar
from okx_ai_pro.okx.rest import RestClient
from okx_ai_pro.settings import OkxSettings

Handler = Callable[[httpx.Request], httpx.Response]


class NoOpLimiter:
    def __init__(self) -> None:
        self.calls = 0

    async def acquire(self) -> None:
        self.calls += 1


def _envelope(data: list[object], *, code: str = "0", message: str = "") -> dict[str, object]:
    return {"code": code, "msg": message, "data": data}


def _limiters(limiter: NoOpLimiter | None = None) -> dict[str, NoOpLimiter]:
    shared = limiter or NoOpLimiter()
    return {
        key: shared
        for key in (
            "instruments",
            "candles",
            "funding_rate",
            "open_interest",
            "order_book",
            "mark_price",
            "index_price",
        )
    }


@pytest.mark.asyncio
async def test_rest_parses_all_supported_public_endpoints(
    okx_settings: OkxSettings,
    instrument_payload: dict[str, str],
) -> None:
    requests: list[httpx.Request] = []
    usdc_instrument = {
        **instrument_payload,
        "instId": "BTC-USDC-SWAP",
        "instFamily": "BTC-USDC",
        "uly": "BTC-USDC",
        "settleCcy": "USDC",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path.endswith("/public/instruments"):
            data = (
                [instrument_payload]
                if "instId" in request.url.params
                else [instrument_payload, usdc_instrument]
            )
        elif path.endswith("/market/candles"):
            data = [["1597026383085", "100", "110", "90", "105", "12", "1.2", "1260", "1"]]
        elif path.endswith("/public/funding-rate"):
            data = [
                {
                    "instId": "BTC-USDT-SWAP",
                    "fundingRate": "0.0001",
                    "fundingTime": "1597026383085",
                    "nextFundingRate": "",
                    "nextFundingTime": "1597055183085",
                }
            ]
        elif path.endswith("/public/open-interest"):
            data = [
                {
                    "instId": "BTC-USDT-SWAP",
                    "instType": "SWAP",
                    "oi": "1000",
                    "oiCcy": "10",
                    "oiUsd": "1000000",
                    "ts": "1597026383085",
                }
            ]
        elif path.endswith("/market/books"):
            data = [
                {
                    "asks": [["101", "2", "0", "3"]],
                    "bids": [["100", "4", "0", "2"]],
                    "ts": "1597026383085",
                    "seqId": "12",
                }
            ]
        elif path.endswith("/public/mark-price"):
            data = [
                {
                    "instId": "BTC-USDT-SWAP",
                    "instType": "SWAP",
                    "markPx": "100.5",
                    "ts": "1597026383085",
                }
            ]
        elif path.endswith("/market/index-tickers"):
            data = [
                {
                    "instId": "BTC-USDT",
                    "idxPx": "100.2",
                    "high24h": "110",
                    "low24h": "90",
                    "open24h": "95",
                    "ts": "1597026383085",
                }
            ]
        else:
            raise AssertionError(f"Chemin inattendu : {path}")
        return httpx.Response(200, json=_envelope(data))

    limiter = NoOpLimiter()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=str(okx_settings.rest_base_url),
    ) as http_client:
        client = RestClient(
            okx_settings,
            http_client=http_client,
            rate_limiters=_limiters(limiter),
        )

        contracts = await client.get_usdt_swap_contracts()
        contract = await client.get_contract("BTC-USDT-SWAP")
        candles = await client.get_candles(
            "BTC-USDT-SWAP",
            bar=CandleBar.M5,
            limit=25,
            after="100",
        )
        funding = await client.get_funding_rate("BTC-USDT-SWAP")
        open_interest = await client.get_open_interest("BTC-USDT-SWAP")
        book = await client.get_order_book("BTC-USDT-SWAP", depth=5)
        mark = await client.get_mark_price("BTC-USDT-SWAP")
        index = await client.get_index_price("BTC-USDT")
        await client.close()

    assert [item.instrument_id for item in contracts] == ["BTC-USDT-SWAP"]
    assert contract.tick_size == Decimal("0.1")
    assert candles[0].confirmed
    assert funding.funding_rate == Decimal("0.0001")
    assert open_interest.open_interest_usd == Decimal("1000000")
    assert book.bids[0].price == Decimal("100")
    assert mark.mark_price == Decimal("100.5")
    assert index.index_price == Decimal("100.2")
    assert limiter.calls == 8
    candle_request = next(
        request for request in requests if request.url.path.endswith("/market/candles")
    )
    assert candle_request.url.params["bar"] == "5m"
    assert candle_request.url.params["limit"] == "25"
    assert "before" not in candle_request.url.params


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response_factory", "expected_error"),
    [
        (lambda request: httpx.Response(429, headers={"Retry-After": "2.5"}), OkxRateLimitError),
        (lambda request: httpx.Response(503), OkxApiUnavailableError),
        (lambda request: httpx.Response(400), OkxHttpError),
        (
            lambda request: httpx.Response(
                200,
                json=_envelope([], code="51000", message="Parameter error"),
            ),
            OkxApiError,
        ),
        (
            lambda request: httpx.Response(
                200,
                json=_envelope([], code="50011", message="Rate limit"),
            ),
            OkxRateLimitError,
        ),
    ],
)
async def test_rest_translates_http_and_api_errors(
    okx_settings: OkxSettings,
    response_factory: Handler,
    expected_error: type[Exception],
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(response_factory),
        base_url=str(okx_settings.rest_base_url),
    ) as http_client:
        client = RestClient(
            okx_settings,
            http_client=http_client,
            rate_limiters=_limiters(),
        )
        with pytest.raises(expected_error):
            await client.get_mark_price("BTC-USDT-SWAP")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        ("timeout", OkxTimeoutError),
        ("network", OkxNetworkError),
    ],
)
async def test_rest_translates_transport_errors(
    okx_settings: OkxSettings,
    raised: str,
    expected: type[Exception],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if raised == "timeout":
            raise httpx.ReadTimeout("late", request=request)
        raise httpx.ConnectError("offline", request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=str(okx_settings.rest_base_url),
    ) as http_client:
        client = RestClient(
            okx_settings,
            http_client=http_client,
            rate_limiters=_limiters(),
        )
        with pytest.raises(expected):
            await client.get_mark_price("BTC-USDT-SWAP")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler",
    [
        lambda request: httpx.Response(200, content=b"not-json"),
        lambda request: httpx.Response(200, json=[]),
        lambda request: httpx.Response(200, json={"code": "0", "data": {}}),
        lambda request: httpx.Response(200, json=_envelope([])),
        lambda request: httpx.Response(200, json=_envelope([{"invalid": True}])),
    ],
)
async def test_rest_rejects_invalid_responses(
    okx_settings: OkxSettings,
    handler: Handler,
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=str(okx_settings.rest_base_url),
    ) as http_client:
        client = RestClient(
            okx_settings,
            http_client=http_client,
            rate_limiters=_limiters(),
        )
        with pytest.raises(OkxInvalidDataError):
            await client.get_mark_price("BTC-USDT-SWAP")


@pytest.mark.asyncio
async def test_rest_retries_transient_server_failure(
    okx_settings: OkxSettings,
    instrument_payload: dict[str, str],
) -> None:
    calls = 0
    retry = okx_settings.retry.model_copy(update={"max_attempts": 2, "initial_delay_seconds": 0.0})
    retrying_settings = okx_settings.model_copy(update={"retry": retry})

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503)
        return httpx.Response(200, json=_envelope([instrument_payload]))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=str(okx_settings.rest_base_url),
    ) as http_client:
        client = RestClient(
            retrying_settings,
            http_client=http_client,
            rate_limiters=_limiters(),
        )
        assert (await client.get_contract("BTC-USDT-SWAP")).state == "live"
    assert calls == 2


@pytest.mark.asyncio
async def test_rest_validates_request_parameters_without_network(
    okx_settings: OkxSettings,
) -> None:
    def unexpected(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"Appel réseau inattendu : {request.url}")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(unexpected),
        base_url=str(okx_settings.rest_base_url),
    ) as http_client:
        client = RestClient(
            okx_settings,
            http_client=http_client,
            rate_limiters=_limiters(),
        )
        with pytest.raises(OkxRequestError):
            await client.get_contract("btc-usdt-swap")
        with pytest.raises(OkxRequestError):
            await client.get_candles("BTC-USDT-SWAP", limit=301)
        with pytest.raises(OkxRequestError):
            await client.get_order_book("BTC-USDT-SWAP", depth=0)
