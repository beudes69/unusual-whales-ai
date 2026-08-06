"""Chargement et validation centralisés avec pydantic-settings."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime
from enum import StrEnum
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Final, Literal

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    WebsocketUrl,
    field_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from okx_ai_pro.exceptions import ConfigurationError

PositiveFloat = Annotated[float, Field(gt=0)]
PositiveInt = Annotated[int, Field(gt=0)]
NonNegativeFloat = Annotated[float, Field(ge=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]

_ENV_PREFIX: Final = "OKX_AI_PRO_"
_LOG_LEVELS: Final = frozenset({"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"})
_LEGACY_ENV_PATHS: Final[dict[str, tuple[str, ...]]] = {
    "ENVIRONMENT": ("app", "environment"),
    "TIMEZONE": ("app", "timezone"),
    "DATA_DIRECTORY": ("app", "data_directory"),
    "LOG_LEVEL": ("logging", "level"),
    "LOG_CONSOLE_ENABLED": ("logging", "console_enabled"),
    "LOG_FILE_ENABLED": ("logging", "file_enabled"),
    "LOG_DIRECTORY": ("logging", "directory"),
}


class Environment(StrEnum):
    """Environnements d'exécution reconnus."""

    DEVELOPMENT = "development"
    TESTING = "testing"
    PRODUCTION = "production"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AppSettings(_FrozenModel):
    """Paramètres généraux de l'application."""

    environment: Environment
    timezone: str = Field(min_length=1)
    data_directory: Path


class LoggingSettings(_FrozenModel):
    """Paramètres du système de journalisation."""

    level: str
    console_enabled: bool
    file_enabled: bool
    directory: Path
    filename: str = Field(min_length=1)
    max_bytes: PositiveInt
    backup_count: NonNegativeInt

    @field_validator("level")
    @classmethod
    def validate_level(cls, value: str) -> str:
        """Normalise et contrôle le niveau de journalisation."""
        normalized = value.upper()
        if normalized not in _LOG_LEVELS:
            accepted = ", ".join(sorted(_LOG_LEVELS))
            raise ValueError(f"niveau invalide, valeurs acceptées : {accepted}")
        return normalized

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        """Empêche qu'un nom de journal sorte de son répertoire."""
        if Path(value).name != value:
            raise ValueError("doit être un simple nom de fichier")
        return value

    @property
    def file_path(self) -> Path:
        """Retourne le chemin complet du journal applicatif."""
        return self.directory / self.filename


class RateLimitSettings(_FrozenModel):
    """Quota d'un groupe d'appels sur une fenêtre glissante."""

    max_requests: PositiveInt
    period_seconds: PositiveFloat


class RetrySettings(_FrozenModel):
    """Politique de nouvelles tentatives REST."""

    max_attempts: PositiveInt
    initial_delay_seconds: NonNegativeFloat
    maximum_delay_seconds: PositiveFloat
    multiplier: Annotated[float, Field(ge=1)]
    jitter_seconds: NonNegativeFloat


class ReconnectSettings(_FrozenModel):
    """Politique de reconnexion WebSocket."""

    initial_delay_seconds: NonNegativeFloat
    maximum_delay_seconds: PositiveFloat
    multiplier: Annotated[float, Field(ge=1)]


class OkxSettings(_FrozenModel):
    """Paramètres réseau publics d'OKX."""

    rest_base_url: AnyHttpUrl
    websocket_public_url: WebsocketUrl
    websocket_business_url: WebsocketUrl
    user_agent: str = Field(min_length=1)
    request_timeout_seconds: PositiveFloat
    websocket_open_timeout_seconds: PositiveFloat
    websocket_close_timeout_seconds: PositiveFloat
    websocket_receive_timeout_seconds: PositiveFloat
    websocket_heartbeat_timeout_seconds: PositiveFloat
    websocket_ack_timeout_seconds: PositiveFloat
    websocket_max_command_bytes: PositiveInt
    subscription_limit: RateLimitSettings
    retry: RetrySettings
    reconnect: ReconnectSettings
    rate_limits: dict[str, RateLimitSettings]


class CacheSettings(_FrozenModel):
    """Paramètres du cache mémoire TTL/LRU."""

    ttl_seconds: PositiveFloat
    maximum_entries: PositiveInt
    purge_interval_seconds: PositiveFloat


class SQLiteSettings(_FrozenModel):
    """Paramètres du dépôt SQLite local."""

    path: Path
    busy_timeout_milliseconds: PositiveInt
    journal_mode: Literal["WAL", "DELETE", "TRUNCATE"]
    synchronous: Literal["OFF", "NORMAL", "FULL", "EXTRA"]


