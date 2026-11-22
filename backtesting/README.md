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
and exit pays commission on absolute quantity times market price. Slippage is
reported as an explicit cost on both sides; trade market prices remain the raw
bar open (or final close for terminal liquidation). Net PnL is gross PnL minus
commission and slippage. Equity is initial capital plus closed-trade net PnL;
drawdown is computed on that closed-trade equity curve.

`BacktestResult` carries a stable SHA-256 run ID and input fingerprint, pair,
interval, strategy ID/version, engine version, configuration, costs, trades,
metrics, skipped decisions, temporal gaps, and any unfilled final target. IDs
are derived from canonical JSON of the input and code versions, not wall-clock
time or random values.
