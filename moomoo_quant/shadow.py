import json
import logging
import math
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from filelock import FileLock, Timeout

from . import config
from .costs import commission_usd

logger = logging.getLogger(__name__)

STATE_FILE = "state.json"
SIGNALS_FILE = "signals.csv"
ORDERS_FILE = "orders.csv"
TRADES_FILE = "trades.csv"
EQUITY_FILE = "equity.csv"
LOCK_FILE = "daily.lock"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def _atomic_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temp, index=False)
    os.replace(temp, path)


def _read_csv(name: str, columns: list[str]) -> pd.DataFrame:
    path = config.SHADOW_DIR / name
    if not path.exists():
        return pd.DataFrame(columns=columns)
    return pd.read_csv(path)


def _weights(signal: pd.Series) -> dict[str, float]:
    return {
        symbol: float(signal[f"{symbol}_weight"])
        for symbol in ("SPY", "QQQ", "GLD", "IEF")
    }


def _affordable_quantity(cash_jpy: float, price_usd: float, fx: float) -> float:
    low, high = 0.0, max(cash_jpy / (price_usd * fx), 0.0)
    fx_rate = config.FX_CONVERSION_COST_BPS / 10_000
    for _ in range(60):
        quantity = (low + high) / 2
        notional_usd = quantity * price_usd
        total = notional_usd * fx * (1 + fx_rate) + commission_usd(notional_usd) * fx
        if total <= cash_jpy:
            low = quantity
        else:
            high = quantity
    return low if config.ALLOW_FRACTIONAL_SHARES else float(math.floor(low))


def _initial_state(daily: dict[str, pd.DataFrame], signals: pd.DataFrame) -> dict:
    latest_market = next(iter(daily.values())).index[-1]
    latest_signal = signals.iloc[-1]
    state = {
        "strategy_version": config.SHADOW_STRATEGY_VERSION,
        "strategy_parameters": {
            "assets": ["SPY", "QQQ", "GLD", "IEF"],
            "momentum_months": config.TREND_MOMENTUM_MONTHS,
            "sma_months": config.TREND_SMA_MONTHS,
            "top_n": config.TREND_MAX_ASSETS,
            "asset_weight": config.TREND_ASSET_WEIGHT,
            "signal_timing": "month-end close",
            "execution_timing": "next trading-day open",
        },
        "created_at": _now(),
        "initial_cash_jpy": config.SHADOW_INITIAL_CASH_JPY,
        "cash_jpy": config.SHADOW_INITIAL_CASH_JPY,
        "positions": {symbol: 0.0 for symbol in ("SPY", "QQQ", "GLD", "IEF")},
        "target_weights": _weights(latest_signal),
        "pending_signal": None,
        "last_signal_date": pd.Timestamp(latest_signal["signal_date"]).date().isoformat(),
        "last_market_date": latest_market.date().isoformat(),
        "current_equity_jpy": config.SHADOW_INITIAL_CASH_JPY,
        "peak_equity_jpy": config.SHADOW_INITIAL_CASH_JPY,
        "status": "NO_ACTION",
    }
    _atomic_json(config.SHADOW_DIR / STATE_FILE, state)
    signal_row = pd.DataFrame(
        [
            {
                "signal_id": f"signal-{state['last_signal_date']}",
                "signal_date": state["last_signal_date"],
                "recorded_at": state["created_at"],
                "selected": latest_signal["selected"],
                "target_weights": json.dumps(state["target_weights"], sort_keys=True),
                "status": "BASELINE_NOT_EXECUTED",
            }
        ]
    )
    _atomic_csv(config.SHADOW_DIR / SIGNALS_FILE, signal_row)
    equity = pd.DataFrame(
        [
            {
                "date": state["last_market_date"],
                "recorded_at": state["created_at"],
                "equity_jpy": state["current_equity_jpy"],
                "cash_jpy": state["cash_jpy"],
                "drawdown": 0.0,
                **{f"{symbol}_weight": 0.0 for symbol in state["positions"]},
                "JPY_CASH_weight": 1.0,
            }
        ]
    )
    _atomic_csv(config.SHADOW_DIR / EQUITY_FILE, equity)
    _atomic_csv(config.SHADOW_DIR / ORDERS_FILE, pd.DataFrame(columns=_order_columns()))
    _atomic_csv(config.SHADOW_DIR / TRADES_FILE, pd.DataFrame(columns=_trade_columns()))
    logger.info("Initialized shadow account at JPY %.2f", config.SHADOW_INITIAL_CASH_JPY)
    return state


def _order_columns() -> list[str]:
    return [
        "order_id", "rebalance_id", "signal_date", "execution_date", "asset", "side",
        "quantity", "status", "created_at",
    ]


