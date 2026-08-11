from pathlib import Path

import pandas as pd
import pytest

from moomoo_quant.backtest.trend_analysis import _crisis_2022_summary, _monthly_crisis_data
from moomoo_quant.backtest.trend_engine import run_trend_backtest
from moomoo_quant.strategies.jpy_multi_asset_trend import prepare_jpy_daily


@pytest.fixture(scope="session")
def trend_result():
    fixture = Path(__file__).parent / "fixtures" / "trend_v1_frozen"
    assets = {symbol: pd.read_csv(fixture / f"{symbol}_daily.csv") for symbol in ("SPY", "QQQ", "GLD", "IEF")}
    fx = pd.read_csv(fixture / "USDJPY_daily.csv")
    daily = prepare_jpy_daily(assets, fx)
    full = run_trend_backtest(daily)
    crisis = _monthly_crisis_data(full, daily)
    return {
        "full": full,
        "latest_signal": full["signals"].iloc[-1],
        "dashboard": {"summary_2022": _crisis_2022_summary(crisis, daily)},
    }
