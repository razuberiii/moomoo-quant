import logging

import matplotlib.pyplot as plt
import pandas as pd

from .. import config
from ..costs import affordable_quantity, commission_usd
from ..strategies.spy_mean_reversion import add_indicators, entry_signal, exit_conditions, exit_signal
from .diagnostics import save_diagnostics
from .metrics import max_drawdown, summarize

logger = logging.getLogger(__name__)


def _buy_price(open_price: float) -> float:
    return open_price * (1 + config.SLIPPAGE_BPS / 10_000)


def _sell_price(open_price: float) -> float:
    return open_price * (1 - config.SLIPPAGE_BPS / 10_000)


def run_backtest(data: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict, dict]:
    df = add_indicators(data).reset_index(drop=True)
    cash = config.INITIAL_CASH_USD
    shares = 0.0
    gross_cash = config.INITIAL_CASH_USD
    gross_shares = 0.0
    gross_entry_cash = 0.0
    gross_entry_price = 0.0
    entry_price = 0.0
    entry_raw_price = 0.0
    entry_date = ""
    entry_cash = 0.0
    entry_commission = 0.0
    entry_slippage = 0.0
    entry_index = 0
    holding_days = 0
    pending_exit_reason = ""
    pending_exit_conditions = {}

    trades = []
    equity_rows = []

    for i in range(len(df)):
        row = df.iloc[i]

        if i > 0:
            signal_row = df.iloc[i - 1]
            exec_open = float(row["open_price"])

            if shares > 0 and pending_exit_reason:
                raw_exit_price = exec_open
                price = _sell_price(raw_exit_price)
                exit_commission = commission_usd(shares * price)
                exit_slippage = shares * (raw_exit_price - price)
                proceeds = shares * price - exit_commission
                cash += proceeds
                pnl = proceeds - entry_cash
                gross_proceeds = gross_shares * raw_exit_price
                gross_cash += gross_proceeds
                gross_pnl = gross_proceeds - gross_entry_cash
                commission_cost = entry_commission + exit_commission
                slippage_cost = entry_slippage + exit_slippage
                trade_slice = df.iloc[entry_index : i + 1]
                max_favorable_price = float(trade_slice["high_price"].max())
                max_adverse_price = float(trade_slice["low_price"].min())
                mfe_pct = max_favorable_price / entry_price - 1
                mae_pct = max_adverse_price / entry_price - 1
                active_conditions = [
                    reason for reason, active in pending_exit_conditions.items() if active
                ]
                trades.append(
                    {
                        "entry_date": entry_date,
                        "exit_date": row["date"],
                        "entry_raw_price": entry_raw_price,
                        "exit_raw_price": raw_exit_price,
                        "entry_price": entry_price,
                        "exit_price": price,
                        "shares": shares,
                        "gross_shares": gross_shares,
                        "pnl": pnl,
                        "gross_pnl": gross_pnl,
                        "return_pct": pnl / entry_cash if entry_cash else 0.0,
                        "gross_return_pct": gross_pnl / gross_entry_cash if gross_entry_cash else 0.0,
                        "commission_cost": commission_cost,
                        "slippage_cost": slippage_cost,
                        "mae_pct": mae_pct,
                        "mfe_pct": mfe_pct,
                        "max_adverse_price": max_adverse_price,
                        "max_favorable_price": max_favorable_price,
                        "holding_days": holding_days,
                        "exit_reason": pending_exit_reason,
                        "exit_conditions": ",".join(active_conditions),
                        "multiple_exit_conditions": len(active_conditions) > 1,
                    }
                )
                shares = 0.0
                gross_shares = 0.0
                gross_entry_cash = 0.0
                gross_entry_price = 0.0
                entry_price = 0.0
                entry_raw_price = 0.0
                entry_date = ""
                entry_cash = 0.0
                entry_commission = 0.0
                entry_slippage = 0.0
                entry_index = 0
                holding_days = 0
                pending_exit_reason = ""
                pending_exit_conditions = {}

            if shares == 0 and entry_signal(signal_row):
                raw_entry_price = exec_open
                price = _buy_price(raw_entry_price)
                budget = cash * config.MAX_POSITION_PCT
                qty = affordable_quantity(budget, price, config.ALLOW_FRACTIONAL_SHARES)
                if qty > 0:
                    entry_commission = commission_usd(qty * price)
                    entry_slippage = qty * (price - raw_entry_price)
                    cost = qty * price + entry_commission
                    cash -= cost
                    shares = qty
                    gross_budget = gross_cash * config.MAX_POSITION_PCT
                    gross_shares = gross_budget / raw_entry_price
                    gross_cash -= gross_budget
                    gross_entry_cash = gross_budget
                    gross_entry_price = raw_entry_price
                    entry_price = price
                    entry_raw_price = raw_entry_price
                    entry_date = row["date"]
                    entry_cash = cost
                    entry_index = i
                    holding_days = 0

        if shares > 0:
            holding_days += 1
            should_exit, reason = exit_signal(row, entry_price, holding_days)
            if should_exit:
                pending_exit_reason = reason
                pending_exit_conditions = exit_conditions(row, entry_price, holding_days)

        position_value = shares * float(row["close_price"])
        equity = cash + position_value
        gross_position_value = gross_shares * float(row["close_price"])
        gross_equity = gross_cash + gross_position_value
        equity_rows.append(
            {
                "date": row["date"],
                "cash": cash,
                "shares": shares,
                "close_price": row["close_price"],
                "position_value": position_value,
                "exposure_pct": position_value / equity if equity else 0.0,
                "equity": equity,
                "gross_cash": gross_cash,
                "gross_shares": gross_shares,
                "gross_position_value": gross_position_value,
                "gross_equity": gross_equity,
            }
        )

    if shares > 0:
        last = df.iloc[-1]
        raw_exit_price = float(last["close_price"])
        price = _sell_price(raw_exit_price)
        exit_commission = commission_usd(shares * price)
        exit_slippage = shares * (raw_exit_price - price)
        proceeds = shares * price - exit_commission
        cash += proceeds
        pnl = proceeds - entry_cash
        gross_proceeds = gross_shares * raw_exit_price
        gross_cash += gross_proceeds
        gross_pnl = gross_proceeds - gross_entry_cash
        trade_slice = df.iloc[entry_index:]
        max_favorable_price = float(trade_slice["high_price"].max())
        max_adverse_price = float(trade_slice["low_price"].min())
        trades.append(
            {
                "entry_date": entry_date,
                "exit_date": last["date"],
                "entry_raw_price": entry_raw_price,
                "exit_raw_price": raw_exit_price,
                "entry_price": entry_price,
                "exit_price": price,
                "shares": shares,
                "gross_shares": gross_shares,
                "pnl": pnl,
                "gross_pnl": gross_pnl,
                "return_pct": pnl / entry_cash if entry_cash else 0.0,
                "gross_return_pct": gross_pnl / gross_entry_cash if gross_entry_cash else 0.0,
                "commission_cost": entry_commission + exit_commission,
                "slippage_cost": entry_slippage + exit_slippage,
                "mae_pct": max_adverse_price / entry_price - 1,
                "mfe_pct": max_favorable_price / entry_price - 1,
                "max_adverse_price": max_adverse_price,
                "max_favorable_price": max_favorable_price,
                "holding_days": holding_days,
                "exit_reason": "FINAL_CLOSE",
                "exit_conditions": "FINAL_CLOSE",
                "multiple_exit_conditions": False,
            }
        )
        equity_rows[-1]["cash"] = cash
        equity_rows[-1]["shares"] = 0.0
        equity_rows[-1]["equity"] = cash
        equity_rows[-1]["gross_cash"] = gross_cash
        equity_rows[-1]["gross_shares"] = 0.0
        equity_rows[-1]["gross_position_value"] = 0.0
        equity_rows[-1]["gross_equity"] = gross_cash

    equity_curve = pd.DataFrame(equity_rows)
    trades_df = pd.DataFrame(trades)
    stats = summarize(equity_curve, trades_df)
    benchmark = buy_and_hold(df)
    save_results(equity_curve, trades_df)
    diagnostics = save_diagnostics(df, equity_curve, trades_df)
    stats["diagnostics"] = diagnostics
    return equity_curve, trades_df, stats, benchmark


