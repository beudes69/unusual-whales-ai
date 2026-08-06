"""Fixtures partagées des tests du moteur OKX."""

from pathlib import Path

import pytest

from okx_ai_pro.settings import OkxSettings, Settings, load_settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return load_settings(environ={}, base_directory=tmp_path)


@pytest.fixture
def okx_settings(settings: Settings) -> OkxSettings:
    retry = settings.okx.retry.model_copy(
        update={
            "max_attempts": 1,
            "initial_delay_seconds": 0.0,
            "jitter_seconds": 0.0,
        }
    )
    return settings.okx.model_copy(update={"retry": retry})


@pytest.fixture
def instrument_payload() -> dict[str, str]:
    return {
        "instId": "BTC-USDT-SWAP",
        "instType": "SWAP",
        "instFamily": "BTC-USDT",
        "uly": "BTC-USDT",
        "settleCcy": "USDT",
        "baseCcy": "",
        "quoteCcy": "",
        "ctType": "linear",
        "ctVal": "0.01",
        "ctMult": "1",
        "ctValCcy": "BTC",
        "tickSz": "0.1",
        "lotSz": "0.01",
        "minSz": "0.01",
        "lever": "100",
        "listTime": "1597026383085",
        "expTime": "",
        "state": "live",
    }
