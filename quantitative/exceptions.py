"""Typed failures surfaced by the quantitative evaluation boundary."""


class QuantitativeEvaluationError(Exception):
    """Base exception for expected quantitative evaluation failures."""


class InsufficientDataError(QuantitativeEvaluationError):
    """Raised when the supplied market history cannot support an evaluation."""


class QuantitativeEngineUnavailableError(QuantitativeEvaluationError):
    """Raised when the quantitative engine or a required dependency is unavailable."""


class QuantitativeError(Exception):
    """Eccezione base per gli errori del dominio quantitativo."""


class InvalidMarketDataError(QuantitativeError):
    """Sollevata quando i dati di mercato contengono anomalie (es. look-ahead, dati STALE)."""


class InsufficientHistoryError(QuantitativeError):
    """Sollevata quando le barre disponibili non soddisfano i requisiti di warm-up."""