def buy_and_hold(df: pd.DataFrame) -> dict:
    first_open = float(df.iloc[0]["open_price"])
    last_close = float(df.iloc[-1]["close_price"])
    entry_price = _buy_price(first_open)
    shares = affordable_quantity(config.INITIAL_CASH_USD, entry_price, True)
    final_equity = shares * _sell_price(last_close)
    final_equity -= commission_usd(final_equity)
    equity = config.INITIAL_CASH_USD * df["close_price"] / float(df.iloc[0]["close_price"])
    return {
        "final_cash": float(final_equity),
        "total_return": float(final_equity / config.INITIAL_CASH_USD - 1),
        "max_drawdown": max_drawdown(equity),
    }


def save_results(equity_curve: pd.DataFrame, trades: pd.DataFrame) -> None:
    config.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    equity_curve.to_csv(config.RESULTS_DIR / "equity_curve.csv", index=False)
    trades.to_csv(config.RESULTS_DIR / "trades.csv", index=False)

    plt.figure(figsize=(10, 5))
    plt.plot(pd.to_datetime(equity_curve["date"]), equity_curve["equity"])
    plt.title("SPY Mean Reversion Equity Curve")
    plt.xlabel("Date")
    plt.ylabel("Equity USD")
    plt.tight_layout()
    plt.savefig(config.RESULTS_DIR / "equity_curve.png", dpi=150)
    plt.close()

    equity = equity_curve["equity"]
    drawdown = equity / equity.cummax() - 1
    plt.figure(figsize=(10, 5))
    plt.plot(pd.to_datetime(equity_curve["date"]), drawdown)
    plt.title("Drawdown")
    plt.xlabel("Date")
    plt.ylabel("Drawdown")
    plt.tight_layout()
    plt.savefig(config.RESULTS_DIR / "drawdown.png", dpi=150)
    plt.close()
