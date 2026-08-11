# Shadow Ledger

`multi_strategy/shadow_ledger.sqlite3` is the default local path. Production uses
`$MOOMOO_QUANT_DATA_ROOT/multi_strategy/shadow_ledger.sqlite3`.

The schema covers strategy definitions/accounts/budgets, lifecycle, run
manifests, signals and input snapshots, target allocations, attributed positions,
cash events, virtual fills, equity and benchmark snapshots, portfolio snapshots,
aggregated targets, risk decisions, proposed orders, reconciliation and migration
events. Event tables reject updates and deletes through SQLite triggers. Stable
IDs and unique constraints make reruns idempotent.

This is theoretical accounting. Fractional quantity is the research quantity;
whole-share quantity is a separate feasibility estimate.
