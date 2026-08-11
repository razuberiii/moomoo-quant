import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .. import config
from .metrics import max_drawdown


def _streak(values: pd.Series, positive: bool) -> int:
    best = 0
    current = 0
    for value in values:
        hit = value > 0 if positive else value < 0
        if hit:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return int(best)


def gross_net_summary(trades: pd.DataFrame, equity_curve: pd.DataFrame) -> dict:
    commission = float(trades["commission_cost"].sum()) if not trades.empty else 0.0
    slippage = float(trades["slippage_cost"].sum()) if not trades.empty else 0.0
    gross_equity_col = "gross_equity" if "gross_equity" in equity_curve.columns else "equity"
    gross_return = float(equity_curve[gross_equity_col].iloc[-1] / config.INITIAL_CASH_USD - 1)
    net_return = float(equity_curve["equity"].iloc[-1] / config.INITIAL_CASH_USD - 1)
    total_trades = len(trades)
    return {
        "gross_return": gross_return,
        "commission_total": commission,
        "slippage_total": slippage,
        "net_return": net_return,
        "avg_commission_per_trade": commission / total_trades if total_trades else 0.0,
        "avg_slippage_per_trade": slippage / total_trades if total_trades else 0.0,
    }


def single_trade_summary(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {}
    returns = trades["return_pct"]
    wins = returns[returns > 0]
    losses = returns[returns < 0]
    avg_win = float(wins.mean()) if not wins.empty else 0.0
    avg_loss = float(losses.mean()) if not losses.empty else 0.0
    win_rate = float((returns > 0).mean())
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss
    gross_wins = float(trades.loc[trades["pnl"] > 0, "pnl"].sum())
    gross_losses = float(trades.loc[trades["pnl"] < 0, "pnl"].abs().sum())
    return {
        "avg_trade_return": float(returns.mean()),
        "median_trade_return": float(returns.median()),
        "avg_winning_trade_return": avg_win,
        "avg_losing_trade_return": avg_loss,
        "max_trade_gain": float(returns.max()),
        "max_trade_loss": float(returns.min()),
        "expectancy": expectancy,
        "profit_factor": gross_wins / gross_losses if gross_losses > 0 else np.inf,
        "win_rate": win_rate,
        "max_consecutive_wins": _streak(trades["pnl"], positive=True),
        "max_consecutive_losses": _streak(trades["pnl"], positive=False),
    }


def exit_reason_summary(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    rows = []
    for reason in ("RSI_EXIT", "TIME_EXIT", "STOP_LOSS", "FINAL_CLOSE"):
        subset = trades[trades["exit_reason"] == reason]
        if subset.empty:
            rows.append({"exit_reason": reason, "trades": 0, "win_rate": 0.0, "avg_return": 0.0})
        else:
            rows.append(
                {
                    "exit_reason": reason,
                    "trades": int(len(subset)),
                    "win_rate": float((subset["pnl"] > 0).mean()),
                    "avg_return": float(subset["return_pct"].mean()),
                }
            )
    return pd.DataFrame(rows)


def yearly_performance(trades: pd.DataFrame, equity_curve: pd.DataFrame) -> pd.DataFrame:
    eq = equity_curve.copy()
    eq["year"] = pd.to_datetime(eq["date"]).dt.year
    out = []
    for year, year_eq in eq.groupby("year"):
        year_trades = trades[pd.to_datetime(trades["exit_date"]).dt.year == year] if not trades.empty else trades
        start_equity = float(year_eq["equity"].iloc[0])
        end_equity = float(year_eq["equity"].iloc[-1])
        gross_start = float(year_eq["gross_equity"].iloc[0]) if "gross_equity" in year_eq else start_equity
        gross_end = float(year_eq["gross_equity"].iloc[-1]) if "gross_equity" in year_eq else end_equity
        out.append(
            {
                "year": int(year),
                "trades": int(len(year_trades)),
                "win_rate": float((year_trades["pnl"] > 0).mean()) if not year_trades.empty else 0.0,
                "gross_return": gross_end / gross_start - 1 if gross_start else 0.0,
                "net_return": end_equity / start_equity - 1 if start_equity else 0.0,
                "average_trade": float(year_trades["return_pct"].mean()) if not year_trades.empty else 0.0,
                "max_drawdown": max_drawdown(year_eq["equity"]),
            }
        )
    return pd.DataFrame(out)


def exposure_summary(equity_curve: pd.DataFrame, net_return: float) -> dict:
    total_days = len(equity_curve)
    held = equity_curve[equity_curve["shares"] > 0]
    held_days = len(held)
    time_in_market = held_days / total_days if total_days else 0.0
    avg_exposure = float(equity_curve["exposure_pct"].mean()) if "exposure_pct" in equity_curve else 0.0
    max_exposure = float(equity_curve["exposure_pct"].max()) if "exposure_pct" in equity_curve else 0.0
    return {
        "total_backtest_days": int(total_days),
        "holding_days": int(held_days),
        "time_in_market_pct": time_in_market,
        "avg_position_pct": avg_exposure,
        "max_position_pct": max_exposure,
        "return_per_exposure": net_return / time_in_market if time_in_market else 0.0,
    }


def mae_mfe_summary(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {}
    winners = trades[trades["pnl"] > 0]
    losers = trades[trades["pnl"] < 0]
    return {
        "winning_avg_mfe": float(winners["mfe_pct"].mean()) if not winners.empty else 0.0,
        "winning_avg_mae": float(winners["mae_pct"].mean()) if not winners.empty else 0.0,
        "losing_avg_mfe": float(losers["mfe_pct"].mean()) if not losers.empty else 0.0,
        "losing_avg_mae": float(losers["mae_pct"].mean()) if not losers.empty else 0.0,
    }


def monthly_returns(equity_curve: pd.DataFrame) -> pd.DataFrame:
    eq = equity_curve.copy()
    eq["month"] = pd.to_datetime(eq["date"]).dt.to_period("M").astype(str)
    monthly = eq.groupby("month").agg(start_equity=("equity", "first"), end_equity=("equity", "last"))
    monthly["return"] = monthly["end_equity"] / monthly["start_equity"] - 1
    return monthly.reset_index()


def save_diagnostics(data: pd.DataFrame, equity_curve: pd.DataFrame, trades: pd.DataFrame) -> dict:
    config.RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    gross_net = gross_net_summary(trades, equity_curve)
    single = single_trade_summary(trades)
    exits = exit_reason_summary(trades)
    yearly = yearly_performance(trades, equity_curve)
    exposure = exposure_summary(equity_curve, gross_net["net_return"])
    mae_mfe = mae_mfe_summary(trades)
    monthly = monthly_returns(equity_curve)

    trade_distribution = trades[
        [
            "entry_date",
            "exit_date",
            "exit_reason",
            "return_pct",
            "gross_return_pct",
            "pnl",
            "gross_pnl",
            "commission_cost",
            "slippage_cost",
            "mae_pct",
            "mfe_pct",
        ]
    ].copy()
    trade_distribution.to_csv(config.RESULTS_DIR / "trade_return_distribution.csv", index=False)
    yearly.to_csv(config.RESULTS_DIR / "yearly_performance.csv", index=False)
    trades[
        [
            "entry_date",
            "exit_date",
            "exit_reason",
            "return_pct",
            "mae_pct",
            "mfe_pct",
            "max_adverse_price",
            "max_favorable_price",
        ]
    ].to_csv(config.RESULTS_DIR / "mae_mfe.csv", index=False)
    monthly.to_csv(config.RESULTS_DIR / "monthly_returns.csv", index=False)
    exits.to_csv(config.RESULTS_DIR / "exit_reason_summary.csv", index=False)

    plot_trades_on_spy(data, trades)

    return {
        "gross_net": gross_net,
        "single_trade": single,
        "exit_reasons": exits,
        "yearly": yearly,
        "exposure": exposure,
        "mae_mfe": mae_mfe,
    }


def plot_trades_on_spy(data: pd.DataFrame, trades: pd.DataFrame) -> None:
    df = data.copy()
    df["date"] = pd.to_datetime(df["date"])
    plt.figure(figsize=(12, 6))
    plt.plot(df["date"], df["close_price"], label="SPY Close", linewidth=1.2)

    if not trades.empty:
        price_by_date = df.set_index("date")["close_price"]
        entries = pd.DataFrame({"date": pd.to_datetime(trades["entry_date"])})
        entries["close_price"] = entries["date"].map(price_by_date)
        exits = pd.DataFrame({"date": pd.to_datetime(trades["exit_date"])})
        exits["close_price"] = exits["date"].map(price_by_date)
        plt.scatter(entries["date"], entries["close_price"], marker="^", color="green", s=35, label="Buy")
        plt.scatter(exits["date"], exits["close_price"], marker="v", color="red", s=35, label="Sell")

    plt.title("SPY Close With Strategy Trades")
    plt.xlabel("Date")
    plt.ylabel("SPY Close")
    plt.legend()
    plt.tight_layout()
    plt.savefig(config.RESULTS_DIR / "trades_on_spy.png", dpi=150)
    plt.close()
