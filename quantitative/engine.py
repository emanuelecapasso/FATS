from core.schemas import (
    DataQuality,
    EvaluationRequest,
    EvaluationResponse,
    RiskDecision,
    Signal,
    Timeframe,
)
from quantitative.exceptions import InsufficientHistoryError, InvalidMarketDataError
from quantitative.ports import RiskEvaluator, Strategy

ENGINE_VERSION = "1.0.0"
REQUIRED_TIMEFRAMES = (Timeframe.M5, Timeframe.M15, Timeframe.H1)


class QuantitativeEngine:
    def __init__(self, strategy: Strategy, risk_evaluator: RiskEvaluator):
        self.strategy = strategy
        self.risk_evaluator = risk_evaluator

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResponse:
        """Implementazione del protocollo QuantitativeEvaluator."""
        snapshot = request.market_data
        configuration = request.configuration

        missing_timeframes = [
            timeframe
            for timeframe in REQUIRED_TIMEFRAMES
            if timeframe not in snapshot.series
        ]
        if missing_timeframes:
            missing = ", ".join(tf.value for tf in missing_timeframes)
            raise InvalidMarketDataError(f"Required timeframes are missing: {missing}.")

        # Itera in ordine canonico e passa alla strategia uno snapshot canonico.
        ordered_series = {
            timeframe: snapshot.series[timeframe] for timeframe in REQUIRED_TIMEFRAMES
        }
        for tf in REQUIRED_TIMEFRAMES:
            series_data = ordered_series[tf]
            quality = series_data.quality
            bars = series_data.bars

            if quality == DataQuality.STALE:
                raise InvalidMarketDataError(
                    f"Market data quality 'STALE' is not allowed for evaluation in timeframe {tf.value}."
                )

            if not bars:
                continue

            for i, bar in enumerate(bars):
                # Regola: Timestamp non futuri rispetto allo snapshot
                if bar.timestamp > snapshot.as_of:
                    raise InvalidMarketDataError(
                        f"Bar timestamp {bar.timestamp} is in the future relative to as_of {snapshot.as_of}."
                    )

                # Regola: Verifica ordine strettamente crescente (rifiuta duplicati e disordinati)
                if i > 0 and bar.timestamp <= bars[i - 1].timestamp:
                    raise InvalidMarketDataError(
                        f"Bars in timeframe {tf.value} are duplicated or out of order at {bar.timestamp}."
                    )

                # Regola: Se la serie è PARTIAL, tolleriamo solo barre esplicitamente marcate come chiuse (True)
                if quality == DataQuality.PARTIAL and bar.is_closed is not True:
                    raise InvalidMarketDataError(
                        f"PARTIAL series in timeframe {tf.value} contains bars with is_closed={bar.is_closed}. "
                        "Only explicitly closed bars (is_closed=True) are allowed."
                    )

        # Verifica il warm-up dichiarato dalla strategia prima di invocarla.
        required_history = self.strategy.required_history(configuration)
        for tf in REQUIRED_TIMEFRAMES:
            minimum = required_history.get(tf, 0)
            if minimum < 0:
                raise ValueError(
                    f"Invalid warm-up requirement for {tf.value}: {minimum}."
                )
            available = len(ordered_series[tf].bars)
            if available < minimum:
                raise InsufficientHistoryError(
                    f"Insufficient history for {tf.value}: required {minimum}, available {available}."
                )

        canonical_snapshot = snapshot.model_copy(update={"series": ordered_series})

        # 2. Generazione del Segnale (Gli errori di dominio si propagano)
        signal = self.strategy.evaluate(canonical_snapshot, configuration)

        if not isinstance(signal, Signal):
            raise TypeError("Strategy must return a canonical Signal.")

        # 3. Valutazione del Rischio
        risk_decision = self.risk_evaluator.evaluate(signal, configuration)

        if not isinstance(risk_decision, RiskDecision):
            raise TypeError("Risk Evaluator must return a canonical RiskDecision.")

        # 4. Restituzione del contratto canonico
        return EvaluationResponse(
            timestamp=canonical_snapshot.as_of,
            pair=canonical_snapshot.pair,
            strategy_id=self.strategy.strategy_id,
            strategy_version=self.strategy.version,
            engine_version=ENGINE_VERSION,
            signal=signal,
            risk_decision=risk_decision,
        )
