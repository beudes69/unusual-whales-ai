"""Configuration cohérente et multiplateforme des journaux."""

from __future__ import annotations

import logging
import sys
import time
from logging.handlers import RotatingFileHandler

from okx_ai_pro.settings import LoggingSettings

_LOGGER_NAME = "okx_ai_pro"
_FORMAT = "%(asctime)s.%(msecs)03dZ | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"


class _UtcFormatter(logging.Formatter):
    """Formateur dont tous les horodatages sont explicitement en UTC."""

    @staticmethod
    def converter(timestamp: float | None) -> time.struct_time:
        """Convertit un timestamp Unix en temps UTC structuré."""
        return time.gmtime(timestamp)


def configure_logging(settings: LoggingSettings) -> logging.Logger:
    """Configure puis retourne le logger racine de l'application.

    Les appels successifs remplacent proprement les handlers précédents afin
    d'éviter les lignes dupliquées lors d'un rechargement de configuration.
    """
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(settings.level)
    logger.propagate = False

    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()

    formatter = _UtcFormatter(_FORMAT, datefmt=_DATE_FORMAT)
    handlers: list[logging.Handler] = []

    if settings.console_enabled:
        console_handler = logging.StreamHandler(sys.stderr)
        handlers.append(console_handler)

    if settings.file_enabled:
        settings.directory.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            settings.file_path,
            maxBytes=settings.max_bytes,
            backupCount=settings.backup_count,
            encoding="utf-8",
            delay=True,
        )
        handlers.append(file_handler)

    if not handlers:
        handlers.append(logging.NullHandler())

    for handler in handlers:
        handler.setLevel(settings.level)
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


def get_logger(component: str | None = None) -> logging.Logger:
    """Retourne un logger appartenant à la hiérarchie d'OKX AI PRO."""
    if not component:
        return logging.getLogger(_LOGGER_NAME)
    normalized = component.removeprefix(f"{_LOGGER_NAME}.")
    return logging.getLogger(f"{_LOGGER_NAME}.{normalized}")
