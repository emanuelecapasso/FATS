"""Typed failures surfaced by the quantitative evaluation boundary."""


class QuantitativeEvaluationError(Exception):
    """Base exception for expected quantitative evaluation failures."""


class InsufficientDataError(QuantitativeEvaluationError):
    """Raised when the supplied market history cannot support an evaluation."""


class QuantitativeEngineUnavailableError(QuantitativeEvaluationError):
    """Raised when the quantitative engine or a required dependency is unavailable."""
