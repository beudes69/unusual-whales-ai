"""Point d'entrée minimal du socle applicatif."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from okx_ai_pro.exceptions import ConfigurationError
from okx_ai_pro.logging_config import configure_logging
from okx_ai_pro.settings import load_settings


def build_parser() -> argparse.ArgumentParser:
    """Construit le parseur de la ligne de commande."""
    parser = argparse.ArgumentParser(
        prog="okx-ai-pro",
        description="Valide et initialise le socle d'OKX AI PRO.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Fichier TOML surchargeant la configuration par défaut.",
    )
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    """Valide la configuration et prépare les répertoires d'exécution."""
    options = build_parser().parse_args(arguments)
    try:
        settings = load_settings(options.config)
        settings.prepare_runtime_directories()
        logger = configure_logging(settings.logging)
    except ConfigurationError as exc:
        sys.stderr.write(f"Erreur de configuration : {exc}\n")
        return 2
    except OSError as exc:
        sys.stderr.write(f"Impossible de préparer l'environnement : {exc}\n")
        return 1

    logger.info(
        "Socle OKX AI PRO initialisé (environnement=%s, fuseau=%s)",
        settings.app.environment,
        settings.app.timezone,
    )
    sys.stdout.write("Configuration OKX AI PRO valide.\n")
    return 0
