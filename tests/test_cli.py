"""Tests du point d'entrée de la phase 1."""

from pathlib import Path

import pytest

from okx_ai_pro import __version__
from okx_ai_pro.cli import main


def test_cli_validates_configuration_and_prepares_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)

    exit_code = main([])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Configuration OKX AI PRO valide." in captured.out
    assert (tmp_path / "data").is_dir()
    assert (tmp_path / "logs").is_dir()
    assert __version__ == "0.1.0"


def test_cli_reports_invalid_configuration(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["--config", str(tmp_path / "missing.toml")])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Erreur de configuration" in captured.err
