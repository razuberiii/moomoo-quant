import logging
import math
import json

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .. import config
from ..costs import commission_usd
from ..strategies.jpy_multi_asset_trend import monthly_signals, static_equal_weight_signals
from .metrics import max_drawdown, sharpe_ratio

logger = logging.getLogger(__name__)


def _max_affordable_jpy(
    cash: float,
    price_usd: float,
    fx: float,
    fx_cost_bps: float = config.FX_CONVERSION_COST_BPS,
    commission_enabled: bool = True,
    fx_fee_jpy_per_usd: float = 0.0,
) -> float:
    if cash <= 0 or price_usd <= 0 or fx <= 0:
        return 0.0
    low, high = 0.0, cash / (price_usd * fx)
    fx_rate = fx_cost_bps / 10_000
    for _ in range(60):
        qty = (low + high) / 2
        notional_usd = qty * price_usd
        commission = commission_usd(notional_usd) if commission_enabled else 0.0
        total = (
            notional_usd * fx
            + commission * fx
            + notional_usd * fx * fx_rate
            + notional_usd * fx_fee_jpy_per_usd
        )
        if total <= cash:
            low = qty
        else:
            high = qty
    return low if config.ALLOW_FRACTIONAL_SHARES else float(math.floor(low))


def _floor_quantity(quantity: float, quantity_step: float | None) -> float:
    if quantity_step is None:
        return quantity
    if quantity_step <= 0:
        raise ValueError("quantity_step must be positive")
    return math.floor((quantity + 1e-12) / quantity_step) * quantity_step


def _performance(
    equity: pd.Series, dates: pd.Series, initial_value: float | None = None
) -> dict:
    values = equity.reset_index(drop=True).astype(float)
    date_values = pd.to_datetime(dates).reset_index(drop=True)
    base = float(initial_value) if initial_value is not None else float(values.iloc[0])
    returns = values.pct_change()
    returns.iloc[0] = values.iloc[0] / base - 1
    returns = returns.dropna()
    downside = returns[returns < 0]
    sortino = 0.0
    if not downside.empty and downside.std(ddof=0) > 0:
        sortino = float(np.sqrt(252) * returns.mean() / downside.std(ddof=0))
    days = (date_values.iloc[-1] - date_values.iloc[0]).days
    growth = float((values.iloc[-1] / base) ** (365.25 / days) - 1) if days > 0 else 0.0
    drawdown = max_drawdown(pd.concat([pd.Series([base]), values], ignore_index=True))
    yearly = returns.groupby(date_values.dt.year).apply(lambda x: (1 + x).prod() - 1)
    return {
        "total_return": float(values.iloc[-1] / base - 1),
        "cagr": growth,
        "max_drawdown": drawdown,
        "sharpe": sharpe_ratio(pd.concat([pd.Series([base]), values], ignore_index=True)),
        "sortino": sortino,
        "calmar": growth / abs(drawdown) if drawdown < 0 else 0.0,
        "volatility": float(returns.std(ddof=0) * np.sqrt(252)) if not returns.empty else 0.0,
        "best_year": int(yearly.idxmax()) if not yearly.empty else 0,
        "best_year_return": float(yearly.max()) if not yearly.empty else 0.0,
        "worst_year": int(yearly.idxmin()) if not yearly.empty else 0,
        "worst_year_return": float(yearly.min()) if not yearly.empty else 0.0,
        "positive_year_pct": float((yearly > 0).mean()) if not yearly.empty else 0.0,
    }


def _execution_schedule(signals: pd.DataFrame, calendar: pd.DatetimeIndex) -> dict[pd.Timestamp, pd.Series]:
    schedule = {}
    for _, signal in signals.iterrows():
        idx = calendar.searchsorted(pd.Timestamp(signal["signal_date"]), side="right")
        if idx < len(calendar):
            schedule[calendar[idx]] = signal
    return schedule


