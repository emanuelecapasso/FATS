from typing import Protocol

from core.schemas import (
    EvaluationRequest,
    EvaluationResponse,
    MarketDataSnapshot,
    RiskAccountState,
    RiskConfiguration,
    RiskDecision,
    Signal,
    StrategyConfiguration,
    Timeframe,
)


class Strategy(Protocol):
    """Porto per l'algoritmo di trading (generazione segnali)."""

    @property
    def strategy_id(self) -> str: ...

    @property
    def version(self) -> str: ...

    def required_history(
        self, configuration: StrategyConfiguration
    ) -> dict[Timeframe, int]:
        """Numero minimo di barre chiuse richieste per ciascun timeframe."""
        ...

    def evaluate(
        self, snapshot: MarketDataSnapshot, configuration: StrategyConfiguration
    ) -> Signal:
        """Valuta il mercato tramite lo snapshot e la configurazione, restituendo un Signal canonico."""
        ...


class RiskEvaluator(Protocol):
    """Porto per il modulo di risk management e position sizing."""

    def evaluate(
        self,
        signal: Signal,
        snapshot: MarketDataSnapshot,
        configuration: StrategyConfiguration,
        risk_configuration: RiskConfiguration | None,
        account_state: RiskAccountState | None,
    ) -> RiskDecision:
        """Valuta il segnale usando dati, limiti e stato conto espliciti."""
        ...


class QuantitativeEvaluator(Protocol):
    """Porto in ingresso invocato dal layer API (Backend)."""

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResponse: ...
