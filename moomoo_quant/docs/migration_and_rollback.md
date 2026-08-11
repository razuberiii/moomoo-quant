# Migration and Rollback

The original `shadow/*.csv` and `state.json` remain untouched as read-only
history. No fill is copied, no historical order is synthesized, and the latest
baseline is not backfilled. A single idempotent
`LEGACY_SHADOW_PRESERVED_READ_ONLY` event records this policy.

The new multi-strategy portfolio starts separately. Its initial JPY 100,000 is
allocated to Trend v1; Mean Reversion receives JPY 0 because it failed research
acceptance. This avoids silently splitting the old budget.

Before server deployment, archive both `/opt/stacks/moomoo-quant` and
`/opt/data/moomoo-quant`. Rollback stops the services, restores both archives,
runs `systemctl daemon-reload`, and restarts the dashboard. SQLite WAL/SHM files
must be restored together with the database when present.
