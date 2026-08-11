import logging
from datetime import date, timedelta

import pandas as pd

from . import config
from .moomoo_client import fetch_history_kline

logger = logging.getLogger(__name__)


def cache_path_for(code: str) -> str:
    clean = code.split(".")[-1]
    return str(config.DATA_DIR / f"{clean}_daily.csv")


def normalize_kline(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    out = df.copy()
    out["date"] = pd.to_datetime(out["time_key"]).dt.date.astype(str)
    keep = [
        "date",
        "code",
        "open",
        "close",
        "high",
        "low",
        "volume",
        "turnover",
    ]
    for col in keep:
        if col not in out.columns:
            out[col] = pd.NA
    out = out[keep].rename(
        columns={
            "open": "open_price",
            "close": "close_price",
            "high": "high_price",
            "low": "low_price",
        }
    )
    numeric_cols = ["open_price", "close_price", "high_price", "low_price", "volume"]
    out[numeric_cols] = out[numeric_cols].apply(pd.to_numeric, errors="coerce")
    return out.dropna(subset=["open_price", "close_price"]).sort_values("date")


def load_or_update_daily(
    code: str = config.BACKTEST_SYMBOL,
    earliest_start: str | None = None,
) -> pd.DataFrame:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = cache_path_for(code)

    existing = pd.DataFrame()
    requested_start = earliest_start or config.BACKTEST_START
    start = requested_start
    if pd.io.common.file_exists(path):
        existing = pd.read_csv(path)
        if not existing.empty:
            last_date = pd.to_datetime(existing["date"]).max().date()
            cached_start = pd.to_datetime(existing["date"]).min().date()
            requested_date = pd.to_datetime(requested_start).date()
            # A requested date can be a weekend/holiday before the first valid bar.
            if cached_start > requested_date + timedelta(days=10):
                start = requested_start
            else:
                start = (last_date - timedelta(days=7)).isoformat()
            logger.info("Existing cache found through %s; updating from %s", last_date, start)

    raw = fetch_history_kline(code, start=start, end=date.today().isoformat())
    fresh = normalize_kline(raw)

    if existing.empty:
        combined = fresh
    elif fresh.empty:
        combined = existing
    else:
        combined = pd.concat([existing, fresh], ignore_index=True)

    if combined.empty:
        raise RuntimeError(f"No historical data returned for {code}")

    combined = (
        combined.drop_duplicates(subset=["date", "code"], keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )
    combined.to_csv(path, index=False)
    logger.info("Cached %s rows to %s", len(combined), path)
    return combined[pd.to_datetime(combined["date"]) >= pd.Timestamp(requested_start)].reset_index(drop=True)
