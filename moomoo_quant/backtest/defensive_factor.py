from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .. import config
from ..strategies.jpy_multi_asset_trend import prepare_jpy_daily
from ..trend_data import (
    load_cached_defensive_factor_history,
    load_cached_trend_history,
)
from .research_common import (
    crisis_returns,
    finish_statistics,
    immutable_first_result,
    monthly_correlation,
    segment_metrics,
    write_research_result,
    yearly_concentration,
)
from .trend_engine import run_trend_backtest


def defensive_factor_signals(daily: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
    assets = params["asset_universe"]
    common = next(iter(daily.values())).index
    for symbol in assets:
        common = common.intersection(daily[symbol].index)
    months = (6, 12) if params["rebalance_frequency"] == "calendar_half_year_end" else (3, 6, 9, 12)
    completed = common[common.month.isin(months)]
    quarter_ends = pd.Series(completed, index=completed).groupby(completed.to_period("Q")).last()
    rows: list[dict] = []
    for signal_date in pd.DatetimeIndex(quarter_ends.to_numpy()):
        row = {
            "signal_date": signal_date,
            "selected": ",".join(assets),
            "JPY_CASH_weight": 0.0,
        }
        for symbol in daily:
            row[f"{symbol}_weight"] = float(params["target_weights"].get(symbol, 0.0))
        rows.append(row)
    return pd.DataFrame(rows)


def _average_rebalance_spacing(trades: pd.DataFrame) -> float:
    if trades.empty:
        return 0.0
    dates = pd.to_datetime(pd.Series(sorted(trades["trade_date"].unique())))
    return float(dates.diff().dt.days.dropna().mean()) if len(dates) > 1 else 0.0


def _formal_result(daily: dict[str, pd.DataFrame], params: dict) -> dict:
    signals = defensive_factor_signals(daily, params)
    net = run_trend_backtest(
        daily,
        signals_override=signals,
        initial_cash_jpy=params["initial_cash_jpy"],
        slippage_bps=params["slippage_bps"],
        fx_cost_bps=params["fx_conversion_cost_bps"],
        commission_enabled=True,
    )
    gross = run_trend_backtest(
        daily,
        signals_override=signals,
        initial_cash_jpy=params["initial_cash_jpy"],
        slippage_bps=0.0,
        fx_cost_bps=0.0,
        commission_enabled=False,
    )
    stats, monthly, yearly = finish_statistics(
        net["equity_curve"],
        params["initial_cash_jpy"],
        gross["stats"]["total_return"],
        net["trades"],
        net["stats"]["turnover"],
        net["stats"]["total_commission_jpy"],
        net["stats"]["total_slippage_jpy"],
        net["stats"]["total_fx_cost_jpy"],
        _average_rebalance_spacing(net["trades"]),
    )
    days = (pd.Timestamp(net["equity_curve"]["date"].iloc[-1]) - pd.Timestamp(net["equity_curve"]["date"].iloc[0])).days
    years = days / 365.25 if days > 0 else 0.0
    stats["history_years"] = years
    stats["annualized_turnover"] = stats["turnover"] / years if years else float("inf")
    stats["cost_drag"] = float(gross["stats"]["total_return"] - net["stats"]["total_return"])
    return {
        "equity": net["equity_curve"],
        "trades": net["trades"],
        "signals": signals,
        "stats": stats,
        "monthly": monthly,
        "yearly": yearly,
        "spy_jpy_stats": net["benchmark_stats"]["SPY_JPY"],
    }


def _run_defensive_factor_version(params: dict, prefix: str, strategy_name: str) -> dict:
    params = json.loads(json.dumps(params))
    trend_assets, fx = load_cached_trend_history()
    factor_assets = load_cached_defensive_factor_history()
    assets = {
        **factor_assets,
        "SPY": trend_assets["SPY"],
        "QQQ": trend_assets["QQQ"],
    }
    daily = prepare_jpy_daily(assets, fx)
    formal = _formal_result(daily, params)
    stats, monthly, yearly = formal["stats"], formal["monthly"], formal["yearly"]
    trend_corr = monthly_correlation(monthly, "trend_monthly_returns.csv")
    parity_corr = monthly_correlation(monthly, "risk_parity_v1_monthly.csv")
    oos = segment_metrics(formal["equity"], start=params["oos_start"])
    insample = segment_metrics(formal["equity"], end="2020-12-31")
    alignment = all(
        bool((frame["fx_date"] <= frame.index).all() and (frame["exec_fx_date"] < frame.index).all())
        for frame in daily.values()
    )
    weight_columns = [f"{symbol}_weight" for symbol in params["asset_universe"]]
    weights = formal["signals"][weight_columns]
    acceptance = params["acceptance"]
    gates = {
        "data_alignment": alignment,
        "minimum_history": stats["history_years"] >= acceptance["minimum_history_years"],
        "long_only_unlevered": bool((weights >= -1e-12).all().all() and np.allclose(weights.sum(axis=1), 1.0)),
        "positive_net_cagr": stats["cagr"] > acceptance["minimum_net_cagr"],
        "oos_sharpe": bool(oos and oos["sharpe"] >= acceptance["minimum_oos_sharpe"]),
        "positive_year_fraction": stats["positive_year_fraction"] >= acceptance["minimum_positive_year_fraction"],
        "low_turnover": stats["annualized_turnover"] <= acceptance["maximum_annualized_turnover"],
        "cost_control": stats["cost_drag"] <= acceptance["maximum_cost_drag"],
        "trend_correlation": trend_corr is not None and abs(trend_corr) < acceptance["maximum_abs_trend_correlation"],
        "risk_parity_correlation": parity_corr is not None and abs(parity_corr) < acceptance["maximum_abs_risk_parity_correlation"],
        "improves_spy_jpy_max_drawdown": stats["max_drawdown"] > formal["spy_jpy_stats"]["max_drawdown"],
    }
    status = "SHADOW" if all(gates.values()) else "RESEARCH_REJECTED"
    generated = datetime.now(timezone.utc)
    parameters_hash = config.stable_config_hash(params)
    run_id = f"{prefix.replace('_', '-')}-{generated.strftime('%Y%m%dT%H%M%SZ')}-{parameters_hash[:8]}"
    latest_target = {}
    for key, value in formal["signals"].iloc[-1].to_dict().items():
        if isinstance(value, pd.Timestamp):
            latest_target[key] = value.date().isoformat()
        elif isinstance(value, np.generic):
            latest_target[key] = value.item()
        else:
            latest_target[key] = value
    latest_signal_date = pd.Timestamp(formal["signals"].iloc[-1]["signal_date"])
    first_asset = params["asset_universe"][0]
    latest_target["usdjpy"] = float(daily[first_asset].at[latest_signal_date, "usdjpy"])
    for symbol in params["asset_universe"]:
        latest_target[f"{symbol}_usd_close"] = float(daily[symbol].at[latest_signal_date, "close_price"])
        latest_target[f"{symbol}_jpy_price"] = float(daily[symbol].at[latest_signal_date, "jpy_close"])
    summary = {
        "run_id": run_id,
        "strategy_id": config.DEFENSIVE_FACTOR_STRATEGY_ID,
        "strategy_name": strategy_name,
        "strategy_version": params["version"],
        "parameters": params,
        "parameters_hash": parameters_hash,
        "status": status,
        "budget_jpy": 100_000.0 if status == "SHADOW" else 0.0,
        "data_start": formal["equity"]["date"].iloc[0].date().isoformat(),
        "data_end": formal["equity"]["date"].iloc[-1].date().isoformat(),
        "generated_at": generated.isoformat(),
        "base_currency": "JPY",
        "historical_data_source": "local adjusted-OHLC cache",
        "stats": stats,
        "in_sample_metrics": insample,
        "oos_metrics": oos,
        "crisis_returns": crisis_returns(monthly),
        "monthly_correlation_with_trend_v1": trend_corr,
        "monthly_correlation_with_risk_parity_v1": parity_corr,
        "year_concentration": yearly_concentration(yearly),
        "acceptance_gates": gates,
        "rejection_reasons": [name for name, passed in gates.items() if not passed],
        "latest_target": latest_target,
        "stability_is_diagnostic_only": True,
    }
    result = {"summary": summary, **formal, "stability": pd.DataFrame()}
    write_research_result(prefix, result)
    formal["signals"].to_csv(config.RESULTS_DIR / f"{prefix}_signals.csv", index=False)
    immutable_first_result(config.RESULTS_DIR / f"{prefix}_first_result.json", summary)
    return result


def run_defensive_factor_v1() -> dict:
    return _run_defensive_factor_version(
        config.DEFENSIVE_FACTOR_V1,
        "defensive_factor_v1",
        "US Defensive Multi-Factor",
    )


def run_defensive_factor_v2() -> dict:
    return _run_defensive_factor_version(
        config.DEFENSIVE_FACTOR_V2,
        "defensive_factor_v2",
        "US Quality & Low Volatility",
    )
