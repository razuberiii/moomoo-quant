from __future__ import annotations

import json

import pandas as pd

from .. import config
from .risk_parity import run_risk_parity_v1
from .stress_pullback import run_stress_pullback_v2


def _correlation_matrix() -> pd.DataFrame:
    sources = {
        "Robot A · Trend v1": (config.RESULTS_DIR / "trend_monthly_returns.csv", "date"),
        "Robot B · Stress Pullback v2": (config.RESULTS_DIR / "stress_pullback_v2_monthly.csv", "date"),
        "Robot C · Risk Parity v1": (config.RESULTS_DIR / "risk_parity_v1_monthly.csv", "date"),
    }
    returns = []
    for name, (path, date_column) in sources.items():
        frame = pd.read_csv(path)
        frame["month"] = pd.to_datetime(frame[date_column]).dt.to_period("M").astype(str)
        returns.append(frame.set_index("month")["return"].rename(name))
    return pd.concat(returns, axis=1).corr()


def run_research_suite() -> dict:
    stress = run_stress_pullback_v2()
    risk_parity = run_risk_parity_v1()
    correlation = _correlation_matrix()
    correlation.to_csv(config.RESULTS_DIR / "strategy_correlation_matrix.csv")

    # Mutable display summaries may reference later candidates; immutable first
    # result files remain untouched.
    stress_path = config.RESULTS_DIR / "stress_pullback_v2_research.json"
    stress_summary = json.loads(stress_path.read_text(encoding="utf-8"))
    stress_summary["monthly_correlation_with_risk_parity_v1"] = float(
        correlation.loc["Robot B · Stress Pullback v2", "Robot C · Risk Parity v1"]
    )
    stress_path.write_text(json.dumps(stress_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"stress": stress, "risk_parity": risk_parity, "correlation": correlation}
