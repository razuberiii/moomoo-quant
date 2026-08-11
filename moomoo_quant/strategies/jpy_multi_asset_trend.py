import numpy as np
import pandas as pd

from .. import config


def prepare_jpy_daily(
    assets: dict[str, pd.DataFrame], fx: pd.DataFrame
) -> dict[str, pd.DataFrame]:
    """Align FX backward only; execution FX excludes the same day's close."""
    fx_rates = fx[["date", "close"]].copy()
    fx_rates["date"] = pd.to_datetime(fx_rates["date"])
    fx_rates = fx_rates.rename(columns={"date": "fx_date", "close": "usdjpy"}).sort_values("fx_date")

    prepared = {}
    common_dates = None
    for symbol, source in assets.items():
        daily = source.copy()
        daily["date"] = pd.to_datetime(daily["date"])
        daily = daily.sort_values("date")
        daily = pd.merge_asof(
            daily,
            fx_rates,
            left_on="date",
            right_on="fx_date",
            direction="backward",
            allow_exact_matches=True,
        )
        execution_fx = pd.merge_asof(
            daily[["date"]],
            fx_rates.rename(columns={"fx_date": "exec_fx_date", "usdjpy": "exec_usdjpy"}),
            left_on="date",
            right_on="exec_fx_date",
            direction="backward",
            allow_exact_matches=False,
        )
        daily = pd.concat(
            [daily.reset_index(drop=True), execution_fx[["exec_fx_date", "exec_usdjpy"]].reset_index(drop=True)],
            axis=1,
        )
        daily["jpy_close"] = daily["close_price"] * daily["usdjpy"]
        daily["jpy_open"] = daily["open_price"] * daily["exec_usdjpy"]
        daily = daily.dropna(subset=["jpy_close", "jpy_open"]).set_index("date")
        prepared[symbol] = daily
        dates = set(daily.index)
        common_dates = dates if common_dates is None else common_dates & dates

    common_index = pd.DatetimeIndex(sorted(common_dates or []))
    if common_index.empty:
        raise RuntimeError("No common ETF/FX dates available for trend backtest")
    return {symbol: daily.loc[common_index].copy() for symbol, daily in prepared.items()}


def monthly_signals(
    daily: dict[str, pd.DataFrame],
    momentum_months: int = config.TREND_MOMENTUM_MONTHS,
    sma_months: int = config.TREND_SMA_MONTHS,
) -> pd.DataFrame:
    per_asset = []
    for symbol, frame in daily.items():
        month_end = frame.groupby(frame.index.to_period("M")).tail(1).copy()
        current_period = pd.Timestamp.today().to_period("M")
        if not month_end.empty and month_end.index[-1].to_period("M") == current_period:
            # The latest in-progress month is not yet a valid month-end signal.
            month_end = month_end.iloc[:-1]
        month_end["momentum"] = month_end["jpy_close"] / month_end["jpy_close"].shift(momentum_months) - 1
        month_end["sma"] = month_end["jpy_close"].rolling(sma_months, min_periods=sma_months).mean()
        month_end["above_sma"] = month_end["jpy_close"] > month_end["sma"]
        month_end["eligible"] = (month_end["momentum"] > 0) & month_end["above_sma"]
        month_end["asset"] = symbol
        month_end["signal_date"] = month_end.index
        per_asset.append(
            month_end[
                [
                    "signal_date",
                    "asset",
                    "close_price",
                    "jpy_close",
                    "usdjpy",
                    "momentum",
                    "sma",
                    "above_sma",
                    "eligible",
                ]
            ]
        )

    detail = pd.concat(per_asset, ignore_index=True)
    rows = []
    for signal_date, group in detail.groupby("signal_date"):
        ranked = group[group["eligible"]].sort_values(["momentum", "asset"], ascending=[False, True])
        selected = ranked.head(config.TREND_MAX_ASSETS)["asset"].tolist()
        ranks = {asset: rank for rank, asset in enumerate(ranked["asset"], start=1)}
        row = {
            "signal_date": signal_date,
            "selected": ",".join(selected),
            "usdjpy": float(group["usdjpy"].iloc[0]),
        }
        for symbol in daily:
            item = group[group["asset"] == symbol].iloc[0]
            row[f"{symbol}_usd_close"] = item["close_price"]
            row[f"{symbol}_jpy_price"] = item["jpy_close"]
            row[f"{symbol}_momentum"] = item["momentum"]
            row[f"{symbol}_sma"] = item["sma"]
            row[f"{symbol}_above_sma"] = bool(item["above_sma"])
            row[f"{symbol}_eligible"] = bool(item["eligible"])
            row[f"{symbol}_rank"] = ranks.get(symbol, pd.NA)
            row[f"{symbol}_weight"] = config.TREND_ASSET_WEIGHT if symbol in selected else 0.0
        row["JPY_CASH_weight"] = 1.0 - sum(row[f"{symbol}_weight"] for symbol in daily)
        rows.append(row)
    return pd.DataFrame(rows).sort_values("signal_date").reset_index(drop=True)


def static_equal_weight_signals(daily: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Monthly 25% target weights using the same complete-month calendar as v1."""
    base = monthly_signals(daily)
    static = base[["signal_date", "usdjpy"]].copy()
    static["selected"] = ",".join(daily)
    for symbol in daily:
        static[f"{symbol}_weight"] = 1.0 / len(daily)
    static["JPY_CASH_weight"] = 0.0
    return static
