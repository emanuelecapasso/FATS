# Deterministic backtesting

`BacktestRunner` replays canonical M5/M15/H1 data through the same
`QuantitativeEngine` used by the backend. It is offline-only and never calls a
broker, data provider, or order-execution API.

## Replay and fill rules

- M5 bars are the decision events and must be supplied in strictly increasing
  timestamp order. Duplicate/out-of-order bars and stale series are rejected.
- At decision time `T`, each timeframe snapshot includes only bars with
  `timestamp <= T` and `is_closed == true`; future, incomplete, and unknown
  bars are never supplied to the engine.
- A target generated at an M5 close is filled at the next observed closed M5
  bar's open. The first closed bar after a temporal gap is used for any already
  pending fill, but its decision is skipped; no missing candles are synthesized.
- Insufficient-history decisions are skipped and counted. An open position is
  liquidated at the last closed in-range M5 close. A target from the final
  decision remains unfilled and is reported as `unfilled_final_signal`.
- A hold preserves its original quantity; the simulator does not pyramid or
  resize positions. A reversal closes the current position and opens the new
  target at the same next-bar open.

## Costs, PnL, and provenance

`BacktestCostModel` takes commission and slippage in basis points. Each entry
and exit cost is charged on absolute quantity times market price, converted to
the account currency with `price_to_account_currency_rate`. Slippage is
reported as an explicit cost on both sides; trade market prices remain the raw
bar open (or final close for terminal liquidation). Net PnL is gross PnL minus
commission and slippage. The conversion rate is an explicit account-state input
and is held constant for one run; a future variable FX conversion model must
provide timestamped rates as market data.

## Equity curve and metrics

`BacktestResult.equity_curve` contains one account-currency mark for every
observed closed M5 event in the requested interval, after any pending fill at
that bar's open and marked at its close. Realized PnL is the sum of closed
trades' net PnL. Unrealized PnL includes the open position's market PnL at the
mark price, converted to account currency and reduced by entry commission and
slippage already incurred. A final forced liquidation replaces the final
mark with the realized result after exit costs. Missing intervals do not
synthesize samples.

`max_drawdown_abs` is the largest account-currency fall from a prior equity
peak, including the initial capital as the starting peak. `max_drawdown_pct`
is that fall divided by its prior peak, represented as a fraction (`0.05` is
5%). `profit_factor` is winning net trade PnL divided by absolute losing net
trade PnL; it is `null` when there are no losing trades. `expectancy_per_trade`
is average net PnL per closed trade and is `null` when no trades closed.
`time_in_market_pct` is the fraction of observed M5 equity marks with a
non-flat position; it is `null` when the interval has no observed closed M5
bars. `average_gross_exposure` and `max_gross_exposure` are account-currency
notional values across observed marks; they are zero when the interval has no
observed marks. These conventions avoid undefined infinities. No annualized Sharpe or
Sortino values are emitted until their annualization and risk-free assumptions
are explicit inputs.

`BacktestResult` carries a stable SHA-256 run ID and input fingerprint, pair,
interval, strategy ID/version, quantitative engine version, backtest version,
configuration, costs, trades, equity curve, metrics, skipped decisions,
temporal gaps, and any unfilled final target. IDs are derived from canonical
JSON of the input and code versions, not wall-clock time or random values.
