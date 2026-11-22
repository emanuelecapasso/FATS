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

## Walk-forward evaluation

`WalkForwardRunner` accepts one canonical dataset, a fixed list of candidate
strategy configurations, and an explicit ordered list of folds. Each fold has
closed UTC windows for training, validation, and out-of-sample test. The
training-to-validation gap must be at least the configured `purge_minutes`; the
validation-to-test gap must be at least `embargo_minutes`. Window endpoints
are inclusive, so windows cannot overlap. Test windows across folds must also
be ordered and non-overlapping.

For every candidate, the report includes separate training and validation
backtests. Training results are diagnostic; selection uses only the explicitly
configured validation metric and direction. Candidates with no validation
result or an undefined metric are ineligible. Exact score ties are resolved by
ascending `candidate_id`, independent of input list order. Only after selection
does the runner invoke the backtester for that candidate's test window. Test
metrics never enter that fold's selection.

Each period sees only bars at or before its end, and the reused backtester
further limits every decision to closed bars available at that decision time.
Earlier bars remain available for indicator warm-up. Each period starts from
the same explicit initial capital and flat account state; positions are not
carried between periods. Commission, slippage, risk settings, and account
currency conversion use the same inputs as ordinary backtests.

Period status is one of `completed`, `no_trades`, `no_data`,
`insufficient_history`, `invalid_data`, or `not_run`. A fold with no eligible
validation score has no selected candidate and its test is `not_run`. Temporal
gaps remain visible through the reused backtest metrics; missing candles are
not synthesized.

The aggregate reports the sum of out-of-sample net PnL, arithmetic mean of
per-fold percentage returns (equal fold weighting), worst per-fold percentage
drawdown, and pooled profit factor and expectancy. Profit factor is `null`
when the evaluated test trades contain no losses; expectancy is `null` when
there are no test trades. Dataset, fold, strategy, engine, backtest, and
walk-forward versions are fingerprinted or included in the report.

## Multi-instrument portfolio replay

`PortfolioBacktestRunner` replays one strategy across canonical instrument
datasets with per-instrument configuration and costs, one shared flat initial
account, one risk configuration, and one account currency. Each instrument's
quote currency is explicit. `AccountCurrencyRate` entries convert one unit of
that quote currency into the account currency; even same-currency conversion
must be supplied explicitly. Rates are UTC ordered input data, never fetched
from a provider during replay.

The event calendar is the sorted union of closed M5 timestamps and conversion
rate timestamps inside the requested interval. Open positions are marked at
each event with the latest closed M5 price and latest conversion rate at or
before that timestamp. A price or conversion older than
`RiskConfiguration.max_price_age_seconds` rejects valuation of an open
portfolio. For an instrument with no open position, a decision with missing or
stale conversion is skipped and counted; a pending new entry whose fill-time
conversion is unavailable is canceled. Missing or stale conversion required
to close or mark an open position rejects the run instead of fabricating PnL.

At a shared timestamp, all per-instrument strategy signals are computed before
risk allocation changes. Risk sizing then visits instruments in ascending
`instrument_id`, reserving projected exposure from earlier allocations. The
aggregate exposure cap is checked again at the next M5 open using the actual
fill price and account-currency rate. Fill transitions at one timestamp use
the same stable instrument order. Input bars and FX rates must be unique and
ordered; bars must align to their UTC M5/M15/H1 boundaries. Only closed bars at
or before each decision are visible. Temporal gaps skip that instrument's
decision and are reported; no bars are synthesized.

Equity, realized and unrealized PnL, costs, exposure, and positions are stored
in the account currency on the shared portfolio curve and in per-instrument
curves. Costs are applied using each instrument's configured commission and
slippage. The initial account must be flat; positions are liquidated at the
last available in-range mark. Results include instrument-level trades and
metrics, aggregate metrics, configurations, costs, risk limits, dataset
fingerprint, and strategy, engine, and portfolio-runner versions. Both the
instrument order and event order are canonicalized, so input list ordering
does not change allocations or run IDs. This flow is offline and never sends
orders.