def run_trend_backtest(
    daily: dict[str, pd.DataFrame],
    momentum_months: int = config.TREND_MOMENTUM_MONTHS,
    sma_months: int = config.TREND_SMA_MONTHS,
    start: str | None = None,
    end: str | None = None,
    save: bool = False,
    signals_override: pd.DataFrame | None = None,
    initial_cash_jpy: float = config.TREND_INITIAL_CASH_JPY,
    slippage_bps: float = config.SLIPPAGE_BPS,
    fx_cost_bps: float = config.FX_CONVERSION_COST_BPS,
    commission_enabled: bool = True,
    quantity_step: float | None = None,
    fx_fee_jpy_per_usd: float = 0.0,
) -> dict:
    symbols = list(daily)
    full_calendar = next(iter(daily.values())).index
    signals = (
        signals_override.copy()
        if signals_override is not None
        else monthly_signals(daily, momentum_months, sma_months)
    )
    schedule = _execution_schedule(signals, full_calendar)
    calendar = full_calendar
    if start:
        calendar = calendar[calendar >= pd.Timestamp(start)]
    if end:
        calendar = calendar[calendar <= pd.Timestamp(end)]
    if calendar.empty:
        raise RuntimeError("No dates in requested trend backtest period")

    cash = initial_cash_jpy
    positions = {symbol: 0.0 for symbol in symbols}
    target_weights = {symbol: 0.0 for symbol in symbols}
    transactions = []
    rebalance_rows = []
    equity_rows = []

    for dt in calendar:
        if dt in schedule:
            signal = schedule[dt]
            previous_weights = target_weights.copy()
            target_weights = {symbol: float(signal[f"{symbol}_weight"]) for symbol in symbols}
            raw_open_values = {
                symbol: positions[symbol] * float(daily[symbol].at[dt, "jpy_open"])
                for symbol in symbols
            }
            pre_trade_equity = cash + sum(raw_open_values.values())
            deltas = {
                symbol: pre_trade_equity * target_weights[symbol] - raw_open_values[symbol]
                for symbol in symbols
            }
            trade_start = len(transactions)

            for side in ("SELL", "BUY"):
                for symbol in symbols:
                    delta = deltas[symbol]
                    if (side == "SELL" and delta >= -1e-8) or (side == "BUY" and delta <= 1e-8):
                        continue
                    row = daily[symbol].loc[dt]
                    raw_open = float(row["open_price"])
                    fx = float(row["exec_usdjpy"])
                    slip = slippage_bps / 10_000
                    exec_price = raw_open * (1 - slip if side == "SELL" else 1 + slip)
                    desired_qty = abs(delta) / (raw_open * fx)
                    if side == "SELL":
                        qty = min(desired_qty, positions[symbol])
                    else:
                        qty = min(
                            desired_qty,
                            _max_affordable_jpy(
                                cash,
                                exec_price,
                                fx,
                                fx_cost_bps,
                                commission_enabled,
                                fx_fee_jpy_per_usd,
                            ),
                        )
                    if not config.ALLOW_FRACTIONAL_SHARES:
                        qty = float(math.floor(qty))
                    qty = _floor_quantity(qty, quantity_step)
                    if qty <= 1e-10:
                        continue

                    notional_usd = qty * exec_price
                    raw_notional_jpy = qty * raw_open * fx
                    commission = commission_usd(notional_usd) if commission_enabled else 0.0
                    commission_jpy = commission * fx
                    slippage_jpy = qty * abs(exec_price - raw_open) * fx
                    fx_cost_jpy = (
                        raw_notional_jpy * fx_cost_bps / 10_000
                        + notional_usd * fx_fee_jpy_per_usd
                    )
                    cash_flow = notional_usd * fx
                    if side == "SELL":
                        positions[symbol] -= qty
                        cash += cash_flow - commission_jpy - fx_cost_jpy
                    else:
                        positions[symbol] += qty
                        cash -= cash_flow + commission_jpy + fx_cost_jpy
                    transactions.append(
                        {
                            "signal_date": pd.Timestamp(signal["signal_date"]).date().isoformat(),
                            "trade_date": dt.date().isoformat(),
                            "asset": symbol,
                            "side": side,
                            "quantity": qty,
                            "raw_open_usd": raw_open,
                            "execution_price_usd": exec_price,
                            "usdjpy": fx,
                            "notional_jpy": raw_notional_jpy,
                            "commission_usd": commission,
                            "commission_jpy": commission_jpy,
                            "slippage_jpy": slippage_jpy,
                            "fx_cost_jpy": fx_cost_jpy,
                            "pre_trade_equity_jpy": pre_trade_equity,
                        }
                    )

            event_trades = transactions[trade_start:]
            sold = sorted({trade["asset"] for trade in event_trades if trade["side"] == "SELL"})
            bought = sorted({trade["asset"] for trade in event_trades if trade["side"] == "BUY"})
            total_notional = sum(trade["notional_jpy"] for trade in event_trades)
            rebalance_rows.append(
                {
                    "signal_date": pd.Timestamp(signal["signal_date"]).date().isoformat(),
                    "execution_date": dt.date().isoformat(),
                    "previous_allocation": json.dumps(previous_weights, sort_keys=True),
                    "new_allocation": json.dumps(target_weights, sort_keys=True),
                    "sold": ",".join(sold),
                    "bought": ",".join(bought),
                    "turnover": total_notional / pre_trade_equity if pre_trade_equity else 0.0,
                    "commission_jpy": sum(trade["commission_jpy"] for trade in event_trades),
                    "slippage_jpy": sum(trade["slippage_jpy"] for trade in event_trades),
                    "fx_cost_jpy": sum(trade["fx_cost_jpy"] for trade in event_trades),
                }
            )

        position_values = {
            symbol: positions[symbol] * float(daily[symbol].at[dt, "jpy_close"])
            for symbol in symbols
        }
        equity = cash + sum(position_values.values())
        equity_row = {"date": dt, "cash_jpy": cash, "equity_jpy": equity}
        for symbol in symbols:
            equity_row[f"{symbol}_value_jpy"] = position_values[symbol]
            equity_row[f"{symbol}_weight"] = position_values[symbol] / equity if equity else 0.0
        equity_row["market_weight"] = sum(position_values.values()) / equity if equity else 0.0
        equity_rows.append(equity_row)

    equity_curve = pd.DataFrame(equity_rows)
    trades = pd.DataFrame(transactions)
    rebalances = pd.DataFrame(rebalance_rows)
    stats = _performance(
        equity_curve["equity_jpy"], equity_curve["date"], initial_cash_jpy
    )
    stats["total_return_jpy"] = stats["total_return"]
    stats.update(
        {
            "start_date": equity_curve["date"].iloc[0].date().isoformat(),
            "end_date": equity_curve["date"].iloc[-1].date().isoformat(),
            "time_in_market": float((equity_curve["market_weight"] > 1e-8).mean()),
            "number_of_trades": int(len(trades)),
            "number_of_rebalances": int(trades["trade_date"].nunique()) if not trades.empty else 0,
            "turnover": float(trades["notional_jpy"].sum() / equity_curve["equity_jpy"].mean()) if not trades.empty else 0.0,
            "total_commission_jpy": float(trades["commission_jpy"].sum()) if not trades.empty else 0.0,
            "total_commission_usd": float(trades["commission_usd"].sum()) if not trades.empty else 0.0,
            "total_slippage_jpy": float(trades["slippage_jpy"].sum()) if not trades.empty else 0.0,
            "total_fx_cost_jpy": float(trades["fx_cost_jpy"].sum()) if not trades.empty else 0.0,
            "quantity_step": quantity_step,
            "fx_fee_jpy_per_usd": fx_fee_jpy_per_usd,
        }
    )
    benchmarks = _benchmarks(daily, equity_curve["date"])
    benchmark_stats = {
        name: _performance(benchmarks[name], benchmarks["date"])
        for name in ("SPY_USD", "SPY_JPY", "QQQ_JPY")
    }
    yearly = _yearly_comparison(equity_curve, benchmarks, daily)

    result = {
        "equity_curve": equity_curve,
        "trades": trades,
        "rebalances": rebalances,
        "signals": signals,
        "stats": stats,
        "benchmarks": benchmarks,
        "benchmark_stats": benchmark_stats,
        "yearly": yearly,
    }
    if save:
        save_trend_results(result, daily)
    return result


