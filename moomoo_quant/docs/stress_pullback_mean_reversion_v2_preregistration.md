# Stress Pullback Mean Reversion v2 - Pre-registration

Status at registration: `RESEARCH`. Version: `2`.

This document and the canonical `STRESS_PULLBACK_V2` dictionary in `config.py`
were fixed before the first complete backtest. The config hash is computed from
canonical JSON and is written into every formal result. The first formal result
must be retained whether it passes or fails.

## Hypothesis and economic rationale

Broad US equities sometimes rebound after an unusually sharp, short-lived selloff
while the long-term trend remains intact. Forced de-risking and short-horizon
liquidity pressure can temporarily push price below its near-term equilibrium.
This is an event strategy, not a routine oscillator strategy.

## Frozen specification

- Asset universe: SPY only; long only; at most one position.
- Signal currency: USD. Reporting and account valuation currency: JPY.
- Stress regime: SPY three-session close-to-close return is at most -5%.
- Entry: stress regime is true, RSI(3) is below 15, and close is above SMA(200).
- Position: 50% of current JPY equity; residual stays as JPY cash.
- Exit: RSI(3) at least 50, five sessions held, or close is 4% below entry.
- Exit priority when simultaneous: stop loss, maximum holding period, RSI bounce.
- Re-entry: no overlapping position and ten completed sessions after an exit.
- Timing: a close signal is executed at the next SPY trading-session open.
- Costs: Moomoo Japan basic commission model, 5 bps slippage per side, and
  10 bps FX conversion cost per conversion.
- Cash and FX: buy converts JPY to USD and sell converts all proceeds back to JPY.
  FX cost is therefore charged on both entry and exit. No persistent USD cash.
- Initial research capital: JPY 100,000 with theoretical fractional shares.
- OOS: 2022-01-01 onward. Earlier observations are in-sample.

## Acceptance criteria

All gates must pass: causal market/FX alignment; 15 through 100 completed trades;
positive net expectancy; OOS Sharpe above zero; absolute monthly-return correlation
with Trend v1 below 0.70; at least three profitable calendar years; and no single
year above 75% of the sum of positive yearly returns.

Failure means `RESEARCH_REJECTED`, JPY 0 budget, no Shadow Account, and no Proposed
Orders. Passing only makes the version eligible for explicit `SHADOW` registration;
it does not authorize broker execution.

## Stability diagnostics

One-at-a-time nearby values (stress return -4.5%/-5.5%, RSI 12/18, holding 4/6)
are reported only to detect fragility. They cannot replace the frozen base case,
select a winner, or create another version automatically. No grid search exists.
