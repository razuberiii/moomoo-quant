import pandas as pd

from .. import config
from ..indicators import rsi, sma


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["sma200"] = sma(out["close_price"], config.SMA_WINDOW)
    out["rsi5"] = rsi(out["close_price"], config.RSI_WINDOW)
    return out


def entry_signal(row: pd.Series) -> bool:
    if pd.isna(row["sma200"]) or pd.isna(row["rsi5"]):
        return False
    return row["close_price"] > row["sma200"] and row["rsi5"] < config.ENTRY_RSI


def exit_signal(row: pd.Series, entry_price: float, holding_days: int) -> tuple[bool, str]:
    conditions = exit_conditions(row, entry_price, holding_days)
    for reason in ("RSI_EXIT", "TIME_EXIT", "STOP_LOSS"):
        if conditions[reason]:
            return True, reason
    return False, ""


def exit_conditions(row: pd.Series, entry_price: float, holding_days: int) -> dict[str, bool]:
    if pd.isna(row["rsi5"]):
        return {"RSI_EXIT": False, "TIME_EXIT": False, "STOP_LOSS": False}
    return {
        "RSI_EXIT": bool(row["rsi5"] > config.EXIT_RSI),
        "TIME_EXIT": bool(holding_days >= config.MAX_HOLDING_DAYS),
        "STOP_LOSS": bool(row["close_price"] <= entry_price * (1 - config.STOP_LOSS_PCT)),
    }
