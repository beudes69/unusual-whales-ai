"""Chargement et validation centralisés de la configuration."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from importlib.resources import files
from pathlib import Path
from typing import Final

from okx_ai_pro.exceptions import ConfigurationError

_ROOT_KEYS: Final = frozenset({"app", "logging"})
_APP_KEYS: Final = frozenset({"environment", "timezone", "data_directory"})
_LOGGING_KEYS: Final = frozenset(
    {
        "level",
        "console_enabled",
        "file_enabled",
        "directory",
        "filename",
        "max_bytes",
        "backup_count",
    }
)
_LOG_LEVELS: Final = frozenset({"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"})


class Environment(StrEnum):
    """Environnements d'exécution reconnus."""

    DEVELOPMENT = "development"
    TESTING = "testing"
    PRODUCTION = "production"


@dataclass(frozen=True, slots=True)
class AppSettings:
    """Paramètres généraux de l'application."""

    environment: Environment
    timezone: str
    data_directory: Path


@dataclass(frozen=True, slots=True)
class LoggingSettings:
    """Paramètres du système de journalisation."""

    level: str
    console_enabled: bool
    file_enabled: bool
    directory: Path
    filename: str
    max_bytes: int
    backup_count: int

    @property
    def file_path(self) -> Path:
        """Retourne le chemin complet du journal applicatif."""
        return self.directory / self.filename


@dataclass(frozen=True, slots=True)
class Settings:
    """Configuration validée et immuable d'OKX AI PRO."""

    app: AppSettings
    logging: LoggingSettings

    def prepare_runtime_directories(self) -> None:
        """Crée uniquement les répertoires nécessaires à l'exécution."""
        self.app.data_directory.mkdir(parents=True, exist_ok=True)
        if self.logging.file_enabled:
            self.logging.directory.mkdir(parents=True, exist_ok=True)


def load_settings(
    config_path: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    base_directory: str | Path | None = None,
) -> Settings:
    """Charge les valeurs par défaut, un fichier utilisateur puis l'environnement.

    Les chemins relatifs sont résolus depuis ``base_directory`` ou, par défaut,
    depuis le répertoire de travail. Le chargement n'a aucun effet de bord sur
    le système de fichiers.
    """
    raw_config = _load_default_config()

    if config_path is not None:
        path = Path(config_path).expanduser()
        if not path.is_file():
            raise ConfigurationError(f"Fichier de configuration introuvable : {path}")
        _deep_merge(raw_config, _load_toml(path))

    _apply_environment(raw_config, os.environ if environ is None else environ)
    base_path = Path.cwd() if base_directory is None else Path(base_directory).expanduser()
    return _build_settings(raw_config, base_path.resolve())


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
    app = _expect_table(config, "app")
    logging_config = _expect_table(config, "logging")

    text_overrides = {
        "OKX_AI_PRO_ENVIRONMENT": (app, "environment"),
        "OKX_AI_PRO_TIMEZONE": (app, "timezone"),
        "OKX_AI_PRO_DATA_DIRECTORY": (app, "data_directory"),
        "OKX_AI_PRO_LOG_LEVEL": (logging_config, "level"),
        "OKX_AI_PRO_LOG_DIRECTORY": (logging_config, "directory"),
    }
    for variable, (section, key) in text_overrides.items():
        if variable in environ:
            section[key] = environ[variable]

    boolean_overrides = {
        "OKX_AI_PRO_LOG_CONSOLE_ENABLED": (logging_config, "console_enabled"),
        "OKX_AI_PRO_LOG_FILE_ENABLED": (logging_config, "file_enabled"),
    }
    for variable, (section, key) in boolean_overrides.items():
        if variable in environ:
            section[key] = _parse_boolean(environ[variable], variable)


def _build_settings(config: Mapping[str, object], base_directory: Path) -> Settings:
    _reject_unknown_keys(config, _ROOT_KEYS, "racine")
    app = _expect_table(config, "app")
    logging_config = _expect_table(config, "logging")
    _reject_unknown_keys(app, _APP_KEYS, "app")
    _reject_unknown_keys(logging_config, _LOGGING_KEYS, "logging")

    environment_value = _expect_string(app, "environment").lower()
    try:
        environment = Environment(environment_value)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in Environment)
        raise ConfigurationError(
            f"app.environment doit être l'une des valeurs suivantes : {allowed}"
        ) from exc

    timezone = _expect_string(app, "timezone")
    level = _expect_string(logging_config, "level").upper()
    if level not in _LOG_LEVELS:
        raise ConfigurationError(
            f"logging.level invalide : {level}. Valeurs acceptées : {', '.join(sorted(_LOG_LEVELS))}"
        )

    filename = _expect_string(logging_config, "filename")
    if Path(filename).name != filename:
        raise ConfigurationError("logging.filename doit être un simple nom de fichier.")

    max_bytes = _expect_integer(logging_config, "max_bytes", minimum=1)
    backup_count = _expect_integer(logging_config, "backup_count", minimum=0)

    return Settings(
        app=AppSettings(
            environment=environment,
            timezone=timezone,
            data_directory=_resolve_path(
                _expect_string(app, "data_directory"), base_directory
            ),
        ),
        logging=LoggingSettings(
            level=level,
            console_enabled=_expect_boolean(logging_config, "console_enabled"),
            file_enabled=_expect_boolean(logging_config, "file_enabled"),
            directory=_resolve_path(
                _expect_string(logging_config, "directory"), base_directory
            ),
            filename=filename,
            max_bytes=max_bytes,
            backup_count=backup_count,
        ),
    )


def _expect_table(config: Mapping[str, object], key: str) -> dict[str, object]:
    value = config.get(key)
    if not isinstance(value, dict):
        raise ConfigurationError(f"La section [{key}] est absente ou invalide.")
    return value


def _expect_string(config: Mapping[str, object], key: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{key} doit être une chaîne non vide.")
    return value.strip()


def _expect_boolean(config: Mapping[str, object], key: str) -> bool:
    value = config.get(key)
    if not isinstance(value, bool):
        raise ConfigurationError(f"{key} doit être un booléen.")
    return value


def _expect_integer(config: Mapping[str, object], key: str, *, minimum: int) -> int:
    value = config.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ConfigurationError(f"{key} doit être un entier supérieur ou égal à {minimum}.")
    return value


def _parse_boolean(value: str, variable: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(
        f"{variable} doit contenir true/false, yes/no, on/off ou 1/0."
    )


def _resolve_path(value: str, base_directory: Path) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base_directory / path).resolve()


def _reject_unknown_keys(
    config: Mapping[str, object], expected: frozenset[str], section: str
) -> None:
    unknown = set(config) - expected
    if unknown:
        raise ConfigurationError(
            f"Clé(s) inconnue(s) dans la section {section} : {', '.join(sorted(unknown))}"
        )
