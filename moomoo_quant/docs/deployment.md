# Deployment

Production paths:

- code: `/opt/stacks/moomoo-quant`
- persistent state: `/opt/data/moomoo-quant`
- Dashboard: `127.0.0.1:8501`
- public reverse proxy: `https://quant.rubusoo.com`

Required service environment includes `MOOMOO_QUANT_DATA_ROOT`,
`QUANT_ADMIN_MODE=false`, `QUANT_KILL_SWITCH=true`, and
`MOOMOO_SIMULATE_ENABLED=false`, `MOOMOO_SIMULATE_KILL_SWITCH=true`. Port 8501 remains
loopback-only; AWS exposes 80/443, not 8501.

The existing `moomoo-quant-daily.timer` is reused; its oneshot service runs
`python -m moomoo_quant.jobs.shadow_runner --run-once`. Do not create a second
timer. After code deployment install requirements, run `pytest`, initialize the
ledger and run the Shadow Runner once so the factor ETF
caches are present, then restart the dashboard. Do not rerun or overwrite a
frozen first-result file during deployment. The operator's existing Nginx
authentication choice is not changed by app deploy.

SIMULATE is deliberately not enabled in the recurring daily service. An
operator may run `jobs.simulate_runner --preflight`, `--bootstrap`, or `--sync`
as one-shot commands under the same service user. Bootstrap must be run during
the XNYS regular session and requires both the temporary environment gate and
the literal confirmation documented in the README. Never add those gates to
the Dashboard service or the daily timer.
