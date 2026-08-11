# Portfolio and Risk Rules

Portfolio Manager calculates target JPY notional from each strategy's own
allocation, converts with timestamped USDJPY, derives fractional and whole-share
quantities, and aggregates by symbol without losing attribution. If Robot A exits
SPY while Robot B retains it, only A's attributed quantity is removed.

Risk policy has three scopes. `SHADOW_SCOPE` checks market/FX freshness, XNYS
calendar validity, signal version, strategy budget, idempotency and ledger
consistency; the execution kill switch does not block a local Virtual Fill.
`PROPOSAL_SCOPE` adds portfolio aggregation, duplicate proposal, symbol/quantity
allowlists, strategy attribution and OpenD health. `EXECUTION_SCOPE` always
returns `EXECUTION_DISABLED`. All scopes reject shorts and leverage.

Forward Shadow and operational backtest replay share an execution profile:
Moomoo Japan Basic commission, 5 bps slippage, JPY 0.25 per converted USD and a
0.001-share quantity step. Results labelled as running performance are NET of
those costs and residual cash drag. Fractional eligibility for every symbol is
still a required broker capability check before any separately approved
SIMULATE or REAL phase.
