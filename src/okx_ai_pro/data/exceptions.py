"""Erreurs contrôlées du moteur de données."""

from okx_ai_pro.exceptions import OkxAiProError


class DataEngineError(OkxAiProError):
    """Base des erreurs du moteur de données."""


class DataNormalizationError(DataEngineError):
    """Une charge utile réseau ne peut pas être normalisée."""


class DataSequenceError(DataNormalizationError):
    """Une séquence incrémentale contient une rupture."""


class DataStorageError(DataEngineError):
    """Une opération de stockage a échoué."""


class DataWriterClosedError(DataStorageError):
    """Une écriture est demandée après l'arrêt du writer."""
