# JPY Unlevered Risk Parity v1 - Pre-registration

Status at registration: `RESEARCH`. Version: `1`.

This document and the canonical `RISK_PARITY_V1` dictionary in `config.py` were
fixed before the first complete backtest. The canonical config hash is included
in the immutable first formal result.

## Hypothesis and portfolio role

SPY, gold, and intermediate US Treasuries carry different economic risks. A JPY
investor can reduce dependence on equity direction by allocating to inverse JPY
volatility rather than forecasting the next winner. This is a diversification
strategy; it is not intended to maximize raw return.

## Frozen specification

- Assets: SPY, GLD, IEF, and residual JPY cash. QQQ is excluded to avoid duplicate
  equity risk with SPY.
- All risk estimates, weights, returns, and acceptance tests use JPY price series.
- Method: inverse volatility using 63 daily log returns. Sample covariance without
  shrinkage is recorded for diagnostics; weights use its diagonal volatilities.
- Long only, no negative weights, no borrowing, no leverage, no options.
- Single-asset cap: 45%. Minimum weight: 0%. Total risky weight cannot exceed 100%.
- No target-volatility scaling. Valid target weights are normalized and capped;
  residual and invalid-data allocation remains JPY cash.
- Warm-up: 63 complete common sessions. Missing or invalid data keeps the previous
  valid allocation; before the first valid signal the account remains JPY cash.
- Review: last common trading session of each month. Rebalance only when the
  largest absolute target change is at least five percentage points.
- Execution: next common trading-session open using only information available at
  the preceding month-end close.
- Costs: Moomoo Japan basic commission model, 5 bps slippage, and 10 bps FX cost
  on every US-asset buy or sell. Initial capital JPY 100,000; fractional shares.
- OOS: 2019-01-01 onward. Earlier observations are in-sample.

## Acceptance criteria

All gates must pass: causal market/FX alignment; no leverage or negative weight;
positive net CAGR; OOS Sharpe at least 0.50; maximum drawdown less severe than JPY
SPY buy-and-hold; absolute monthly-return correlation with Trend v1 below 0.85;
and at least 60% of calendar years positive.

Failure means `RESEARCH_REJECTED`, JPY 0 budget, no Shadow Account, and no Proposed
Orders. Passing permits an explicitly registered JPY 100,000 local Shadow Account
starting from the next true month-end only. It does not authorize broker execution.

## Stability diagnostics

Lookbacks 50/76 days, caps 40%/50%, and rebalance thresholds 4%/6% are evaluated
one at a time only. They diagnose fragility and cannot select or overwrite v1.
