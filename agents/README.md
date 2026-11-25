# Coordinator and agent alignment

The onboarding flow is exposed at `POST /api/v1/onboarding/next-question`.
Pass prior answers in `answers`; the response contains exactly one next
question, or reports that all five questions are complete. The service is
stateless and does not retain account size, broker details, or other answers.
It does not build a team or initiate a trade.

## Prompt roles mapped to repository capabilities

| Role | Current implementation | Boundary |
| --- | --- | --- |
| Coordinator | Stateless five-question onboarding and the prompt in `TRADING_TEAM_BUILDER.md` | No LLM routing, saved conversation, or daily messaging integration. |
| Scout | Strategy implementations and quantitative evaluation | No continuous scanner, market schedule, or alert delivery. |
| Lab | Offline backtests, walk-forward selection, portfolio replay, and regime analysis | Historical simulation only; results do not establish future performance. |
| Shield | `DeterministicRiskEvaluator`, stale-price rejection, 3% default daily stop, and 3-loss default stop | Returns a risk decision; it cannot submit or close broker orders. |
| Journal | Backtest trade and decision outputs | No durable journal or weekly review service yet. |
| Pilot | Not implemented | No order adapter, paper broker connection, or live execution path. |

The current API is therefore a quantitative and simulation foundation, not a
deployed multi-agent or 24/7 trading bot. Agenkit, TradingView webhooks,
notifications, persistence, cloud hosting, and recovery monitoring require
separate verified integrations before they can be described as operational.

## Safety behavior

`RiskConfiguration.max_daily_loss_pct` defaults to `0.03` and cannot be set
higher; `max_consecutive_losses` defaults to `3` and cannot be relaxed. A
directional decision fails closed if `RiskAccountState.consecutive_losses` is
unknown. The backtest runners derive that count from closed trades. A risk
denial blocks new exposure and does not itself turn an open position into an
exit signal; a strategy's explicit flat signal remains an exit in simulation.
No repository endpoint sends orders.
