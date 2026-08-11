from __future__ import annotations

import json
from pathlib import Path

from .. import config
from .ledger import ShadowLedger
from .models import LifecycleStage, StrategyDefinition


def initialize_multi_strategy_ledger(
    ledger_path: Path | None = None,
    trend_run_id: str | None = None,
    mean_reversion_status: str = "RESEARCH_PENDING",
    stress_status: str | None = None,
    risk_parity_status: str | None = None,
    include_research_slots: bool = False,
) -> ShadowLedger:
    ledger = ShadowLedger(ledger_path or config.LEDGER_PATH)
    ledger.migrate()
    ledger.record_migration(
        "migration:legacy-shadow-read-only:v1",
        {
            "policy": "Legacy CSV shadow remains untouched and read-only",
            "historical_fills_migrated": False,
            "historical_orders_created": False,
            "capital_split": False,
        },
    )
    ledger.register_strategy(
        StrategyDefinition(
            strategy_id=config.TREND_STRATEGY_ID,
            name="JPY Multi-Asset Trend",
            version="1",
            signal_currency="JPY",
            benchmark_ids=("spy-buy-hold-jpy", "qqq-buy-hold-jpy", "static-equal-weight-jpy"),
        ),
        portfolio_id=config.PORTFOLIO_ID,
        allocated_capital_jpy=config.PORTFOLIO_CAPITAL_JPY,
        stage=LifecycleStage.SHADOW,
        status="WAITING_NEXT_MONTH_END",
        evidence_run_id=trend_run_id,
    )
    ledger.register_strategy(
        StrategyDefinition(
            strategy_id=config.MEAN_REVERSION_STRATEGY_ID,
            name="US ETF Short-Term Mean Reversion",
            version="1",
            signal_currency="USD",
            benchmark_ids=("spy-buy-hold-jpy",),
        ),
        portfolio_id=config.PORTFOLIO_ID,
        allocated_capital_jpy=0.0,
        stage=LifecycleStage.RESEARCH,
        status=mean_reversion_status,
        evidence_run_id=None,
    )
    if not include_research_slots:
        return ledger
    stress_summary = _research_summary("stress_pullback_v2_research.json")
    stress_status = stress_status or (stress_summary or {}).get("status", "RESEARCH_PENDING")
    stress_shadow = stress_status == "SHADOW"
    ledger.register_strategy(
        StrategyDefinition(
            strategy_id=config.STRESS_PULLBACK_STRATEGY_ID,
            name="Stress Pullback Mean Reversion",
            version="2",
            signal_currency="USD",
            benchmark_ids=("spy-buy-hold-jpy",),
        ),
        portfolio_id=config.PORTFOLIO_ID,
        allocated_capital_jpy=100_000.0 if stress_shadow else 0.0,
        stage=LifecycleStage.SHADOW if stress_shadow else LifecycleStage.RESEARCH,
        status="WAITING_NEXT_SIGNAL" if stress_shadow else stress_status,
        evidence_run_id=(stress_summary or {}).get("run_id"),
    )
    risk_summary = _research_summary("risk_parity_v1_research.json")
    risk_parity_status = risk_parity_status or (risk_summary or {}).get("status", "RESEARCH_PENDING")
    risk_shadow = risk_parity_status == "SHADOW"
    ledger.register_strategy(
        StrategyDefinition(
            strategy_id=config.RISK_PARITY_STRATEGY_ID,
            name="JPY Unlevered Risk Parity",
            version="1",
            signal_currency="JPY",
            benchmark_ids=("spy-buy-hold-jpy",),
        ),
        portfolio_id=config.PORTFOLIO_ID,
        allocated_capital_jpy=100_000.0 if risk_shadow else 0.0,
        stage=LifecycleStage.SHADOW if risk_shadow else LifecycleStage.RESEARCH,
        status="WAITING_NEXT_MONTH_END" if risk_shadow else risk_parity_status,
        evidence_run_id=(risk_summary or {}).get("run_id"),
    )
    return ledger


def _research_summary(filename: str) -> dict | None:
    path = config.RESULTS_DIR / filename
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
