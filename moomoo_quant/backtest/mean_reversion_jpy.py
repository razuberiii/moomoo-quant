from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .. import config
from ..costs import commission_usd
from ..strategies.spy_mean_reversion import add_indicators, entry_signal, exit_conditions, exit_signal
from ..trend_data import load_cached_trend_history
from .trend_engine import _performance


def _prepare(spy: pd.DataFrame, fx: pd.DataFrame) -> pd.DataFrame:
    market = spy.copy().sort_values("date")
    market["date"] = pd.to_datetime(market["date"])
    rates = fx[["date", "close"]].copy().sort_values("date")
    rates["date"] = pd.to_datetime(rates["date"])
    rates = rates.rename(columns={"date": "fx_date", "close": "usdjpy"})
    market = pd.merge_asof(
        market, rates, left_on="date", right_on="fx_date",
        direction="backward", allow_exact_matches=True,
    )
    execution_fx = pd.merge_asof(
        market[["date"]],
        rates.rename(columns={"fx_date": "exec_fx_date", "usdjpy": "exec_usdjpy"}),
        left_on="date", right_on="exec_fx_date",
        direction="backward", allow_exact_matches=False,
    )
    market = pd.concat(
        [market.reset_index(drop=True), execution_fx[["exec_fx_date", "exec_usdjpy"]]], axis=1
    )
    market = market.dropna(subset=["usdjpy", "exec_usdjpy"]).reset_index(drop=True)
    return add_indicators(market)


def _affordable_jpy(cash_jpy: float, raw_price_usd: float, fx_rate: float) -> float:
    execution_price = raw_price_usd * (1 + config.SLIPPAGE_BPS / 10_000)
    low, high = 0.0, cash_jpy / (execution_price * fx_rate)
    for _ in range(60):
        quantity = (low + high) / 2
        notional_usd = quantity * execution_price
        raw_jpy = quantity * raw_price_usd * fx_rate
        total = (
            notional_usd * fx_rate
            + commission_usd(notional_usd) * fx_rate
            + raw_jpy * config.MR_FX_CONVERSION_COST_BPS / 10_000
        )
        if total <= cash_jpy:
            low = quantity
        else:
            high = quantity
    return low


def _drawdown_duration(equity: pd.Series) -> int:
    below_peak = equity < equity.cummax()
    groups = (~below_peak).cumsum()
    return int(below_peak.groupby(groups).sum().max()) if len(equity) else 0