def _trade_columns() -> list[str]:
    return [
        "trade_id", "rebalance_id", "signal_date", "execution_date", "asset", "side",
        "quantity", "execution_price_usd", "usdjpy", "notional_jpy", "commission_jpy",
        "slippage_jpy", "fx_cost_jpy", "post_cash_jpy", "post_positions",
    ]


def _execute_rebalance(
    state: dict,
    pending: dict,
    execution_date: pd.Timestamp,
    daily: dict[str, pd.DataFrame],
    existing_trades: pd.DataFrame,
) -> tuple[list[dict], list[dict]]:
    rebalance_id = f"rebalance-{pending['signal_date']}-{execution_date.date().isoformat()}"
    already = existing_trades[existing_trades["rebalance_id"] == rebalance_id]
    if not already.empty:
        recovered = already.iloc[-1]
        state["cash_jpy"] = float(recovered["post_cash_jpy"])
        state["positions"] = json.loads(recovered["post_positions"])
        logger.warning("Recovered shadow state from existing rebalance %s", rebalance_id)
        return [], []

    symbols = list(state["positions"])
    positions = {symbol: float(state["positions"][symbol]) for symbol in symbols}
    cash = float(state["cash_jpy"])
    current_values = {
        symbol: positions[symbol] * float(daily[symbol].at[execution_date, "jpy_open"])
        for symbol in symbols
    }
    pre_trade_equity = cash + sum(current_values.values())
    targets = pending["target_weights"]
    deltas = {
        symbol: pre_trade_equity * float(targets[symbol]) - current_values[symbol]
        for symbol in symbols
    }
    order_rows: list[dict] = []
    trade_rows: list[dict] = []

    for side in ("SELL", "BUY"):
        for symbol in symbols:
            delta = deltas[symbol]
            if (side == "SELL" and delta >= -1e-8) or (side == "BUY" and delta <= 1e-8):
                continue
            row = daily[symbol].loc[execution_date]
            raw_open = float(row["open_price"])
            fx = float(row["exec_usdjpy"])
            slip = config.SLIPPAGE_BPS / 10_000
            execution_price = raw_open * (1 - slip if side == "SELL" else 1 + slip)
            desired = abs(delta) / (raw_open * fx)
            quantity = min(desired, positions[symbol]) if side == "SELL" else min(
                desired, _affordable_quantity(cash, execution_price, fx)
            )
            if quantity <= 1e-10:
                continue
            notional_usd = quantity * execution_price
            raw_notional_jpy = quantity * raw_open * fx
            commission_jpy = commission_usd(notional_usd) * fx
            slippage_jpy = quantity * abs(execution_price - raw_open) * fx
            fx_cost_jpy = raw_notional_jpy * config.FX_CONVERSION_COST_BPS / 10_000
            cash_flow = notional_usd * fx
            if side == "SELL":
                positions[symbol] -= quantity
                cash += cash_flow - commission_jpy - fx_cost_jpy
            else:
                positions[symbol] += quantity
                cash -= cash_flow + commission_jpy + fx_cost_jpy
            order_id = f"{rebalance_id}-{symbol}-{side}"
            order_rows.append(
                {
                    "order_id": order_id,
                    "rebalance_id": rebalance_id,
                    "signal_date": pending["signal_date"],
                    "execution_date": execution_date.date().isoformat(),
                    "asset": symbol,
                    "side": side,
                    "quantity": quantity,
                    "status": "THEORETICAL_FILLED",
                    "created_at": _now(),
                }
            )
            trade_rows.append(
                {
                    "trade_id": order_id.replace("rebalance", "trade", 1),
                    "rebalance_id": rebalance_id,
                    "signal_date": pending["signal_date"],
                    "execution_date": execution_date.date().isoformat(),
                    "asset": symbol,
                    "side": side,
                    "quantity": quantity,
                    "execution_price_usd": execution_price,
                    "usdjpy": fx,
                    "notional_jpy": raw_notional_jpy,
                    "commission_jpy": commission_jpy,
                    "slippage_jpy": slippage_jpy,
                    "fx_cost_jpy": fx_cost_jpy,
                }
            )

    state["cash_jpy"] = cash
    state["positions"] = positions
    snapshot = json.dumps(positions, sort_keys=True)
    for trade in trade_rows:
        trade["post_cash_jpy"] = cash
        trade["post_positions"] = snapshot
    return order_rows, trade_rows


