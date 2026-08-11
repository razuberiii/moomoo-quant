# Migration and Rollback

The original `shadow/*.csv` and `state.json` remain untouched as read-only
history. No fill is copied, no historical order is synthesized, and the latest
baseline is not backfilled. A single idempotent
`LEGACY_SHADOW_PRESERVED_READ_ONLY` event records this policy.

The new multi-strategy portfolio starts separately. Trend v1 and Risk Parity v1
each receive a dedicated JPY 100,000 virtual budget. Mean Reversion, Stress
Pullback, Defensive Multi-Factor v1 and the current reproducible Quality & Low
Volatility v2 result remain at JPY 0. Each funded strategy records its own
baseline without backfilling a historical virtual fill. This avoids silently
splitting the old budget or assigning one strategy's position to another.

Before server deployment, archive both `/opt/stacks/moomoo-quant` and
`/opt/data/moomoo-quant`. Rollback stops the services, restores both archives,
runs `systemctl daemon-reload`, and restarts the dashboard. SQLite WAL/SHM files
must be restored together with the database when present.
