# Deployment

Production paths:

- code: `/opt/stacks/moomoo-quant`
- persistent state: `/opt/data/moomoo-quant`
- Dashboard: `127.0.0.1:8501`
- public reverse proxy: `https://quant.rubusoo.com`

Required service environment includes `MOOMOO_QUANT_DATA_ROOT`,
`QUANT_ADMIN_MODE=false`, `QUANT_KILL_SWITCH=true`, and
`MOOMOO_SIMULATE_ENABLED=false`. Port 8501 remains
loopback-only; AWS exposes 80/443, not 8501.

The existing `moomoo-quant-daily.timer` is reused; its oneshot service runs
`python -m moomoo_quant.jobs.shadow_runner --run-once`. Do not create a second
timer. After code deployment install requirements, run the fixed research suite,
ledger migration and `pytest`, then restart the dashboard. The operator's
existing Nginx authentication choice is not changed by app deploy.