def update_shadow(daily: dict[str, pd.DataFrame], signals: pd.DataFrame) -> dict:
    config.SHADOW_DIR.mkdir(parents=True, exist_ok=True)
    state_path = config.SHADOW_DIR / STATE_FILE
    if not state_path.exists():
        return _initial_state(daily, signals)

    with state_path.open(encoding="utf-8") as handle:
        state = json.load(handle)
    signal_log = _read_csv(SIGNALS_FILE, ["signal_id", "signal_date", "recorded_at", "selected", "target_weights", "status"])
    order_log = _read_csv(ORDERS_FILE, _order_columns())
    trade_log = _read_csv(TRADES_FILE, _trade_columns())
    equity_log = _read_csv(EQUITY_FILE, ["date"])

    calendar = next(iter(daily.values())).index
    signal_map = {pd.Timestamp(row["signal_date"]): row for _, row in signals.iterrows()}
    new_dates = calendar[calendar > pd.Timestamp(state["last_market_date"])]
    if len(new_dates) == 0:
        logger.info("NO ACTION: no new common market date")
        return state

    new_signals: list[dict] = []
    new_orders: list[dict] = []
    new_trades: list[dict] = []
    new_equity: list[dict] = []

    for market_date in new_dates:
        pending = state.get("pending_signal")
        if pending and not pending.get("execution_date"):
            next_index = calendar.searchsorted(pd.Timestamp(pending["signal_date"]), side="right")
            if next_index < len(calendar):
                pending["execution_date"] = calendar[next_index].date().isoformat()
        if pending and pending.get("execution_date") == market_date.date().isoformat():
            known_trades = pd.concat([trade_log, pd.DataFrame(new_trades)], ignore_index=True)
            orders, trades = _execute_rebalance(state, pending, market_date, daily, known_trades)
            new_orders.extend(orders)
            new_trades.extend(trades)
            state["pending_signal"] = None
            state["status"] = "NO_ACTION"

        if market_date in signal_map and market_date.date().isoformat() > state["last_signal_date"]:
            signal = signal_map[market_date]
            target_weights = _weights(signal)
            next_index = calendar.searchsorted(market_date, side="right")
            execution_date = calendar[next_index].date().isoformat() if next_index < len(calendar) else None
            pending = {
                "signal_date": market_date.date().isoformat(),
                "execution_date": execution_date,
                "target_weights": target_weights,
                "selected": signal["selected"],
            }
            state["pending_signal"] = pending
            state["target_weights"] = target_weights
            state["last_signal_date"] = pending["signal_date"]
            state["status"] = "PENDING_REBALANCE"
            new_signals.append(
                {
                    "signal_id": f"signal-{pending['signal_date']}",
                    "signal_date": pending["signal_date"],
                    "recorded_at": _now(),
                    "selected": pending["selected"],
                    "target_weights": json.dumps(target_weights, sort_keys=True),
                    "status": "PENDING_REBALANCE",
                }
            )

        values = {
            symbol: float(state["positions"][symbol]) * float(daily[symbol].at[market_date, "jpy_close"])
            for symbol in state["positions"]
        }
        equity = float(state["cash_jpy"]) + sum(values.values())
        state["current_equity_jpy"] = equity
        state["peak_equity_jpy"] = max(float(state["peak_equity_jpy"]), equity)
        state["last_market_date"] = market_date.date().isoformat()
        new_equity.append(
            {
                "date": state["last_market_date"],
                "recorded_at": _now(),
                "equity_jpy": equity,
                "cash_jpy": state["cash_jpy"],
                "drawdown": equity / state["peak_equity_jpy"] - 1,
                **{
                    f"{symbol}_weight": values[symbol] / equity if equity else 0.0
                    for symbol in values
                },
                "JPY_CASH_weight": float(state["cash_jpy"]) / equity if equity else 0.0,
            }
        )

    if new_signals:
        signal_log = pd.concat([signal_log, pd.DataFrame(new_signals)], ignore_index=True).drop_duplicates("signal_id")
    if new_orders:
        order_log = pd.concat([order_log, pd.DataFrame(new_orders)], ignore_index=True).drop_duplicates("order_id")
    if new_trades:
        trade_log = pd.concat([trade_log, pd.DataFrame(new_trades)], ignore_index=True).drop_duplicates("trade_id")
    if new_equity:
        equity_log = pd.concat([equity_log, pd.DataFrame(new_equity)], ignore_index=True).drop_duplicates("date", keep="last")

    _atomic_csv(config.SHADOW_DIR / SIGNALS_FILE, signal_log)
    _atomic_csv(config.SHADOW_DIR / ORDERS_FILE, order_log)
    _atomic_csv(config.SHADOW_DIR / TRADES_FILE, trade_log)
    _atomic_csv(config.SHADOW_DIR / EQUITY_FILE, equity_log)
    _atomic_json(state_path, state)
    logger.info("Shadow update complete through %s: %s", state["last_market_date"], state["status"])
    return state


def run_locked_shadow_update(daily: dict[str, pd.DataFrame], signals: pd.DataFrame) -> dict:
    config.SHADOW_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(config.SHADOW_DIR / LOCK_FILE, timeout=0):
            return update_shadow(daily, signals)
    except Timeout as exc:
        raise RuntimeError("Another daily process is already running") from exc