def run_static_equal_weight_backtest(
    daily: dict[str, pd.DataFrame], save: bool = False
) -> dict:
    signals = static_equal_weight_signals(daily)
    result = run_trend_backtest(daily, signals_override=signals)
    if save:
        result["equity_curve"].to_csv(config.RESULTS_DIR / "static_equal_weight_equity.csv", index=False)
        result["trades"].to_csv(config.RESULTS_DIR / "static_equal_weight_trades.csv", index=False)
        result["rebalances"].to_csv(config.RESULTS_DIR / "static_equal_weight_rebalances.csv", index=False)
    return result


def _benchmarks(daily: dict[str, pd.DataFrame], dates: pd.Series) -> pd.DataFrame:
    idx = pd.DatetimeIndex(dates)
    frame = pd.DataFrame(index=idx)
    frame["SPY_USD"] = daily["SPY"].loc[idx, "close_price"]
    frame["SPY_JPY"] = daily["SPY"].loc[idx, "jpy_close"]
    frame["QQQ_JPY"] = daily["QQQ"].loc[idx, "jpy_close"]
    frame["USDJPY"] = daily["SPY"].loc[idx, "usdjpy"]
    normalized = frame / frame.iloc[0] * config.TREND_INITIAL_CASH_JPY
    normalized.index.name = "date"
    return normalized.reset_index()