class CollectionSettings(_FrozenModel):
    """Paramètres de collecte et d'écriture."""

    order_book_depth: Annotated[int, Field(ge=1, le=400)]
    candle_history_limit: Annotated[int, Field(ge=1, le=300)]
    rest_concurrency: PositiveInt
    writer_queue_size: PositiveInt
    writer_batch_size: PositiveInt
    writer_flush_interval_seconds: PositiveFloat
    instrument_refresh_seconds: PositiveFloat
    candle_timeframes: tuple[str, ...] = Field(min_length=1)
    ticker_channel: str = Field(min_length=1)
    mark_price_channel: str = Field(min_length=1)
    index_ticker_channel: str = Field(min_length=1)
    open_interest_channel: str = Field(min_length=1)
    funding_rate_channel: str = Field(min_length=1)
    order_book_channel: str = Field(min_length=1)
    candle_channel_prefix: str = Field(min_length=1)

    @field_validator("candle_timeframes")
    @classmethod
    def validate_unique_timeframes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("Les timeframes de bougies doivent être uniques.")
        return value


class DataQualitySettings(_FrozenModel):
    """Bornes temporelles utilisées pour contrôler les observations."""

    minimum_timestamp: datetime
    maximum_future_seconds: NonNegativeFloat

    @field_validator("minimum_timestamp")
    @classmethod
    def normalize_minimum_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("data.quality.minimum_timestamp doit contenir un fuseau.")
        return value.astimezone(UTC)


class DataSettings(_FrozenModel):
    """Configuration du moteur de données."""

    cache: CacheSettings
    sqlite: SQLiteSettings
    collection: CollectionSettings
    quality: DataQualitySettings


class Settings(BaseSettings):
    """Configuration pydantic-settings validée et immuable."""

    model_config = SettingsConfigDict(extra="forbid", frozen=True)

    app: AppSettings
    logging: LoggingSettings
    okx: OkxSettings
    data: DataSettings

    def prepare_runtime_directories(self) -> None:
        """Crée uniquement les répertoires nécessaires à l'exécution."""
        self.app.data_directory.mkdir(parents=True, exist_ok=True)
        self.data.sqlite.path.parent.mkdir(parents=True, exist_ok=True)
        if self.logging.file_enabled:
            self.logging.directory.mkdir(parents=True, exist_ok=True)


def load_settings(
    config_path: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    base_directory: str | Path | None = None,
) -> Settings:
    """Fusionne TOML et environnement, puis valide via pydantic-settings."""
    raw_config = _load_default_config()

    if config_path is not None:
        path = Path(config_path).expanduser()
        if not path.is_file():
            raise ConfigurationError(f"Fichier de configuration introuvable : {path}")
        _deep_merge(raw_config, _load_toml(path))

    _apply_environment(raw_config, os.environ if environ is None else environ)
    base_path = Path.cwd() if base_directory is None else Path(base_directory).expanduser()
    _resolve_runtime_paths(raw_config, base_path.resolve())
    try:
        return Settings.model_validate(raw_config)
    except ValidationError as exc:
        raise ConfigurationError(f"Configuration invalide : {exc}") from exc


def _load_default_config() -> dict[str, object]:
    resource = files("okx_ai_pro").joinpath("default.toml")
    try:
        return tomllib.loads(resource.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError("Impossible de charger la configuration par défaut.") from exc


def _load_toml(path: Path) -> dict[str, object]:
    try:
        with path.open("rb") as stream:
            return tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"Configuration TOML invalide : {path}") from exc


def _deep_merge(target: dict[str, object], override: Mapping[str, object]) -> None:
    for key, value in override.items():
        current = target.get(key)
        if isinstance(current, dict) and isinstance(value, Mapping):
            _deep_merge(current, value)
        else:
            target[key] = deepcopy(value)


def _apply_environment(config: dict[str, object], environ: Mapping[str, str]) -> None:
    for variable, value in environ.items():
        if not variable.startswith(_ENV_PREFIX):
            continue
        suffix = variable.removeprefix(_ENV_PREFIX)
        path = _LEGACY_ENV_PATHS.get(suffix)
        if path is None:
            path = tuple(part.lower() for part in suffix.split("__"))
        if len(path) < 2 or any(not part for part in path):
            raise ConfigurationError(f"Variable d'environnement invalide : {variable}")
        _set_nested(config, path, value)


def _set_nested(config: dict[str, object], path: tuple[str, ...], value: str) -> None:
    current = config
    for key in path[:-1]:
        child = current.get(key)
        if not isinstance(child, dict):
            child = {}
            current[key] = child
        current = child
    current[path[-1]] = value


def _resolve_runtime_paths(config: dict[str, object], base_directory: Path) -> None:
    app = config.get("app")
    logging_config = config.get("logging")
    data_config = config.get("data")
    if isinstance(app, dict) and isinstance(app.get("data_directory"), str):
        app["data_directory"] = _resolve_path(app["data_directory"], base_directory)
    if isinstance(logging_config, dict) and isinstance(logging_config.get("directory"), str):
        logging_config["directory"] = _resolve_path(logging_config["directory"], base_directory)
    if isinstance(data_config, dict):
        sqlite_config = data_config.get("sqlite")
        if isinstance(sqlite_config, dict) and isinstance(sqlite_config.get("path"), str):
            sqlite_config["path"] = _resolve_path(sqlite_config["path"], base_directory)


def _resolve_path(value: str, base_directory: Path) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base_directory / path).resolve()
