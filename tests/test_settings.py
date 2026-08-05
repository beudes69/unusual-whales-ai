"""Tests de la configuration centralisée."""

from pathlib import Path

import pytest

from okx_ai_pro.exceptions import ConfigurationError
from okx_ai_pro.settings import Environment, load_settings


def _write_config(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_loads_packaged_defaults_without_creating_directories(tmp_path: Path) -> None:
    settings = load_settings(environ={}, base_directory=tmp_path)

    assert settings.app.environment is Environment.DEVELOPMENT
    assert settings.app.timezone == "UTC"
    assert settings.app.data_directory == tmp_path / "data"
    assert settings.logging.level == "INFO"
    assert settings.logging.file_path == tmp_path / "logs" / "okx-ai-pro.log"
    assert str(settings.okx.rest_base_url) == "https://openapi.okx.com/"
    assert settings.okx.rate_limits["candles"].max_requests == 40
    assert not settings.app.data_directory.exists()
    assert not settings.logging.directory.exists()


def test_file_and_environment_overrides_follow_expected_precedence(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path / "custom.toml",
        """
[app]
environment = "testing"
data_directory = "market-data"

[logging]
level = "WARNING"
file_enabled = false
""",
    )

    settings = load_settings(
        config_path,
        environ={
            "OKX_AI_PRO_ENVIRONMENT": "production",
            "OKX_AI_PRO_LOG_LEVEL": "debug",
            "OKX_AI_PRO_LOG_FILE_ENABLED": "yes",
            "OKX_AI_PRO_LOG_CONSOLE_ENABLED": "0",
            "OKX_AI_PRO_LOG_DIRECTORY": "~/okx-test-logs",
        },
        base_directory=tmp_path,
    )

    assert settings.app.environment is Environment.PRODUCTION
    assert settings.app.data_directory == tmp_path / "market-data"
    assert settings.logging.level == "DEBUG"
    assert settings.logging.file_enabled is True
    assert settings.logging.console_enabled is False
    assert settings.logging.directory == Path("~/okx-test-logs").expanduser().resolve()


def test_prepare_runtime_directories_honours_disabled_file_logging(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path / "custom.toml",
        """
[logging]
file_enabled = false
""",
    )
    settings = load_settings(config_path, environ={}, base_directory=tmp_path)

    settings.prepare_runtime_directories()

    assert settings.app.data_directory.is_dir()
    assert not settings.logging.directory.exists()


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("unknown = true", "Extra inputs are not permitted"),
        ("[app]\nenvironment = 'invalid'", "app.environment"),
        ("[logging]\nlevel = 'TRACE'", "logging.level"),
        ("[logging]\nmax_bytes = 0", "max_bytes"),
        ("[logging]\nconsole_enabled = 'sometimes'", "console_enabled"),
        ("[logging]\nfilename = '../outside.log'", "simple nom de fichier"),
    ],
)
def test_rejects_invalid_configuration(tmp_path: Path, content: str, message: str) -> None:
    config_path = _write_config(tmp_path / "invalid.toml", content)

    with pytest.raises(ConfigurationError, match=message):
        load_settings(config_path, environ={}, base_directory=tmp_path)


def test_rejects_unknown_nested_key(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path / "invalid.toml", "[app]\ntypo = true")

    with pytest.raises(ConfigurationError, match=r"app\.typo"):
        load_settings(config_path, environ={}, base_directory=tmp_path)


def test_rejects_invalid_boolean_environment_value(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match=r"logging\.file_enabled"):
        load_settings(
            environ={"OKX_AI_PRO_LOG_FILE_ENABLED": "sometimes"},
            base_directory=tmp_path,
        )


def test_supports_nested_pydantic_environment_variables(tmp_path: Path) -> None:
    settings = load_settings(
        environ={
            "OKX_AI_PRO_OKX__REQUEST_TIMEOUT_SECONDS": "3.5",
            "OKX_AI_PRO_OKX__RETRY__MAX_ATTEMPTS": "7",
        },
        base_directory=tmp_path,
    )

    assert settings.okx.request_timeout_seconds == 3.5
    assert settings.okx.retry.max_attempts == 7


def test_rejects_missing_and_malformed_files(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="introuvable"):
        load_settings(tmp_path / "missing.toml", environ={})

    invalid_path = _write_config(tmp_path / "malformed.toml", "[app")
    with pytest.raises(ConfigurationError, match="TOML invalide"):
        load_settings(invalid_path, environ={})
