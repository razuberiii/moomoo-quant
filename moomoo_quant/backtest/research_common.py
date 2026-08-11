from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config
from .trend_engine import _performance


def rsi(series: pd.Series, window: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / window, adjust=False).mean()
    loss = -delta.clip(upper=0).ewm(alpha=1 / window, adjust=False).mean()
    relative = gain / loss.replace(0, np.nan)
    output = 100 - 100 / (1 + relative)
    return output.where(loss != 0, 100.0).fillna(50.0)


def drawdown_diagnostics(equity: pd.Series, dates: pd.Series) -> dict:
    values = equity.reset_index(drop=True).astype(float)
    date_values = pd.to_datetime(dates).reset_index(drop=True)
    drawdown = values / values.cummax() - 1
    longest = current = 0
    peak_before_worst = recovery_date = None
    for underwater in drawdown < 0:
        current = current + 1 if underwater else 0
        longest = max(longest, current)
    if not drawdown.empty:
        worst_idx = int(drawdown.idxmin())
        peak_value = values.iloc[: worst_idx + 1].max()
        peak_before_worst = date_values.iloc[: worst_idx + 1][values.iloc[: worst_idx + 1].idxmax()]
        recovered = values.iloc[worst_idx + 1 :] >= peak_value
        if recovered.any():
            recovery_date = date_values.loc[recovered[recovered].index[0]]
    recovery_days = (
        int((recovery_date - peak_before_worst).days)
        if recovery_date is not None and peak_before_worst is not None
        else None
    )
    return {
        "drawdown_duration_sessions": int(longest),
        "recovery_time_days": recovery_days,
        "recovered": recovery_date is not None,
    }


def period_outputs(equity: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = equity[["date", "equity_jpy"]].copy()
    frame["date"] = pd.to_datetime(frame["date"])
    month_end = frame.groupby(frame["date"].dt.to_period("M")).tail(1).copy()
    month_end["return"] = month_end["equity_jpy"].pct_change()
    month_end["month"] = month_end["date"].dt.to_period("M").astype(str)
    monthly = month_end[["month", "date", "equity_jpy", "return"]].reset_index(drop=True)
    monthly["year"] = monthly["date"].dt.year
    yearly = (
        monthly.groupby("year", as_index=False)["return"]
        .apply(lambda values: (1 + values.dropna()).prod() - 1)
        .rename(columns={"return": "net_return"})
    )
    return monthly, yearly


def segment_metrics(equity: pd.DataFrame, start: str | None = None, end: str | None = None) -> dict:
    frame = equity.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    if start:
        frame = frame[frame["date"] >= pd.Timestamp(start)]
    if end:
        frame = frame[frame["date"] <= pd.Timestamp(end)]
    if len(frame) < 2:
        return {}
    return _performance(frame["equity_jpy"], frame["date"])


def monthly_correlation(monthly: pd.DataFrame, filename: str) -> float | None:
    path = config.RESULTS_DIR / filename
    if not path.exists():
        return None
    other = pd.read_csv(path)
    other["month_key"] = pd.to_datetime(other["date"]).dt.to_period("M").astype(str)
    aligned = monthly.merge(
        other[["month_key", "return"]].rename(columns={"return": "other_return"}),
        left_on="month",
        right_on="month_key",
        how="inner",
    ).dropna(subset=["return", "other_return"])
    return float(aligned[["return", "other_return"]].corr().iloc[0, 1]) if len(aligned) > 1 else None


def finish_statistics(
    equity: pd.DataFrame,
    initial_cash: float,
    gross_return: float,
    trades: pd.DataFrame,
    turnover: float,
    fees_jpy: float,
    slippage_jpy: float,
    fx_cost_jpy: float,
    average_holding_days: float,
) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    stats = _performance(equity["equity_jpy"], equity["date"], initial_cash)
    monthly, yearly = period_outputs(equity)
    stats.update(drawdown_diagnostics(equity["equity_jpy"], equity["date"]))
    stats.update(
        {
            "gross_return": float(gross_return),
            "net_return": float(stats["total_return"]),
            "trade_count": int(len(trades)),
            "turnover": float(turnover),
            "average_holding_days": float(average_holding_days),
            "fees_jpy": float(fees_jpy),
            "slippage_jpy": float(slippage_jpy),
            "fx_cost_jpy": float(fx_cost_jpy),
            "total_cost_jpy": float(fees_jpy + slippage_jpy + fx_cost_jpy),
            "worst_month": float(monthly["return"].min()),
            "worst_month_label": str(monthly.loc[monthly["return"].idxmin(), "month"]),
            "positive_year_fraction": float((yearly["net_return"] > 0).mean()),
        }
    )
    return stats, monthly, yearly


def crisis_returns(monthly: pd.DataFrame) -> dict[str, float | None]:
    output = {}
    for year in (2008, 2020, 2022):
        values = monthly[monthly["year"] == year]["return"].dropna()
        output[str(year)] = float((1 + values).prod() - 1) if not values.empty else None
    return output


def yearly_concentration(yearly: pd.DataFrame) -> float:
    positive = yearly.loc[yearly["net_return"] > 0, "net_return"]
    return float(positive.max() / positive.sum()) if not positive.empty and positive.sum() > 0 else 1.0


def write_research_result(prefix: str, result: dict) -> None:
    config.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    result["equity"].to_csv(config.RESULTS_DIR / f"{prefix}_equity.csv", index=False)
    result["trades"].to_csv(config.RESULTS_DIR / f"{prefix}_trades.csv", index=False)
    result["monthly"].to_csv(config.RESULTS_DIR / f"{prefix}_monthly.csv", index=False)
    result["yearly"].to_csv(config.RESULTS_DIR / f"{prefix}_yearly.csv", index=False)
    result["stability"].to_csv(config.RESULTS_DIR / f"{prefix}_stability.csv", index=False)
    (config.RESULTS_DIR / f"{prefix}_research.json").write_text(
        json.dumps(result["summary"], ensure_ascii=False, indent=2), encoding="utf-8"
    )


def immutable_first_result(path: Path, payload: dict) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing.get("parameters_hash") != payload.get("parameters_hash"):
            raise RuntimeError(f"Frozen first result hash mismatch: {path}")
        return
    path.write_text(serialized, encoding="utf-8")