def run_mean_reversion_jpy() -> dict:
    assets, fx = load_cached_trend_history()
    df = _prepare(assets["SPY"], fx)
    cash = config.MR_INITIAL_CASH_JPY
    shares = 0.0
    gross_cash = config.MR_INITIAL_CASH_JPY
    gross_shares = 0.0
    entry: dict = {}
    pending_reason = ""
    pending_conditions: dict[str, bool] = {}
    holding_days = 0
    trades: list[dict] = []
    equity_rows: list[dict] = []

    for i, row in df.iterrows():
        if i > 0:
            signal_row = df.iloc[i - 1]
            raw_open = float(row["open_price"])
            exec_fx = float(row["exec_usdjpy"])

            if shares > 0 and pending_reason:
                execution_price = raw_open * (1 - config.SLIPPAGE_BPS / 10_000)
                notional_usd = shares * execution_price
                raw_notional_jpy = shares * raw_open * exec_fx
                commission_jpy = commission_usd(notional_usd) * exec_fx
                slippage_jpy = shares * (raw_open - execution_price) * exec_fx
                fx_cost_jpy = raw_notional_jpy * config.MR_FX_CONVERSION_COST_BPS / 10_000
                proceeds_jpy = notional_usd * exec_fx - commission_jpy - fx_cost_jpy
                cash += proceeds_jpy
                gross_proceeds_jpy = gross_shares * raw_open * exec_fx
                gross_cash += gross_proceeds_jpy
                trade_slice = df.iloc[entry["index"] : i + 1]
                gross_return = gross_proceeds_jpy / entry["gross_cost_jpy"] - 1
                net_return = proceeds_jpy / entry["net_cost_jpy"] - 1
                active = [name for name, enabled in pending_conditions.items() if enabled]
                usd_return = raw_open / entry["raw_price_usd"] - 1
                trades.append(
                    {
                        "entry_date": entry["date"],
                        "exit_date": pd.Timestamp(row["date"]).date().isoformat(),
                        "holding_days": holding_days,
                        "exit_reason": pending_reason,
                        "exit_conditions": ",".join(active),
                        "multiple_exit_conditions": len(active) > 1,
                        "quantity": shares,
                        "entry_raw_price_usd": entry["raw_price_usd"],
                        "exit_raw_price_usd": raw_open,
                        "entry_usdjpy": entry["fx"],
                        "exit_usdjpy": exec_fx,
                        "gross_pnl_jpy": gross_proceeds_jpy - entry["gross_cost_jpy"],
                        "net_pnl_jpy": proceeds_jpy - entry["net_cost_jpy"],
                        "gross_return": gross_return,
                        "net_return": net_return,
                        "usd_asset_return": usd_return,
                        "fx_contribution": gross_return - usd_return,
                        "commission_jpy": entry["commission_jpy"] + commission_jpy,
                        "slippage_jpy": entry["slippage_jpy"] + slippage_jpy,
                        "fx_cost_jpy": entry["fx_cost_jpy"] + fx_cost_jpy,
                        "mfe": float(trade_slice["high_price"].max()) / entry["execution_price_usd"] - 1,
                        "mae": float(trade_slice["low_price"].min()) / entry["execution_price_usd"] - 1,
                    }
                )
                shares = gross_shares = 0.0
                entry = {}
                holding_days = 0
                pending_reason = ""
                pending_conditions = {}

            if shares == 0 and entry_signal(signal_row):
                budget = cash * config.MR_MAX_POSITION_PCT
                quantity = _affordable_jpy(budget, raw_open, exec_fx)
                if quantity > 1e-10:
                    execution_price = raw_open * (1 + config.SLIPPAGE_BPS / 10_000)
                    notional_usd = quantity * execution_price
                    raw_notional_jpy = quantity * raw_open * exec_fx
                    commission_jpy = commission_usd(notional_usd) * exec_fx
                    slippage_jpy = quantity * (execution_price - raw_open) * exec_fx
                    fx_cost_jpy = raw_notional_jpy * config.MR_FX_CONVERSION_COST_BPS / 10_000
                    net_cost_jpy = notional_usd * exec_fx + commission_jpy + fx_cost_jpy
                    cash -= net_cost_jpy
                    shares = quantity

                    gross_budget = gross_cash * config.MR_MAX_POSITION_PCT
                    gross_shares = gross_budget / (raw_open * exec_fx)
                    gross_cash -= gross_budget
                    entry = {
                        "date": pd.Timestamp(row["date"]).date().isoformat(),
                        "index": i,
                        "raw_price_usd": raw_open,
                        "execution_price_usd": execution_price,
                        "fx": exec_fx,
                        "net_cost_jpy": net_cost_jpy,
                        "gross_cost_jpy": gross_budget,
                        "commission_jpy": commission_jpy,
                        "slippage_jpy": slippage_jpy,
                        "fx_cost_jpy": fx_cost_jpy,
                    }
                    holding_days = 0

        if shares > 0:
            holding_days += 1
            should_exit, reason = exit_signal(row, entry["execution_price_usd"], holding_days)
            if should_exit:
                pending_reason = reason
                pending_conditions = exit_conditions(row, entry["execution_price_usd"], holding_days)

        close_jpy = float(row["close_price"] * row["usdjpy"])
        equity_rows.append(
            {
                "date": pd.Timestamp(row["date"]),
                "cash_jpy": cash,
                "quantity": shares,
                "position_value_jpy": shares * close_jpy,
                "equity_jpy": cash + shares * close_jpy,
                "gross_equity_jpy": gross_cash + gross_shares * close_jpy,
                "market_weight": (shares * close_jpy) / (cash + shares * close_jpy),
                "usdjpy": float(row["usdjpy"]),
            }
        )

    equity = pd.DataFrame(equity_rows)
    trades_df = pd.DataFrame(trades)
    stats = _performance(equity["equity_jpy"], equity["date"], config.MR_INITIAL_CASH_JPY)
    gross_stats = _performance(equity["gross_equity_jpy"], equity["date"], config.MR_INITIAL_CASH_JPY)
    stats.update(
        {
            "trade_count": len(trades_df),
            "average_holding_days": float(trades_df["holding_days"].mean()),
            "turnover": float(
                ((trades_df["quantity"] * trades_df["entry_raw_price_usd"] * trades_df["entry_usdjpy"]).sum() * 2)
                / equity["equity_jpy"].mean()
            ),
            "gross_return": gross_stats["total_return"],
            "fees_jpy": float(trades_df["commission_jpy"].sum()),
            "slippage_jpy": float(trades_df["slippage_jpy"].sum()),
            "fx_cost_jpy": float(trades_df["fx_cost_jpy"].sum()),
            "net_return": stats["total_return"],
            "net_expectancy": float(trades_df["net_return"].mean()),
            "win_rate": float((trades_df["net_pnl_jpy"] > 0).mean()),
            "drawdown_duration_days": _drawdown_duration(equity["equity_jpy"]),
            "time_in_market": float((equity["market_weight"] > 1e-8).mean()),
        }
    )

    equity["month"] = equity["date"].dt.to_period("M")
    month_end = equity.groupby("month").tail(1).copy()
    month_end["return"] = month_end["equity_jpy"].pct_change()
    monthly = month_end[["date", "return"]].copy()
    monthly["month"] = monthly["date"].dt.to_period("M").astype(str)
    monthly = monthly[["month", "return"]]
    monthly["year"] = monthly["month"].str[:4].astype(int)
    yearly = monthly.groupby("year", as_index=False)["return"].apply(
        lambda values: (1 + values.dropna()).prod() - 1
    ).rename(columns={"return": "net_return"})
    stats["worst_month"] = float(monthly["return"].min())
    stats["worst_year"] = int(yearly.loc[yearly["net_return"].idxmin(), "year"])
    stats["worst_year_return"] = float(yearly["net_return"].min())

    oos_equity = equity[equity["date"] >= pd.Timestamp(config.MR_OOS_START)]
    oos_stats = _performance(oos_equity["equity_jpy"], oos_equity["date"])
    trend_monthly = pd.read_csv(config.RESULTS_DIR / "trend_monthly_returns.csv")
    trend_monthly["month_key"] = pd.to_datetime(trend_monthly["date"]).dt.to_period("M").astype(str)
    aligned = monthly.merge(
        trend_monthly[["month_key", "return"]].rename(columns={"return": "trend_return"}),
        left_on="month", right_on="month_key", how="inner",
    )
    correlation = float(aligned[["return", "trend_return"]].corr().iloc[0, 1])
    positive_years = int((yearly["net_return"] > 0).sum())
    positive_sum = yearly.loc[yearly["net_return"] > 0, "net_return"].sum()
    concentration = float(yearly["net_return"].max() / positive_sum) if positive_sum > 0 else 1.0
    gates = {
        "data_alignment": bool((df["fx_date"] <= df["date"]).all() and (df["exec_fx_date"] < df["date"]).all()),
        "positive_net_expectancy": stats["net_expectancy"] > config.MR_MIN_NET_EXPECTANCY,
        "positive_oos_sharpe": oos_stats["sharpe"] > config.MR_MIN_OOS_SHARPE,
        "edge_correlation": abs(correlation) < config.MR_MAX_ABS_TREND_CORRELATION,
        "not_one_year_dependent": positive_years >= 3 and concentration <= 0.75,
    }
    status = "SHADOW_ELIGIBLE" if all(gates.values()) else "RESEARCH_REJECTED"
    crisis = {}
    for year in (2008, 2020, 2022):
        values = monthly[monthly["year"] == year]["return"].dropna()
        crisis[str(year)] = float((1 + values).prod() - 1) if not values.empty else None

    params = {
        "sma_window": config.SMA_WINDOW,
        "rsi_window": config.RSI_WINDOW,
        "entry_rsi": config.ENTRY_RSI,
        "exit_rsi": config.EXIT_RSI,
        "max_holding_days": config.MAX_HOLDING_DAYS,
        "stop_loss_pct": config.STOP_LOSS_PCT,
        "max_position_pct": config.MR_MAX_POSITION_PCT,
    }
    params_hash = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
    run_id = f"mean-reversion-v1-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{params_hash[:8]}"
    summary = {
        "run_id": run_id,
        "strategy_id": config.MEAN_REVERSION_STRATEGY_ID,
        "strategy_version": "1",
        "status": status,
        "parameters": params,
        "parameters_hash": params_hash,
        "data_start": equity["date"].iloc[0].date().isoformat(),
        "data_end": equity["date"].iloc[-1].date().isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_currency": "JPY",
        "signal_currency": "USD",
        "stats": stats,
        "oos_stats": oos_stats,
        "monthly_correlation_with_trend_v1": correlation,
        "crisis_returns": crisis,
        "year_concentration": concentration,
        "acceptance_gates": gates,
    }
    config.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    equity.drop(columns="month").to_csv(config.RESULTS_DIR / "mean_reversion_jpy_equity.csv", index=False)
    trades_df.to_csv(config.RESULTS_DIR / "mean_reversion_jpy_trades.csv", index=False)
    monthly.to_csv(config.RESULTS_DIR / "mean_reversion_jpy_monthly.csv", index=False)
    yearly.to_csv(config.RESULTS_DIR / "mean_reversion_jpy_yearly.csv", index=False)
    (config.RESULTS_DIR / "mean_reversion_jpy_research.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"summary": summary, "equity": equity, "trades": trades_df, "monthly": monthly, "yearly": yearly}
