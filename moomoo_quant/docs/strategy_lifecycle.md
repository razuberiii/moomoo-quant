# Strategy Lifecycle

Stable stages are `RESEARCH`, `BACKTEST`, `SHADOW`, `SIMULATE`, and `LIVE`.
Transitions are explicit, sequential, approved events; no job can auto-promote a
strategy. This release keeps strategy lifecycle at `SHADOW`; a parallel,
portfolio-level SIMULATE execution-validation ledger is implemented but remains
disabled and unapproved for order submission. `LIVE` always raises
`PermissionError`; no paper-order result can promote any strategy to LIVE.

- JPY Multi-Asset Trend v1: `SHADOW`
- US ETF Short-Term Mean Reversion v1: `RESEARCH_REJECTED`, frozen first result
- Stress Pullback Mean Reversion v2: `RESEARCH_REJECTED`, frozen first result
- US Quality & Low Volatility v2: `SHADOW`, JPY 100,000; the current
  OpenD-cache reproduction's SPY JPY drawdown comparison is an advisory warning
- JPY Unlevered Risk Parity v1: `SHADOW`, JPY 100,000, no baseline backfill

Lifecycle history records entered time, approver, evidence run ID, notes and any
rejection reason. No scheduled job promotes a strategy. Database enum values
remain English and the UI translates them.

`strategy-admission-v1` separates hard validity/executability checks from
portfolio warnings. Hard checks cover evidence, JPY net reporting, commission,
slippage, FX cost, 0.001-share rounding, nonnegative cash, long-only/unlevered
weights, at least ten years of history, positive operational net CAGR and data
alignment. Benchmark outperformance and correlation are advisory. Admission
records are content-hashed and stored under `results/admissions/`; mutable
research reruns cannot promote, reject or resize an admitted strategy.
