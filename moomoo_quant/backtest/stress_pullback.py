from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd

from .. import config
from ..costs import commission_usd
from ..trend_data import load_cached_trend_history
from .research_common import (
    crisis_returns,
    finish_statistics,
    immutable_first_result,
    monthly_correlation,
    rsi,
    segment_metrics,
    write_research_result,
    yearly_concentration,
)


def _prepare(spy: pd.DataFrame, fx: pd.DataFrame, params: dict) -> pd.DataFrame:
    market = spy.copy().sort_values("date")
    market["date"] = pd.to_datetime(market["date"])
    rates = fx[["date", "close"]].copy().sort_values("date")
    rates["date"] = pd.to_datetime(rates["date"])
    rates = rates.rename(columns={"date": "fx_date", "close": "usdjpy"})
    market = pd.merge_asof(
        market, rates, left_on="date", right_on="fx_date", direction="backward"
    )
    prior_rates = rates.rename(columns={"fx_date": "exec_fx_date", "usdjpy": "exec_usdjpy"})
    execution_fx = pd.merge_asof(
        market[["date"]],
        prior_rates,
        left_on="date",
        right_on="exec_fx_date",
        direction="backward",
        allow_exact_matches=False,
    )
    market = pd.concat(
        [market.reset_index(drop=True), execution_fx[["exec_fx_date", "exec_usdjpy"]]], axis=1
    ).dropna(subset=["usdjpy", "exec_usdjpy"])
    market["stress_return"] = market["close_price"].pct_change(params["stress_lookback_days"])
    market["rsi"] = rsi(market["close_price"], params["rsi_window"])
    market["long_sma"] = market["close_price"].rolling(params["long_term_sma_days"]).mean()
    return market.reset_index(drop=True)


def _affordable(cash_jpy: float, raw_open: float, fx: float, params: dict) -> float:
    slip = params["slippage_bps"] / 10_000
    execution_price = raw_open * (1 + slip)
    low, high = 0.0, cash_jpy / (execution_price * fx)
    for _ in range(60):
        quantity = (low + high) / 2
        notional_usd = quantity * execution_price
        raw_notional_jpy = quantity * raw_open * fx
        total = (
            notional_usd * fx
            + commission_usd(notional_usd) * fx
            + raw_notional_jpy * params["fx_conversion_cost_bps"] / 10_000
        )
        if total <= cash_jpy:
            low = quantity
        else:
            high = quantity
    return low


