from __future__ import annotations

import json
from pathlib import Path

from .. import config
from .admission import load_admission
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
    trend_admission = _required_admission(config.TREND_STRATEGY_ID, "1")
    ledger.register_strategy(
        StrategyDefinition(
            strategy_id=config.TREND_STRATEGY_ID,
            name="JPY Multi-Asset Trend",
            version="1",
            signal_currency="JPY",
            benchmark_ids=("spy-buy-hold-jpy", "qqq-buy-hold-jpy", "static-equal-weight-jpy"),
        ),
        portfolio_id=config.PORTFOLIO_ID,
        allocated_capital_jpy=float(trend_admission["allocated_capital_jpy"]),
        stage=LifecycleStage.SHADOW,
        status="WAITING_NEXT_MONTH_END",
        evidence_run_id=trend_run_id or trend_admission["evidence_run_id"],
    )
    ledger.apply_admission_budget(trend_admission, "WAITING_NEXT_MONTH_END")
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
    risk_admission = _required_admission(config.RISK_PARITY_STRATEGY_ID, "1")
    risk_shadow = risk_admission["decision"] == "SHADOW_READY"
    risk_parity_status = "WAITING_NEXT_MONTH_END" if risk_shadow else "RESEARCH_INVALID"
    ledger.register_strategy(
        StrategyDefinition(
            strategy_id=config.RISK_PARITY_STRATEGY_ID,
            name="JPY Unlevered Risk Parity",
            version="1",
            signal_currency="JPY",
            benchmark_ids=("spy-buy-hold-jpy",),
        ),
        portfolio_id=config.PORTFOLIO_ID,
        allocated_capital_jpy=float(risk_admission["allocated_capital_jpy"]),
        stage=LifecycleStage.SHADOW if risk_shadow else LifecycleStage.RESEARCH,
        status="WAITING_NEXT_MONTH_END" if risk_shadow else risk_parity_status,
        evidence_run_id=risk_admission["evidence_run_id"],
    )
    ledger.apply_admission_budget(risk_admission, "WAITING_NEXT_MONTH_END")
    factor_admission = _required_admission(config.DEFENSIVE_FACTOR_STRATEGY_ID, "2")
    factor_shadow = factor_admission["decision"] == "SHADOW_READY"
    factor_status = "WAITING_NEXT_HALF_YEAR_END" if factor_shadow else "RESEARCH_INVALID"
    ledger.register_strategy(
        StrategyDefinition(
            strategy_id=config.DEFENSIVE_FACTOR_STRATEGY_ID,
            name="US Quality & Low Volatility",
            version="2",
            signal_currency="JPY",
            benchmark_ids=("spy-buy-hold-jpy",),
        ),
        portfolio_id=config.PORTFOLIO_ID,
        allocated_capital_jpy=float(factor_admission["allocated_capital_jpy"]),
        stage=LifecycleStage.SHADOW if factor_shadow else LifecycleStage.RESEARCH,
        status="WAITING_NEXT_HALF_YEAR_END" if factor_shadow else factor_status,
        evidence_run_id=factor_admission["evidence_run_id"],
    )
    ledger.apply_admission_budget(factor_admission, "WAITING_NEXT_HALF_YEAR_END")
    return ledger


def _research_summary(filename: str) -> dict | None:
    path = config.RESULTS_DIR / filename
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _required_admission(strategy_id: str, version: str) -> dict:
    admission = load_admission(strategy_id, version)
    if admission is None:
        raise RuntimeError(f"Missing immutable admission for {strategy_id} v{version}")
    return admission
