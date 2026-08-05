"""Exceptions métier communes au projet."""


class OkxAiProError(Exception):
    """Classe de base des erreurs contrôlées de l'application."""


class ConfigurationError(OkxAiProError):
    """Signale une configuration absente, inconnue ou invalide."""