def _yearly_comparison(
    equity_curve: pd.DataFrame, benchmarks: pd.DataFrame, daily: dict[str, pd.DataFrame]
) -> pd.DataFrame:
    combined = benchmarks.copy().set_index("date")
    combined["Strategy_JPY"] = equity_curve.set_index("date")["equity_jpy"]
    returns = combined.pct_change().fillna(0.0)
    yearly = returns.groupby(returns.index.year).apply(lambda x: (1 + x).prod() - 1)
    return yearly[["SPY_JPY", "QQQ_JPY", "Strategy_JPY", "USDJPY"]].reset_index(names="year")


def save_trend_results(result: dict, daily: dict[str, pd.DataFrame]) -> None:
    config.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    equity = result["equity_curve"]
    trades = result["trades"]
    rebalances = result["rebalances"]
    signals = result["signals"]
    benchmarks = result["benchmarks"]
    result["yearly"].to_csv(config.RESULTS_DIR / "trend_yearly_performance.csv", index=False)
    equity.to_csv(config.RESULTS_DIR / "trend_equity_curve.csv", index=False)
    trades.to_csv(config.RESULTS_DIR / "trend_trades.csv", index=False)
    rebalances.to_csv(config.RESULTS_DIR / "trend_rebalance_history.csv", index=False)
    signals.to_csv(config.RESULTS_DIR / "trend_allocations.csv", index=False)

    chart = benchmarks.set_index("date")[["SPY_JPY", "QQQ_JPY"]] / config.TREND_INITIAL_CASH_JPY * 100
    chart["Strategy_JPY"] = equity.set_index("date")["equity_jpy"] / config.TREND_INITIAL_CASH_JPY * 100
    chart[["Strategy_JPY", "SPY_JPY", "QQQ_JPY"]].plot(figsize=(11, 6))
    plt.title("JPY Equity Curves (Start = 100)")
    plt.ylabel("Growth of 100")
    plt.tight_layout()
    plt.savefig(config.RESULTS_DIR / "trend_equity_curve.png", dpi=150)
    plt.close()

    strategy = equity.set_index("date")["equity_jpy"]
    drawdown = strategy / strategy.cummax() - 1
    drawdown.plot(figsize=(11, 5), color="firebrick")
    plt.title("JPY Multi-Asset Trend Drawdown")
    plt.ylabel("Drawdown")
    plt.tight_layout()
    plt.savefig(config.RESULTS_DIR / "trend_drawdown.png", dpi=150)
    plt.close()

    weights = signals.set_index("signal_date")[[f"{s}_weight" for s in daily] + ["JPY_CASH_weight"]]
    weights.columns = list(daily) + ["JPY CASH"]
    weights.plot.area(figsize=(12, 6), ylim=(0, 1), linewidth=0)
    plt.title("Monthly Target Allocations")
    plt.ylabel("Weight")
    plt.tight_layout()
    plt.savefig(config.RESULTS_DIR / "trend_allocations.png", dpi=150)
    plt.close()

    selection = (weights.drop(columns="JPY CASH") > 0).astype(int).T
    plt.figure(figsize=(13, 3.5))
    plt.imshow(selection, aspect="auto", interpolation="nearest", cmap="Greens", vmin=0, vmax=1)
    plt.yticks(range(len(selection.index)), selection.index)
    tick_count = min(12, len(selection.columns))
    tick_positions = np.linspace(0, len(selection.columns) - 1, tick_count, dtype=int)
    plt.xticks(tick_positions, [pd.Timestamp(selection.columns[i]).strftime("%Y-%m") for i in tick_positions], rotation=45)
    plt.title("Monthly Asset Selection")
    plt.tight_layout()
    plt.savefig(config.RESULTS_DIR / "trend_asset_selection.png", dpi=150)
    plt.close()
