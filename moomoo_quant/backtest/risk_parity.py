from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .. import config
from ..strategies.jpy_multi_asset_trend import prepare_jpy_daily
from ..trend_data import load_cached_trend_history
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


def _capped_inverse_vol(volatility: pd.Series, cap: float) -> pd.Series:
    if volatility.isna().any() or (volatility <= 0).any():
        raise ValueError("Volatility inputs must be finite and positive")
    raw = (1 / volatility).astype(float)
    weights = raw / raw.sum()
    fixed: set[str] = set()
    while (weights > cap + 1e-12).any():
        newly_fixed = set(weights[weights > cap].index) - fixed
        if not newly_fixed:
            break
        fixed |= newly_fixed
        weights.loc[list(fixed)] = cap
        free = [symbol for symbol in weights.index if symbol not in fixed]
        remaining = 1.0 - cap * len(fixed)
        if not free or remaining <= 0:
            break
        free_raw = raw.loc[free]
        weights.loc[free] = remaining * free_raw / free_raw.sum()
    return weights.clip(lower=0.0, upper=cap)


def risk_parity_signals(daily: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
    assets = params["asset_universe"]
    common_index = next(iter(daily.values())).index
    prices = pd.DataFrame({symbol: daily[symbol]["jpy_close"] for symbol in assets}, index=common_index)
    log_returns = np.log(prices / prices.shift(1))
    month_ends = prices.groupby(prices.index.to_period("M")).tail(1).index
    current_period = pd.Timestamp.today().to_period("M")
    if len(month_ends) and month_ends[-1].to_period("M") == current_period:
        month_ends = month_ends[:-1]
    previous = pd.Series(0.0, index=assets)
    rows: list[dict] = []
    for dt in month_ends:
        history = log_returns.loc[:dt].tail(params["volatility_lookback_days"])
        valid = len(history.dropna()) >= params["warmup_days"] and np.isfinite(history.to_numpy()).all()
        retained = False
        covariance = None
        if valid:
            covariance = history.cov() * 252
            target = _capped_inverse_vol(
                pd.Series(np.sqrt(np.diag(covariance)), index=assets),
                params["maximum_asset_weight"],
            )
            if previous.sum() > 0 and float((target - previous).abs().max()) < params["rebalance_threshold"]:
                target = previous.copy()
                retained = True
            previous = target.copy()
        else:
            target = previous.copy()
            retained = previous.sum() > 0
        row = {
            "signal_date": dt,
            "selected": ",".join(target[target > 0].index),
            "JPY_CASH_weight": max(0.0, 1.0 - float(target.sum())),
            "retained_previous": retained,
            "data_valid": valid,
        }
        for symbol in daily:
            row[f"{symbol}_weight"] = float(target.get(symbol, 0.0))
        if covariance is not None:
            portfolio_variance = float(target.to_numpy() @ covariance.to_numpy() @ target.to_numpy())
            marginal = covariance.to_numpy() @ target.to_numpy()
            for index, symbol in enumerate(assets):
                contribution = target.iloc[index] * marginal[index] / portfolio_variance if portfolio_variance > 0 else 0.0
                row[f"{symbol}_risk_contribution"] = float(contribution)
                row[f"{symbol}_annual_volatility"] = float(np.sqrt(covariance.iloc[index, index]))
        rows.append(row)
    return pd.DataFrame(rows)


def _average_rebalance_spacing(trades: pd.DataFrame) -> float:
    if trades.empty:
        return 0.0
    dates = pd.to_datetime(pd.Series(sorted(trades["trade_date"].unique())))
    return float(dates.diff().dt.days.dropna().mean()) if len(dates) > 1 else 0.0


def _return_contributions(equity: pd.DataFrame, daily: dict[str, pd.DataFrame], assets: list[str]) -> dict:
    dates = pd.DatetimeIndex(equity["date"])
    fx_return = daily[assets[0]].loc[dates, "usdjpy"].pct_change().fillna(0.0)
    usd_component = pd.Series(0.0, index=dates)
    fx_component = pd.Series(0.0, index=dates)
    for symbol in assets:
        usd_return = daily[symbol].loc[dates, "close_price"].pct_change().fillna(0.0)
        weight = equity.set_index("date")[f"{symbol}_weight"].shift(1).fillna(0.0)
        usd_component += weight * usd_return
        fx_component += weight * ((1 + usd_return) * (1 + fx_return) - 1 - usd_return)
    return {
        "usd_return_contribution": float(usd_component.sum()),
        "fx_return_contribution": float(fx_component.sum()),
    }


def _formal_result(daily: dict[str, pd.DataFrame], params: dict) -> dict:
    signals = risk_parity_signals(daily, params)
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
    stats.update(_return_contributions(net["equity_curve"], daily, params["asset_universe"]))
    stats["rebalance_count"] = int(net["stats"]["number_of_rebalances"])
    stats["average_holding_days_definition"] = "average calendar days between executed rebalances"
    return {
        "equity": net["equity_curve"],
        "trades": net["trades"],
        "signals": signals,
        "stats": stats,
        "monthly": monthly,
        "yearly": yearly,
        "spy_jpy_max_drawdown": net["benchmark_stats"]["SPY_JPY"]["max_drawdown"],
    }


def run_risk_parity_v1() -> dict:
    params = json.loads(json.dumps(config.RISK_PARITY_V1))
    assets, fx = load_cached_trend_history()
    daily = prepare_jpy_daily(assets, fx)
    formal = _formal_result(daily, params)
    stats, monthly, yearly = formal["stats"], formal["monthly"], formal["yearly"]
    trend_corr = monthly_correlation(monthly, "trend_monthly_returns.csv")
    stress_corr = monthly_correlation(monthly, "stress_pullback_v2_monthly.csv")
    factor_corr = monthly_correlation(monthly, "defensive_factor_v2_monthly.csv")
    oos = segment_metrics(formal["equity"], start=params["oos_start"])
    insample = segment_metrics(formal["equity"], end="2018-12-31")
    alignment = all(
        bool((frame["fx_date"] <= frame.index).all() and (frame["exec_fx_date"] < frame.index).all())
        for frame in daily.values()
    )
    weight_columns = [f"{symbol}_weight" for symbol in params["asset_universe"]]
    weights = formal["signals"][weight_columns]
    gates = {
        "data_alignment": alignment,
        "unlevered_nonnegative_weights": bool((weights >= -1e-12).all().all() and (weights.sum(axis=1) <= 1 + 1e-12).all()),
        "positive_net_cagr": stats["cagr"] > params["acceptance"]["minimum_net_cagr"],
        "oos_sharpe": bool(oos and oos["sharpe"] >= params["acceptance"]["minimum_oos_sharpe"]),
        "improves_spy_jpy_max_drawdown": stats["max_drawdown"] > formal["spy_jpy_max_drawdown"],
        "trend_correlation": trend_corr is not None and abs(trend_corr) < params["acceptance"]["maximum_abs_trend_correlation"],
        "positive_year_fraction": stats["positive_year_fraction"] >= params["acceptance"]["minimum_positive_year_fraction"],
    }
    status = "SHADOW" if all(gates.values()) else "RESEARCH_REJECTED"

    nearby = [
        ("lookback", "volatility_lookback_days", 50),
        ("lookback", "volatility_lookback_days", 76),
        ("weight_cap", "maximum_asset_weight", 0.40),
        ("weight_cap", "maximum_asset_weight", 0.50),
        ("rebalance_threshold", "rebalance_threshold", 0.04),
        ("rebalance_threshold", "rebalance_threshold", 0.06),
    ]
    stability_rows = []
    for dimension, key, value in nearby:
        varied = json.loads(json.dumps(params))
        varied[key] = value
        if key == "volatility_lookback_days":
            varied["warmup_days"] = value
        output = _formal_result(daily, varied)
        stability_rows.append(
            {"dimension": dimension, "parameter": key, "value": value, "net_return": output["stats"]["net_return"], "cagr": output["stats"]["cagr"], "sharpe": output["stats"]["sharpe"], "trades": len(output["trades"]), "selection_allowed": False}
        )
    stability = pd.DataFrame(stability_rows)
    generated = datetime.now(timezone.utc)
    run_id = f"risk-parity-v1-{generated.strftime('%Y%m%dT%H%M%SZ')}-{config.RISK_PARITY_V1_HASH[:8]}"
    latest_target = {}
    for key, value in formal["signals"].iloc[-1].to_dict().items():
        if isinstance(value, pd.Timestamp):
            latest_target[key] = value.date().isoformat()
        elif isinstance(value, np.generic):
            latest_target[key] = value.item()
        else:
            latest_target[key] = value
    summary = {
        "run_id": run_id,
        "strategy_id": config.RISK_PARITY_STRATEGY_ID,
        "strategy_name": "JPY Unlevered Risk Parity",
        "strategy_version": "1",
        "parameters": params,
        "parameters_hash": config.RISK_PARITY_V1_HASH,
        "status": status,
        "budget_jpy": 100_000.0 if status == "SHADOW" else 0.0,
        "data_start": formal["equity"]["date"].iloc[0].date().isoformat(),
        "data_end": formal["equity"]["date"].iloc[-1].date().isoformat(),
        "generated_at": generated.isoformat(),
        "base_currency": "JPY",
        "stats": stats,
        "in_sample_metrics": insample,
        "oos_metrics": oos,
        "crisis_returns": crisis_returns(monthly),
        "monthly_correlation_with_trend_v1": trend_corr,
        "monthly_correlation_with_stress_pullback_v2": stress_corr,
        "monthly_correlation_with_defensive_factor_v2": factor_corr,
        "year_concentration": yearly_concentration(yearly),
        "acceptance_gates": gates,
        "rejection_reasons": [name for name, passed in gates.items() if not passed],
        "latest_target": latest_target,
        "stability_is_diagnostic_only": True,
    }
    result = {"summary": summary, **formal, "stability": stability}
    write_research_result("risk_parity_v1", result)
    formal["signals"].to_csv(config.RESULTS_DIR / "risk_parity_v1_signals.csv", index=False)
    immutable_first_result(config.RESULTS_DIR / "risk_parity_v1_first_result.json", summary)
    return result
