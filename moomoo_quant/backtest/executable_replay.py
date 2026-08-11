from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd

from .. import config
from ..strategies.jpy_multi_asset_trend import monthly_signals, prepare_jpy_daily
from ..trend_data import load_cached_defensive_factor_history, load_cached_trend_history
from .defensive_factor import defensive_factor_signals
from .research_common import period_outputs
from .risk_parity import risk_parity_signals
from .trend_engine import _performance, run_trend_backtest


def _run(
    daily: dict[str, pd.DataFrame],
    signals: pd.DataFrame,
    start: str | None = None,
) -> dict:
    """Replay a frozen signal stream with the common operational cost profile."""
    return run_trend_backtest(
        daily,
        signals_override=signals,
        start=start,
        initial_cash_jpy=config.PORTFOLIO_CAPITAL_JPY,
        slippage_bps=config.SLIPPAGE_BPS,
        fx_cost_bps=0.0,
        commission_enabled=True,
        quantity_step=config.OPERATIONAL_QUANTITY_STEP,
        fx_fee_jpy_per_usd=config.OPERATIONAL_FX_FEE_JPY_PER_USD,
    )


def _summary(output: dict, strategy_id: str, version: str) -> dict:
    stats = dict(output["stats"])
    trades = output["trades"]
    stats["net_return"] = stats["total_return"]
    stats["fees_jpy"] = stats.pop("total_commission_jpy")
    stats["slippage_jpy"] = stats.pop("total_slippage_jpy")
    stats["fx_cost_jpy"] = stats.pop("total_fx_cost_jpy")
    stats["total_cost_jpy"] = (
        stats["fees_jpy"] + stats["slippage_jpy"] + stats["fx_cost_jpy"]
    )
    quantities = trades["quantity"] if not trades.empty else pd.Series(dtype=float)
    rounded = quantities.div(config.OPERATIONAL_QUANTITY_STEP).round()
    return {
        "strategy_id": strategy_id,
        "strategy_version": version,
        "stats": stats,
        "execution_checks": {
            "jpy_reporting": True,
            "commission_included": True,
            "slippage_included": True,
            "fx_conversion_cost_included": True,
            "fractional_quantity_step_applied": bool(
                quantities.empty or (quantities.div(config.OPERATIONAL_QUANTITY_STEP) - rounded).abs().max() < 1e-7
            ),
            "nonnegative_cash": bool((output["equity_curve"]["cash_jpy"] >= -1e-7).all()),
        },
    }


def _combined_portfolio(outputs: dict[str, dict]) -> tuple[dict, pd.DataFrame]:
    curves = {
        name: value["equity_curve"].set_index("date")["equity_jpy"].astype(float)
        for name, value in outputs.items()
    }
    common = next(iter(curves.values())).index
    for curve in curves.values():
        common = common.intersection(curve.index)
    components = pd.DataFrame(index=common)
    for name, curve in curves.items():
        components[name] = curve.loc[common] / curve.loc[common].iloc[0] * config.PORTFOLIO_CAPITAL_JPY
    components["equity_jpy"] = components.sum(axis=1)
    components = components.reset_index(names="date")
    initial = config.PORTFOLIO_CAPITAL_JPY * len(outputs)
    stats = _performance(components["equity_jpy"], components["date"], initial)
    stats.update(
        {
            "fees_jpy": sum(value["stats"]["total_commission_jpy"] for value in outputs.values()),
            "slippage_jpy": sum(value["stats"]["total_slippage_jpy"] for value in outputs.values()),
            "fx_cost_jpy": sum(value["stats"]["total_fx_cost_jpy"] for value in outputs.values()),
        }
    )
    stats["total_cost_jpy"] = stats["fees_jpy"] + stats["slippage_jpy"] + stats["fx_cost_jpy"]
    monthly, _ = period_outputs(components[["date", "equity_jpy"]])
    return {
        "allocation": "equal virtual budgets; no cross-strategy capital transfer",
        "common_period_start": pd.Timestamp(components["date"].iloc[0]).date().isoformat(),
        "common_period_end": pd.Timestamp(components["date"].iloc[-1]).date().isoformat(),
        "initial_capital_jpy": initial,
        "stats": stats,
    }, monthly


def run_operational_replay(save: bool = True) -> dict:
    trend_assets, fx = load_cached_trend_history()
    trend_daily = prepare_jpy_daily(trend_assets, fx)
    factor_assets = load_cached_defensive_factor_history(config.DEFENSIVE_FACTOR_V2["asset_universe"])
    factor_daily = prepare_jpy_daily(
        {
            **factor_assets,
            "SPY": trend_assets["SPY"],
            "QQQ": trend_assets["QQQ"],
        },
        fx,
    )
    parity_signals = risk_parity_signals(
        {symbol: trend_daily[symbol] for symbol in config.RISK_PARITY_V1["asset_universe"]},
        config.RISK_PARITY_V1,
    )
    parity_signals["QQQ_weight"] = 0.0

    trend_signals = monthly_signals(trend_daily)
    factor_signals = defensive_factor_signals(factor_daily, config.DEFENSIVE_FACTOR_V2)
    outputs = {
        "A": _run(trend_daily, trend_signals),
        "B": _run(factor_daily, factor_signals),
        "C": _run(trend_daily, parity_signals),
    }
    strategies = {
        "A": _summary(outputs["A"], config.TREND_STRATEGY_ID, "1"),
        "B": _summary(outputs["B"], config.DEFENSIVE_FACTOR_STRATEGY_ID, "2"),
        "C": _summary(outputs["C"], config.RISK_PARITY_STRATEGY_ID, "1"),
    }
    common_start = max(
        pd.Timestamp(value["equity_curve"]["date"].iloc[0]) for value in outputs.values()
    ).date().isoformat()
    common_outputs = {
        "A": _run(trend_daily, trend_signals, common_start),
        "B": _run(factor_daily, factor_signals, common_start),
        "C": _run(trend_daily, parity_signals, common_start),
    }
    portfolio, monthly = _combined_portfolio(common_outputs)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "profile_version": config.OPERATIONAL_EXECUTION_PROFILE_VERSION,
        "base_currency": "JPY",
        "initial_capital_per_strategy_jpy": config.PORTFOLIO_CAPITAL_JPY,
        "cost_model": {
            "commission": config.COST_MODEL_VERSION,
            "slippage_bps_each_trade": config.SLIPPAGE_BPS,
            "fx_fee_jpy_per_usd_each_conversion": config.OPERATIONAL_FX_FEE_JPY_PER_USD,
            "quantity_step_shares": config.OPERATIONAL_QUANTITY_STEP,
            "cash_drag_included": True,
            "taxes_included": False,
            "tax_note": "Taxes are investor/account-specific and are outside trade-level strategy returns.",
        },
        "strategies": strategies,
        "portfolio": portfolio,
    }
    if save:
        config.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        (config.RESULTS_DIR / "operational_replay.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        monthly.to_csv(config.RESULTS_DIR / "operational_portfolio_monthly.csv", index=False)
    return payload


if __name__ == "__main__":
    run_operational_replay(save=True)
