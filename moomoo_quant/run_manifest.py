from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import config


TREND_ARTIFACTS = (
    "trend_equity_curve.csv",
    "trend_trades.csv",
    "trend_rebalance_history.csv",
    "trend_allocations.csv",
    "trend_yearly_performance.csv",
    "trend_comparison_equity.csv",
    "trend_performance_comparison.csv",
    "trend_yearly_comparison.csv",
    "trend_monthly_returns.csv",
    "trend_crisis_monthly.csv",
    "trend_asset_prices_jpy.csv",
    "trend_data_metadata.csv",
    "trend_current_signal.csv",
    "trend_robustness.csv",
    "trend_oos_performance.csv",
    "crisis_2022_monthly.csv",
    "crisis_2022_summary.json",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _commit_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=config.BASE_DIR,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def trend_parameters() -> dict:
    return {
        "assets": config.TREND_SYMBOLS,
        "momentum_months": config.TREND_MOMENTUM_MONTHS,
        "sma_months": config.TREND_SMA_MONTHS,
        "max_assets": config.TREND_MAX_ASSETS,
        "asset_weight": config.TREND_ASSET_WEIGHT,
        "signal_timing": "month-end close",
        "execution_timing": "next common trading-day open",
        "slippage_bps": config.SLIPPAGE_BPS,
        "fx_conversion_cost_bps": config.FX_CONVERSION_COST_BPS,
    }


def publish_trend_run(data_start: str, data_end: str) -> dict:
    config.RUNS_DIR.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc)
    params = trend_parameters()
    params_json = json.dumps(params, sort_keys=True, separators=(",", ":"))
    params_hash = hashlib.sha256(params_json.encode()).hexdigest()
    suffix = hashlib.sha256(f"{generated_at.isoformat()}:{params_hash}".encode()).hexdigest()[:10]
    run_id = f"trend-v1-{generated_at.strftime('%Y%m%dT%H%M%S%fZ')}-{suffix}"
    temp_dir = config.RUNS_DIR / f".{run_id}.tmp"
    final_dir = config.RUNS_DIR / run_id
    temp_dir.mkdir(parents=True, exist_ok=False)

    copied = []
    for name in TREND_ARTIFACTS:
        source = config.RESULTS_DIR / name
        if source.exists():
            shutil.copy2(source, temp_dir / name)
            copied.append(name)

    data_hashes = {}
    data_last_dates = {}
    for path in sorted(config.DATA_DIR.glob("*_daily.csv")):
        data_hashes[path.name] = sha256_file(path)
        try:
            last_line = path.read_text(encoding="utf-8").splitlines()[-1]
            data_last_dates[path.name] = last_line.split(",", 1)[0]
        except (IndexError, UnicodeDecodeError):
            data_last_dates[path.name] = None

    manifest = {
        "run_id": run_id,
        "strategy_id": config.TREND_STRATEGY_ID,
        "strategy_name": "JPY Multi-Asset Trend",
        "strategy_version": "1",
        "parameters": params,
        "parameters_hash": params_hash,
        "data_start_date": data_start,
        "data_end_date": data_end,
        "market_data_last_date": data_last_dates.get("SPY_daily.csv"),
        "usdjpy_last_date": data_last_dates.get(config.FX_CACHE_NAME),
        "generated_at": generated_at.isoformat(),
        "cost_model_version": config.COST_MODEL_VERSION,
        "base_currency": "JPY",
        "data_file_hashes": data_hashes,
        "commit_sha": _commit_sha(),
        "backtest_engine_version": config.BACKTEST_ENGINE_VERSION,
        "artifacts": copied,
    }
    (temp_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temp_dir, final_dir)

    pointer = config.RESULTS_DIR / "current_run.json"
    pointer_temp = pointer.with_suffix(".json.tmp")
    pointer_temp.write_text(
        json.dumps({"run_id": run_id, "path": str(final_dir)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(pointer_temp, pointer)
    return manifest


def resolve_current_run() -> tuple[Path, dict | None]:
    pointer = config.RESULTS_DIR / "current_run.json"
    if not pointer.exists():
        return config.RESULTS_DIR, None
    selected = json.loads(pointer.read_text(encoding="utf-8"))
    run_dir = config.RUNS_DIR / selected["run_id"]
    manifest_path = run_dir / "manifest.json"
    if not run_dir.is_dir() or not manifest_path.exists():
        raise RuntimeError("Current run pointer is incomplete")
    return run_dir, json.loads(manifest_path.read_text(encoding="utf-8"))
