import logging
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

from . import config
from .data_loader import load_or_update_daily

logger = logging.getLogger(__name__)


def _normalize_yahoo_fx(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close"])
    out = raw.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = out.columns.get_level_values(0)
    out = out.reset_index().rename(columns=str.lower)
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    keep = ["date", "open", "high", "low", "close"]
    out = out[keep].apply(lambda col: pd.to_numeric(col, errors="coerce") if col.name != "date" else col)
    return out.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)


def load_or_update_usdjpy(start: str = config.TREND_START) -> pd.DataFrame:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = config.DATA_DIR / config.FX_CACHE_NAME
    existing = pd.read_csv(path) if path.exists() else pd.DataFrame()
    download_start = start

    if not existing.empty:
        first = pd.to_datetime(existing["date"]).min().date()
        last = pd.to_datetime(existing["date"]).max().date()
        download_start = start if first > pd.to_datetime(start).date() else (last - timedelta(days=7)).isoformat()

    # Yahoo's end date is exclusive.
    raw = yf.download(
        config.FX_YAHOO_SYMBOL,
        start=download_start,
        end=(date.today() + timedelta(days=1)).isoformat(),
        auto_adjust=False,
        progress=False,
    )
    fresh = _normalize_yahoo_fx(raw)
    combined = fresh if existing.empty else pd.concat([existing, fresh], ignore_index=True)
    if combined.empty:
        raise RuntimeError("No USDJPY history returned by Yahoo Finance")
    combined = combined.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
    combined.to_csv(path, index=False)
    logger.info("Cached %s USDJPY rows to %s", len(combined), path)
    return combined


def load_trend_history() -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    assets = {
        code.split(".")[-1]: load_or_update_daily(code, earliest_start=config.TREND_START)
        for code in config.TREND_SYMBOLS
    }
    return assets, load_or_update_usdjpy(config.TREND_START)


def load_cached_trend_history() -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """Load the existing research cache without making any network request."""
    assets: dict[str, pd.DataFrame] = {}
    missing: list[str] = []
    for code in config.TREND_SYMBOLS:
        symbol = code.split(".")[-1]
        path = config.DATA_DIR / f"{symbol}_daily.csv"
        if not path.exists():
            missing.append(str(path))
            continue
        assets[symbol] = pd.read_csv(path)

    fx_path = config.DATA_DIR / config.FX_CACHE_NAME
    if not fx_path.exists():
        missing.append(str(fx_path))
    if missing:
        raise FileNotFoundError("Missing cached trend data: " + ", ".join(missing))

    return assets, pd.read_csv(fx_path)


def load_defensive_factor_history() -> dict[str, pd.DataFrame]:
    """Update the factor ETF caches through the quote-only OpenD path."""
    return {
        code.split(".")[-1]: load_or_update_daily(code, earliest_start="2013-01-01")
        for code in config.DEFENSIVE_FACTOR_SYMBOLS
    }


def load_cached_defensive_factor_history() -> dict[str, pd.DataFrame]:
    """Load factor ETF history without opening OpenD or making a network call."""
    assets: dict[str, pd.DataFrame] = {}
    missing: list[str] = []
    for code in config.DEFENSIVE_FACTOR_SYMBOLS:
        symbol = code.split(".")[-1]
        path = config.DATA_DIR / f"{symbol}_daily.csv"
        if path.exists():
            assets[symbol] = pd.read_csv(path)
        else:
            missing.append(str(path))
    if missing:
        raise FileNotFoundError("Missing cached defensive factor data: " + ", ".join(missing))
    return assets