def _run_case(data: pd.DataFrame, params: dict) -> dict:
    initial_cash = params["initial_cash_jpy"]
    cash = gross_cash = initial_cash
    shares = gross_shares = 0.0
    entry: dict = {}
    pending: dict | None = None
    cooldown = 0
    holding_days = 0
    trades: list[dict] = []
    equity_rows: list[dict] = []

    for i, row in data.iterrows():
        if i > 0 and pending:
            raw_open = float(row["open_price"])
            fx = float(row["exec_usdjpy"])
            signal_date = pd.Timestamp(data.iloc[i - 1]["date"]).date().isoformat()
            if pending["action"] == "SELL" and shares > 0:
                execution_price = raw_open * (1 - params["slippage_bps"] / 10_000)
                notional_usd = shares * execution_price
                raw_notional_jpy = shares * raw_open * fx
                commission_jpy = commission_usd(notional_usd) * fx
                slippage_jpy = shares * (raw_open - execution_price) * fx
                fx_cost_jpy = raw_notional_jpy * params["fx_conversion_cost_bps"] / 10_000
                proceeds = notional_usd * fx - commission_jpy - fx_cost_jpy
                gross_proceeds = gross_shares * raw_open * fx
                cash += proceeds
                gross_cash += gross_proceeds
                trade_slice = data.iloc[entry["index"] : i + 1]
                gross_pnl = gross_proceeds - entry["gross_cost_jpy"]
                net_pnl = proceeds - entry["net_cost_jpy"]
                trades.append(
                    {
                        "entry_date": entry["date"],
                        "exit_date": pd.Timestamp(row["date"]).date().isoformat(),
                        "entry_signal_date": entry["signal_date"],
                        "exit_signal_date": signal_date,
                        "exit_reason": pending["reason"],
                        "exit_conditions": ",".join(pending["conditions"]),
                        "holding_days": holding_days,
                        "quantity": shares,
                        "entry_notional_jpy": entry["raw_notional_jpy"],
                        "exit_notional_jpy": raw_notional_jpy,
                        "gross_pnl_jpy": gross_pnl,
                        "net_pnl_jpy": net_pnl,
                        "gross_return": gross_proceeds / entry["gross_cost_jpy"] - 1,
                        "net_return": proceeds / entry["net_cost_jpy"] - 1,
                        "usd_asset_return": raw_open / entry["raw_open"] - 1,
                        "fx_contribution": (raw_open * fx) / (entry["raw_open"] * entry["fx"]) - 1
                        - (raw_open / entry["raw_open"] - 1),
                        "commission_jpy": entry["commission_jpy"] + commission_jpy,
                        "slippage_jpy": entry["slippage_jpy"] + slippage_jpy,
                        "fx_cost_jpy": entry["fx_cost_jpy"] + fx_cost_jpy,
                        "mfe": float(trade_slice["high_price"].max()) / entry["execution_price"] - 1,
                        "mae": float(trade_slice["low_price"].min()) / entry["execution_price"] - 1,
                    }
                )
                shares = gross_shares = 0.0
                entry = {}
                holding_days = 0
                cooldown = params["reentry_cooldown_days"]
            elif pending["action"] == "BUY" and shares == 0:
                budget = cash * params["position_size"]
                quantity = _affordable(budget, raw_open, fx, params)
                if quantity > 1e-10:
                    execution_price = raw_open * (1 + params["slippage_bps"] / 10_000)
                    notional_usd = quantity * execution_price
                    raw_notional_jpy = quantity * raw_open * fx
                    commission_jpy = commission_usd(notional_usd) * fx
                    slippage_jpy = quantity * (execution_price - raw_open) * fx
                    fx_cost_jpy = raw_notional_jpy * params["fx_conversion_cost_bps"] / 10_000
                    net_cost = notional_usd * fx + commission_jpy + fx_cost_jpy
                    cash -= net_cost
                    shares = quantity
                    gross_budget = gross_cash * params["position_size"]
                    gross_shares = gross_budget / (raw_open * fx)
                    gross_cash -= gross_budget
                    entry = {
                        "date": pd.Timestamp(row["date"]).date().isoformat(),
                        "signal_date": signal_date,
                        "index": i,
                        "raw_open": raw_open,
                        "execution_price": execution_price,
                        "fx": fx,
                        "net_cost_jpy": net_cost,
                        "gross_cost_jpy": gross_budget,
                        "commission_jpy": commission_jpy,
                        "slippage_jpy": slippage_jpy,
                        "fx_cost_jpy": fx_cost_jpy,
                        "raw_notional_jpy": raw_notional_jpy,
                    }
                    holding_days = 0
            pending = None

        if shares > 0:
            holding_days += 1
            stop = float(row["close_price"]) / entry["execution_price"] - 1 <= params["stop_loss"]
            time_exit = holding_days >= params["maximum_holding_days"]
            bounce = float(row["rsi"]) >= params["bounce_exit_rsi"]
            conditions = [name for name, met in (("STOP_LOSS", stop), ("TIME_EXIT", time_exit), ("RSI_EXIT", bounce)) if met]
            if conditions:
                pending = {"action": "SELL", "reason": conditions[0], "conditions": conditions}
        elif cooldown > 0:
            cooldown -= 1
        elif pd.notna(row["long_sma"]):
            entry_signal = (
                float(row["stress_return"]) <= params["stress_return_threshold"]
                and float(row["rsi"]) < params["entry_rsi_threshold"]
                and float(row["close_price"]) > float(row["long_sma"])
            )
            if entry_signal:
                pending = {"action": "BUY", "reason": "STRESS_PULLBACK", "conditions": []}

        close_jpy = float(row["close_price"] * row["usdjpy"])
        equity_rows.append(
            {
                "date": pd.Timestamp(row["date"]),
                "cash_jpy": cash,
                "quantity": shares,
                "position_value_jpy": shares * close_jpy,
                "equity_jpy": cash + shares * close_jpy,
                "gross_equity_jpy": gross_cash + gross_shares * close_jpy,
                "market_weight": shares * close_jpy / (cash + shares * close_jpy),
                "usdjpy": float(row["usdjpy"]),
            }
        )

    equity = pd.DataFrame(equity_rows)
    trades_frame = pd.DataFrame(trades)
    total_notional = (
        float((trades_frame["entry_notional_jpy"] + trades_frame["exit_notional_jpy"]).sum())
        if not trades_frame.empty
        else 0.0
    )
    gross_return = float(equity["gross_equity_jpy"].iloc[-1] / initial_cash - 1)
    return {
        "equity": equity,
        "trades": trades_frame,
        "gross_return": gross_return,
        "turnover": total_notional / float(equity["equity_jpy"].mean()),
    }


