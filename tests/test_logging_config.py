"""Tests de la journalisation applicative."""

import logging
import re
from pathlib import Path

from okx_ai_pro.logging_config import configure_logging, get_logger
from okx_ai_pro.settings import LoggingSettings


def _logging_settings(
    directory: Path,
    *,
    console_enabled: bool = False,
    file_enabled: bool = True,
) -> LoggingSettings:
    return LoggingSettings(
        level="DEBUG",
        console_enabled=console_enabled,
        file_enabled=file_enabled,
        directory=directory,
        filename="application.log",
        max_bytes=1024,
        backup_count=2,
    )


def test_writes_utf8_log_with_utc_timestamp(tmp_path: Path) -> None:
    settings = _logging_settings(tmp_path / "nested" / "logs")
    logger = configure_logging(settings)

    get_logger("analysis").info("Analyse prête : marché haussier")
    for handler in logger.handlers:
        handler.flush()

    content = settings.file_path.read_text(encoding="utf-8")
    assert "okx_ai_pro.analysis | Analyse prête : marché haussier" in content
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", content)


def test_reconfiguration_does_not_duplicate_handlers(tmp_path: Path) -> None:
    settings = _logging_settings(tmp_path)

    first = configure_logging(settings)
    second = configure_logging(settings)

    assert first is second
    assert len(second.handlers) == 1


def test_uses_null_handler_when_all_outputs_are_disabled(tmp_path: Path) -> None:
    logger = configure_logging(
        _logging_settings(tmp_path, console_enabled=False, file_enabled=False)
    )

    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0], logging.NullHandler)
    assert not tmp_path.exists()


def test_get_logger_normalizes_application_prefix() -> None:
    assert get_logger().name == "okx_ai_pro"
    assert get_logger("scanner").name == "okx_ai_pro.scanner"
    assert get_logger("okx_ai_pro.scanner").name == "okx_ai_pro.scanner"
