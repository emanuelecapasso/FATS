"""Deterministic, fail-closed risk evaluation. This module never places orders."""

from datetime import timedelta
from decimal import ROUND_FLOOR, Decimal

from core.schemas import (
    DataQuality,
    MarketDataSnapshot,
    RiskAccountState,
    RiskConfiguration,
    RiskDecision,
    Signal,
    SignalDirection,
    StrategyConfiguration,
    Timeframe,
)


class DeterministicRiskEvaluator:
    """Sizes a target position using explicit limits and account-currency prices."""

    def evaluate(
        self,
        signal: Signal,
        snapshot: MarketDataSnapshot,
        configuration: StrategyConfiguration,
        risk_configuration: RiskConfiguration | None,
        account_state: RiskAccountState | None,
    ) -> RiskDecision:
        def deny(reason: str) -> RiskDecision:
            return RiskDecision(
                approved=False, reasons=[reason], recommended_quantity=0.0
            )

        if not isinstance(risk_configuration, RiskConfiguration):
            return deny("risk:configuration_missing")
        if not isinstance(account_state, RiskAccountState):
            return deny("risk:account_state_missing")

        series = snapshot.series.get(Timeframe.M5)
        if series is None or series.quality == DataQuality.STALE or not series.bars:
            return deny("risk:price_unavailable")
        latest = series.bars[-1]
        if latest.is_closed is not True or latest.close <= 0:
            return deny("risk:price_invalid_or_unclosed")
        age = snapshot.as_of - latest.timestamp
        if (
            age < timedelta(0)
            or age.total_seconds() > risk_configuration.max_price_age_seconds
        ):
            return deny("risk:price_stale")

        account = account_state
        if any(
            position.valued_at > snapshot.as_of
            for position in account.portfolio_positions.values()
        ):
            return deny("risk:portfolio_position_from_future")
        if configuration.current_position != account.position_direction:
            return deny("risk:position_state_mismatch")
        price = latest.close
        unit_notional = price * account.price_to_account_currency_rate
        current_notional = account.position_quantity * unit_notional
        if (
            account.position_direction != SignalDirection.FLAT
            and account.gross_exposure < current_notional
        ):
            return deny("risk:account_exposure_invalid")

        daily_loss_pct = max(
            0.0,
            (account.daily_start_equity - account.equity) / account.daily_start_equity,
        )
        drawdown_pct = max(
            0.0, (account.peak_equity - account.equity) / account.peak_equity
        )

        # Exits are always allowed after market/account checks; risk limits block new exposure.
        if signal.direction == SignalDirection.FLAT:
            return RiskDecision(
                approved=True,
                reasons=["risk:exit_or_flat_approved"],
                recommended_quantity=0.0,
            )
        if daily_loss_pct >= risk_configuration.max_daily_loss_pct:
            return deny("risk:daily_loss_limit")
        if drawdown_pct >= risk_configuration.max_drawdown_pct:
            return deny("risk:drawdown_limit")

        risk_budget = account.equity * risk_configuration.max_risk_per_position_pct
        per_unit_risk = unit_notional * risk_configuration.stop_distance_pct
        max_by_risk = risk_budget / per_unit_risk
        target_exposure = account.gross_exposure + account.reserved_exposure_delta
        exposure_without_this_position = max(0.0, target_exposure - current_notional)
        exposure_budget = account.equity * risk_configuration.max_aggregate_exposure_pct
        remaining_exposure = max(0.0, exposure_budget - exposure_without_this_position)
        max_by_exposure = remaining_exposure / unit_notional

        if signal.direction == account.position_direction:
            quantity = account.position_quantity
            if quantity * per_unit_risk > risk_budget:
                return deny("risk:position_risk_limit")
            if target_exposure > exposure_budget:
                return deny("risk:aggregate_exposure_limit")
            return RiskDecision(
                approved=True,
                reasons=["risk:existing_position_within_limits"],
                recommended_quantity=quantity,
            )

        raw_quantity = min(max_by_risk, max_by_exposure)
        step = Decimal(str(risk_configuration.quantity_step))
        sized = (Decimal(str(raw_quantity)) / step).to_integral_value(
            rounding=ROUND_FLOOR
        ) * step
        quantity = float(sized)
        if quantity <= 0:
            reason = (
                "risk:aggregate_exposure_limit"
                if max_by_exposure <= max_by_risk
                else "risk:position_risk_limit"
            )
            return deny(reason)

        reasons = ["risk:approved"]
        if max_by_risk <= max_by_exposure:
            reasons.append("risk:position_risk_sized")
        else:
            reasons.append("risk:aggregate_exposure_sized")
        return RiskDecision(
            approved=True, reasons=reasons, recommended_quantity=quantity
        )