def _formal_result(data: pd.DataFrame, params: dict) -> dict:
    case = _run_case(data, params)
    trades = case["trades"]
    fees = float(trades["commission_jpy"].sum()) if not trades.empty else 0.0
    slippage = float(trades["slippage_jpy"].sum()) if not trades.empty else 0.0
    fx_cost = float(trades["fx_cost_jpy"].sum()) if not trades.empty else 0.0
    avg_hold = float(trades["holding_days"].mean()) if not trades.empty else 0.0
    stats, monthly, yearly = finish_statistics(
        case["equity"], params["initial_cash_jpy"], case["gross_return"], trades,
        case["turnover"], fees, slippage, fx_cost, avg_hold,
    )
    stats.update(
        {
            "net_expectancy": float(trades["net_return"].mean()) if not trades.empty else 0.0,
            "win_rate": float((trades["net_pnl_jpy"] > 0).mean()) if not trades.empty else 0.0,
            "usd_return_contribution": float(trades["usd_asset_return"].mean()) if not trades.empty else 0.0,
            "fx_return_contribution": float(trades["fx_contribution"].mean()) if not trades.empty else 0.0,
        }
    )
    return {**case, "stats": stats, "monthly": monthly, "yearly": yearly}


def run_stress_pullback_v2() -> dict:
    params = json.loads(json.dumps(config.STRESS_PULLBACK_V2))
    assets, fx = load_cached_trend_history()
    data = _prepare(assets["SPY"], fx, params)
    formal = _formal_result(data, params)
    stats, monthly, yearly = formal["stats"], formal["monthly"], formal["yearly"]
    trend_corr = monthly_correlation(monthly, "trend_monthly_returns.csv")
    oos = segment_metrics(formal["equity"], start=params["oos_start"])
    insample = segment_metrics(formal["equity"], end="2021-12-31")
    concentration = yearly_concentration(yearly)
    gates = {
        "data_alignment": bool((data["fx_date"] <= data["date"]).all() and (data["exec_fx_date"] < data["date"]).all()),
        "low_frequency_trade_count": params["acceptance"]["minimum_trades"] <= len(formal["trades"]) <= params["acceptance"]["maximum_trades"],
        "positive_net_expectancy": stats["net_expectancy"] > params["acceptance"]["minimum_net_expectancy"],
        "positive_oos_sharpe": bool(oos and oos["sharpe"] > params["acceptance"]["minimum_oos_sharpe"]),
        "edge_correlation": trend_corr is not None and abs(trend_corr) < params["acceptance"]["maximum_abs_trend_correlation"],
        "not_one_year_dependent": int((yearly["net_return"] > 0).sum()) >= params["acceptance"]["minimum_positive_years"] and concentration <= params["acceptance"]["maximum_positive_year_concentration"],
    }
    status = "SHADOW" if all(gates.values()) else "RESEARCH_REJECTED"

    nearby = [
        ("stress_return", "stress_return_threshold", -0.045),
        ("stress_return", "stress_return_threshold", -0.055),
        ("entry_rsi", "entry_rsi_threshold", 12.0),
        ("entry_rsi", "entry_rsi_threshold", 18.0),
        ("max_holding", "maximum_holding_days", 4),
        ("max_holding", "maximum_holding_days", 6),
    ]
    stability_rows = []
    for dimension, key, value in nearby:
        varied = json.loads(json.dumps(params))
        varied[key] = value
        varied_data = _prepare(assets["SPY"], fx, varied)
        output = _formal_result(varied_data, varied)
        stability_rows.append(
            {"dimension": dimension, "parameter": key, "value": value, "net_return": output["stats"]["net_return"], "cagr": output["stats"]["cagr"], "sharpe": output["stats"]["sharpe"], "trades": len(output["trades"]), "selection_allowed": False}
        )
    stability = pd.DataFrame(stability_rows)
    generated = datetime.now(timezone.utc)
    run_id = f"stress-pullback-v2-{generated.strftime('%Y%m%dT%H%M%SZ')}-{config.STRESS_PULLBACK_V2_HASH[:8]}"
    summary = {
        "run_id": run_id,
        "strategy_id": config.STRESS_PULLBACK_STRATEGY_ID,
        "strategy_name": "Stress Pullback Mean Reversion",
        "strategy_version": "2",
        "parameters": params,
        "parameters_hash": config.STRESS_PULLBACK_V2_HASH,
        "status": status,
        "budget_jpy": 100_000.0 if status == "SHADOW" else 0.0,
        "data_start": formal["equity"]["date"].iloc[0].date().isoformat(),
        "data_end": formal["equity"]["date"].iloc[-1].date().isoformat(),
        "generated_at": generated.isoformat(),
        "base_currency": "JPY",
        "signal_currency": "USD",
        "stats": stats,
        "in_sample_metrics": insample,
        "oos_metrics": oos,
        "crisis_returns": crisis_returns(monthly),
        "monthly_correlation_with_trend_v1": trend_corr,
        "year_concentration": concentration,
        "acceptance_gates": gates,
        "rejection_reasons": [name for name, passed in gates.items() if not passed],
        "stability_is_diagnostic_only": True,
    }
    result = {"summary": summary, **formal, "stability": stability}
    write_research_result("stress_pullback_v2", result)
    immutable_first_result(config.RESULTS_DIR / "stress_pullback_v2_first_result.json", summary)
    return result
