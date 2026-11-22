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
