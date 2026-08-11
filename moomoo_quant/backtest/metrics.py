import math

import numpy as np
import pandas as pd

from .. import config


def max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    dd = equity / peak - 1
    return float(dd.min())


def cagr(equity: pd.Series, dates: pd.Series) -> float:
    if len(equity) < 2:
        return 0.0
    days = (pd.to_datetime(dates.iloc[-1]) - pd.to_datetime(dates.iloc[0])).days
    if days <= 0:
        return 0.0
    return float((equity.iloc[-1] / equity.iloc[0]) ** (365.25 / days) - 1)


def sharpe_ratio(equity: pd.Series) -> float:
    returns = equity.pct_change().dropna()
    if returns.empty or returns.std(ddof=0) == 0:
        return 0.0
    daily_rf = config.RISK_FREE_RATE / 252
    return float(math.sqrt(252) * (returns.mean() - daily_rf) / returns.std(ddof=0))


def trade_stats(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {
            "win_rate": 0.0,
            "payoff_ratio": 0.0,
            "profit_factor": 0.0,
            "total_trades": 0,
            "avg_holding_days": 0.0,
            "max_consecutive_losses": 0,
        }

    pnl = trades["pnl"]
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    loss_abs = losses.abs()

    max_consec = 0
    current = 0
    for value in pnl:
        if value < 0:
            current += 1
            max_consec = max(max_consec, current)
        else:
            current = 0

    return {
        "win_rate": float((pnl > 0).mean()),
        "payoff_ratio": float(wins.mean() / loss_abs.mean()) if not wins.empty and not loss_abs.empty else 0.0,
        "profit_factor": float(wins.sum() / loss_abs.sum()) if loss_abs.sum() > 0 else np.inf,
        "total_trades": int(len(trades)),
        "avg_holding_days": float(trades["holding_days"].mean()),
        "max_consecutive_losses": int(max_consec),
    }


def summarize(equity_curve: pd.DataFrame, trades: pd.DataFrame) -> dict:
    equity = equity_curve["equity"]
    stats = {
        "initial_cash": float(equity.iloc[0]),
        "final_cash": float(equity.iloc[-1]),
        "total_return": float(equity.iloc[-1] / equity.iloc[0] - 1),
        "cagr": cagr(equity, equity_curve["date"]),
        "max_drawdown": max_drawdown(equity),
        "sharpe": sharpe_ratio(equity),
    }
    stats.update(trade_stats(trades))
    return stats

