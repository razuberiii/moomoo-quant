from __future__ import annotations

import pandas as pd

from .. import config
from .defensive_factor import run_defensive_factor_v2
from .risk_parity import run_risk_parity_v1


def _correlation_matrix() -> pd.DataFrame:
    sources = {
        "Robot A · Trend v1": (config.RESULTS_DIR / "trend_monthly_returns.csv", "date"),
        "Robot B · Quality Low Vol v2": (config.RESULTS_DIR / "defensive_factor_v2_monthly.csv", "date"),
        "Robot C · Risk Parity v1": (config.RESULTS_DIR / "risk_parity_v1_monthly.csv", "date"),
    }
    returns = []
    for name, (path, date_column) in sources.items():
        frame = pd.read_csv(path)
        frame["month"] = pd.to_datetime(frame[date_column]).dt.to_period("M").astype(str)
        returns.append(frame.set_index("month")["return"].rename(name))
    return pd.concat(returns, axis=1).corr()


def run_research_suite() -> dict:
    defensive_factor = run_defensive_factor_v2()
    risk_parity = run_risk_parity_v1()
    correlation = _correlation_matrix()
    correlation.to_csv(config.RESULTS_DIR / "strategy_correlation_matrix.csv")
    return {"defensive_factor": defensive_factor, "risk_parity": risk_parity, "correlation": correlation}
