# Strategy Lifecycle

Stable stages are `RESEARCH`, `BACKTEST`, `SHADOW`, `SIMULATE`, and `LIVE`.
Transitions are explicit, sequential, approved events; no job can auto-promote a
strategy. In this release `SIMULATE` and `LIVE` always raise `PermissionError`.

- JPY Multi-Asset Trend v1: `SHADOW`
- US ETF Short-Term Mean Reversion v1: `RESEARCH_REJECTED`, frozen first result
- Stress Pullback Mean Reversion v2: `RESEARCH_REJECTED`, frozen first result
- JPY Unlevered Risk Parity v1: `SHADOW`, JPY 100,000, no baseline backfill

Lifecycle history records entered time, approver, evidence run ID, notes and any
rejection reason. No scheduled job promotes a strategy. Database enum values
remain English and the UI translates them.
