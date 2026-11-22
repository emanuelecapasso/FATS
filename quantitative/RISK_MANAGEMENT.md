# Deterministic risk policy

`DeterministicRiskEvaluator` is a pure calculation component shared by
`QuantitativeEngine` and `BacktestRunner`. It never contacts a broker, submits an
order, or calls an AI agent. A `RiskDecision` is an authorization and target
quantity for downstream simulation/API consumers; it is not an order.

## Explicit inputs and units

Every risk configuration value is supplied in `RiskConfiguration`; there are
no operating defaults:

| Field | Unit and meaning |
| --- | --- |
| `max_risk_per_position_pct` | Fraction of current equity; `0.01` means 1%. |
| `max_aggregate_exposure_pct` | Maximum gross notional / current equity; fractions may exceed 1 for leverage. |
| `max_daily_loss_pct` | Fractional loss from `daily_start_equity`. |
| `max_drawdown_pct` | Fractional loss from `peak_equity`. |
| `stop_distance_pct` | Assumed stop distance as a fraction of the latest M5 close. |
| `max_price_age_seconds` | Maximum age of the latest explicitly closed M5 price at `as_of`; equality is accepted. |
| `quantity_step` | Instrument quantity increment; sizes round down to this increment. |

`RiskAccountState` expresses equity, daily starting equity, peak equity, gross
exposure, and the current position in account-currency money and instrument
units. Portfolio evaluations also provide `instrument_id` and a timestamped
`portfolio_positions` map so every open holding and its account-currency mark
are explicit. `reserved_exposure_delta` is the deterministic allocation
reservation for earlier instrument IDs evaluated at the same event; it may be
negative when an earlier target closes a position. `price_to_account_currency_rate` explicitly converts one unit of the
market price's quote currency into account currency; it is used for both risk
per unit and notional exposure (set to `1` when the currencies match). A
directional position must have positive quantity; flat must have zero.
For a new/reversed position, the current instrument's notional is removed from
projected gross exposure (current exposure plus reservation delta) before
calculating remaining aggregate capacity.

## Sizing and boundaries

For a directional signal, the evaluator computes:

```
risk_budget = equity * max_risk_per_position_pct
unit_notional = M5_close * price_to_account_currency_rate
unit_risk = unit_notional * stop_distance_pct
quantity_by_risk = risk_budget / unit_risk
quantity_by_exposure = remaining_exposure / unit_notional
recommended_quantity = floor(min(quantity_by_risk, quantity_by_exposure) / quantity_step) * quantity_step
```

The smaller cap governs sizing. Equality with the position-risk and aggregate
exposure budgets is allowed. A result that rounds to zero is rejected. A
position already being held is approved only if its risk and aggregate exposure
remain within their configured limits. Daily loss or drawdown **at or above**
the configured threshold blocks new directional exposure. A flat signal is
approved with quantity zero after input validation so that exits remain
possible even when a loss threshold has been reached.

## Fail-closed rules

The evaluator rejects missing/invalid risk configuration or account state,
position-state mismatches, missing/stale/unclosed/non-positive M5 prices, and
account exposure that cannot cover the current position. Rejections contain a
stable reason code and always recommend quantity zero. No signal receives an
approval when it would exceed a configured cap. The daily-loss and drawdown
ratios use current equity; backtests update equity from realized and marked
open-position PnL, with entry costs included.

The backtester requires an explicit risk configuration and initial account
state and passes evolving account state to the same evaluator on every replay
decision. The initial simulator position must be flat and initial equity must
match `initial_capital`.
