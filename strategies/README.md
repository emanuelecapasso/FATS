# Deterministic strategies

## Trend Following v1.0.0

The strategy emits a target position: `long` means enter or keep a long
position, `short` means enter or keep a short position, and `flat` means remain
flat or exit the current position. `StrategyConfiguration.current_position`
supplies the position before this evaluation so exit and maintenance decisions
are explicit. An opposite trend first produces `flat`; the strategy does not
reverse a position in one evaluation.

### Timeframe roles and rules

- **H1 regime:** an SMA-seeded EMA fast/slow relationship sets direction. A
  bullish regime requires fast EMA above slow EMA and the latest H1 close above
  the fast EMA. A bearish regime is the inverse.
- **M15 confirmation:** the latest close must be above its EMA and that EMA must
  be rising for a long; both conditions are inverted for a short.
- **M5 entry:** while flat, enter only when H1 and M15 agree and the latest M5
  close is beyond the prior breakout lookback highs/lows and on the matching
  side of its entry EMA.
- **M5 exit and maintenance:** while long, exit to flat if H1/M15 alignment is
  lost or the latest M5 close falls below the entry EMA. While short, exit if
  alignment is lost or the close rises above the entry EMA. Otherwise, emit the
  current direction with a `position_maintained` motivation code.
- If flat and no aligned breakout exists, return `flat` with `no_entry`.

### Parameters and warm-up

All parameters are positive integers passed in
`StrategyConfiguration.parameters`; unknown keys are rejected. Defaults:

| Parameter | Default | Meaning |
| --- | ---: | --- |
| `h1_fast_period` | 20 | Fast H1 EMA period |
| `h1_slow_period` | 50 | Slow H1 EMA period; must exceed fast period |
| `m15_ema_period` | 20 | M15 confirmation EMA period |
| `m5_entry_ema_period` | 9 | M5 entry and exit EMA period |
| `breakout_lookback` | 3 | Prior contiguous M5 bars used for breakout |

Warm-up is derived from configuration: M5 requires the greater of the entry EMA
period and breakout lookback plus one bar; M15 requires its EMA period plus one
bar to compare EMA slope; H1 requires the slow EMA period.

The strategy never fills or interpolates missing bars. Missing timeframes or
history below warm-up raise `InsufficientHistoryError`. A gap in the contiguous
M5 breakout window also raises that error. Stale data, future timestamps,
out-of-order/duplicate bars, and any bar whose `is_closed` value is not exactly
`true` are rejected; partial series are usable only when every supplied bar is
explicitly closed. All calculations use only the snapshot and configuration,
and are deterministic for identical inputs and strategy version.

H1 and M15 EMA windows count observed, closed candles; absent observations are
not synthesized or forward-filled. M5 entry additionally requires the recent
breakout window to have exact five-minute spacing.

Signals use the canonical `core.schemas.Signal`; their motivation codes identify
entry, exit, maintenance, or the reason no entry was made. Confidence scores are
fixed deterministic rule outputs: 0.85 for entry, 0.70 for maintenance, 0.75
for exit, and 0.50 for no entry. They are not calibrated probabilities. The
engine attaches the strategy ID and version to the canonical evaluation
response.

## Mean Reversion v1.0.0

This strategy emits a target position using `StrategyConfiguration.current_position`:
`long`/`short` means enter or keep that position; `flat` means stay flat or exit.
H1 avoids fading strong trends, M15 measures price extension from its rolling
mean, and M5 confirms that price has started to return toward the mean.

### Timeframe roles and rules

- **H1 filter:** calculate fast and slow SMA-seeded EMAs. New entries are
  blocked, and an open position is exited, when their absolute spread divided
  by the latest close exceeds `max_h1_trend_spread`.
- **M15 setup:** calculate a population-standard-deviation z-score of the most
  recent `m15_bollinger_period` closes. A value at or below negative
  `entry_zscore` is oversold; a value at or above positive `entry_zscore` is
  overbought.
- **M5 confirmation:** enter long only when the latest close crosses above the
  M5 rolling mean after an oversold M15 setup. Enter short only when it crosses
  below the M5 mean after an overbought setup.
- **Exit and hold:** exit long when H1 trend strength exceeds its limit, M15
  z-score reaches `-exit_zscore`, or it breaches the adverse `-stop_zscore`.
  Exit short under the mirrored conditions. Otherwise emit the current position
  with `position_maintained`.
- The strategy does not reverse directly: an exit returns `flat`; any opposite
  entry requires a later evaluation from a flat position.

### Parameters and warm-up

Parameters are positive, finite values in `StrategyConfiguration.parameters`;
unknown keys are rejected. Period values must be integers. Defaults:

| Parameter | Default | Meaning |
| --- | ---: | --- |
| `h1_fast_period` | 20 | Fast H1 EMA period |
| `h1_slow_period` | 50 | Slow H1 EMA period; must exceed fast period |
| `max_h1_trend_spread` | 0.005 | Maximum EMA spread as a fraction of H1 close (0.5%) |
| `m15_bollinger_period` | 20 | M15 closes used for mean and population standard deviation |
| `entry_zscore` | 2.0 | M15 extension required for a new setup |
| `exit_zscore` | 0.2 | Mean-reversion completion threshold |
| `stop_zscore` | 3.0 | Adverse M15 extension that exits an open position |
| `m5_mean_period` | 5 | M5 rolling-mean period |

Warm-up is derived from configuration: M5 requires `m5_mean_period + 1` bars for
the cross, M15 requires `m15_bollinger_period` bars, and H1 requires
`h1_slow_period` bars. Missing timeframes or insufficient observations raise
`InsufficientHistoryError`. No observations are filled or interpolated; the
last two M5 bars must be exactly five minutes apart. H1/M15 windows use observed
closed bars. Stale data, future timestamps, duplicate/out-of-order bars and any
bar not explicitly marked closed are rejected. Partial series are allowed only
when every supplied bar is explicitly closed.

Motivation codes identify entries, maintenance, exits and rejected setups.
Confidence scores are fixed deterministic rule outputs (0.85 entry, 0.70 hold,
0.75 exit, 0.50 no entry), not calibrated probabilities. The canonical engine
response includes the strategy ID and version.
