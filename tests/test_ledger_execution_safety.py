import ast
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pandas as pd

from moomoo_quant import config
from moomoo_quant.multi_strategy.bootstrap import initialize_multi_strategy_ledger
from moomoo_quant.multi_strategy.execution import ExecutionDisabledError, ProposedOrderService
from moomoo_quant.multi_strategy.ledger import ShadowLedger
from moomoo_quant.multi_strategy.models import (
    LifecycleStage, MarketPrice, RiskDecision, RiskStatus, StrategyDefinition, TargetRequest,
)
from moomoo_quant.multi_strategy.portfolio import aggregate_targets
from moomoo_quant.multi_strategy.portfolio import runtime_portfolio_totals


def test_benchmarks_are_not_strategy_accounts(tmp_path):
    ledger = initialize_multi_strategy_ledger(tmp_path / "ledger.db")
    ids = {row["strategy_id"] for row in ledger.rows("strategy_accounts")}
    assert ids == {config.TREND_STRATEGY_ID, config.MEAN_REVERSION_STRATEGY_ID}
    assert not any("buy-hold" in value or "equal-weight" in value for value in ids)


def test_ledger_restart_is_idempotent(tmp_path):
    path = tmp_path / "ledger.db"
    initialize_multi_strategy_ledger(path)
    ledger = initialize_multi_strategy_ledger(path)
    assert len(ledger.rows("strategy_accounts")) == 2
    assert len(ledger.rows("migration_events")) == 1


def test_immutable_admission_can_fund_a_pristine_existing_research_account_once(tmp_path):
    path = tmp_path / "ledger.db"
    ledger = ShadowLedger(path)
    ledger.migrate()
    ledger.register_strategy(
        StrategyDefinition(config.DEFENSIVE_FACTOR_STRATEGY_ID, "Quality Low Vol", "2", "JPY"),
        config.PORTFOLIO_ID,
        0.0,
        LifecycleStage.RESEARCH,
        "RESEARCH_REJECTED",
    )
    migrated = initialize_multi_strategy_ledger(path, include_research_slots=True)
    account = migrated.strategy_account(config.DEFENSIVE_FACTOR_STRATEGY_ID, "2")
    assert account["allocated_capital_jpy"] == 100_000
    assert account["cash_jpy"] == 100_000
    transitions = [
        row for row in migrated.rows("lifecycle_events")
        if row["strategy_id"] == config.DEFENSIVE_FACTOR_STRATEGY_ID
        and row["to_stage"] == LifecycleStage.SHADOW.value
    ]
    assert len(transitions) == 1
    assert transitions[0]["from_stage"] == LifecycleStage.RESEARCH.value
    assert transitions[0]["approved_by"] == "ADMISSION_POLICY"
    restarted = initialize_multi_strategy_ledger(path, include_research_slots=True)
    account = restarted.strategy_account(config.DEFENSIVE_FACTOR_STRATEGY_ID, "2")
    assert account["cash_jpy"] == 100_000
    assert len([
        row for row in restarted.rows("lifecycle_events")
        if row["strategy_id"] == config.DEFENSIVE_FACTOR_STRATEGY_ID
        and row["to_stage"] == LifecycleStage.SHADOW.value
    ]) == 1


def test_restart_preserves_funded_strategy_runtime_status(tmp_path):
    path = tmp_path / "ledger.db"
    ledger = initialize_multi_strategy_ledger(path, include_research_slots=True)
    account = ledger.strategy_account(config.TREND_STRATEGY_ID, "1")
    with ledger.connect() as conn:
        conn.execute(
            "UPDATE strategy_accounts SET status=? WHERE account_id=?",
            ("RECONCILED", account["account_id"]),
        )

    restarted = initialize_multi_strategy_ledger(path, include_research_slots=True)
    assert restarted.strategy_account(config.TREND_STRATEGY_ID, "1")["status"] == "RECONCILED"


def test_runtime_totals_include_cash_for_new_account_without_equity_snapshot():
    accounts = pd.DataFrame(
        [
            {"account_id": "A", "allocated_capital_jpy": 100_000, "cash_jpy": 100_000},
            {"account_id": "B", "allocated_capital_jpy": 100_000, "cash_jpy": 100_000},
            {"account_id": "C", "allocated_capital_jpy": 100_000, "cash_jpy": 100_000},
            {"account_id": "research", "allocated_capital_jpy": 0, "cash_jpy": 0},
        ]
    )
    snapshots = pd.DataFrame(
        [
            {"account_id": "A", "market_date": "2026-08-10", "equity_jpy": 101_000},
            {"account_id": "C", "market_date": "2026-08-10", "equity_jpy": 99_000},
        ]
    )

    allocated, equity, funded_count = runtime_portfolio_totals(accounts, snapshots)

    assert allocated == 300_000
    assert equity == 300_000
    assert funded_count == 3


def test_legacy_baseline_does_not_create_fill(tmp_path):
    ledger = initialize_multi_strategy_ledger(tmp_path / "ledger.db")
    with ledger.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM virtual_fills").fetchone()[0] == 0
    details = json.loads(ledger.rows("migration_events")[0]["details_json"])
    assert details["historical_fills_migrated"] is False
    assert details["historical_orders_created"] is False


def test_ledger_events_are_append_only(tmp_path):
    ledger = initialize_multi_strategy_ledger(tmp_path / "ledger.db")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with ledger.connect() as conn:
            conn.execute("UPDATE migration_events SET source='changed'")


def test_duplicate_signal_is_blocked_by_unique_key(tmp_path):
    ledger = ShadowLedger(tmp_path / "ledger.db")
    ledger.migrate()
    row = ("s1", "A", "1", "2026-07-31", "snap", "SIGNAL_RECORDED", "test", "now")
    with ledger.connect() as conn:
        conn.execute("INSERT INTO signals VALUES (?, ?, ?, ?, ?, ?, ?, ?)", row)
    with pytest.raises(sqlite3.IntegrityError):
        with ledger.connect() as conn:
            conn.execute("INSERT INTO signals VALUES (?, ?, ?, ?, ?, ?, ?, ?)", ("s2", *row[1:]))


def _approved_risk():
    return RiskDecision("risk-1", RiskStatus.APPROVED_FOR_PROPOSAL, (), datetime.now(timezone.utc), "snap-1", "test-v1")


def test_duplicate_proposed_order_is_idempotent(tmp_path):
    ledger = ShadowLedger(tmp_path / "ledger.db")
    ledger.migrate()
    now = datetime.now(timezone.utc)
    target = aggregate_targets(
        [TargetRequest("A", "1", "signal", 100_000, 100_000, {"SPY": 0.5})],
        {"SPY": MarketPrice("SPY", 500, 150, now, now)}, {}, 100_000,
    )[0]
    service = ProposedOrderService(ledger)
    assert service.create("portfolio", target, _approved_risk()) is not None
    assert service.create("portfolio", target, _approved_risk()) is None
    assert len(ledger.rows("proposed_orders")) == 1


def test_rejected_risk_cannot_create_proposal(tmp_path):
    ledger = ShadowLedger(tmp_path / "ledger.db")
    ledger.migrate()
    now = datetime.now(timezone.utc)
    target = aggregate_targets(
        [TargetRequest("A", "1", "signal", 100_000, 100_000, {"SPY": 0.5})],
        {"SPY": MarketPrice("SPY", 500, 150, now, now)}, {}, 100_000,
    )[0]
    rejected = RiskDecision("risk-2", RiskStatus.REJECTED, ("KILL_SWITCHED",), now, "snap-2", "v1")
    assert ProposedOrderService(ledger).create("portfolio", target, rejected) is None


def test_execution_layer_always_raises(tmp_path):
    ledger = ShadowLedger(tmp_path / "ledger.db")
    ledger.migrate()
    with pytest.raises(ExecutionDisabledError):
        ProposedOrderService(ledger).execute(None)


def test_simulate_and_live_lifecycle_are_disabled(tmp_path):
    ledger = initialize_multi_strategy_ledger(tmp_path / "ledger.db")
    with pytest.raises(PermissionError):
        ledger.transition_lifecycle("A", LifecycleStage.SHADOW, LifecycleStage.SIMULATE, "nobody", "none")
    with pytest.raises(PermissionError):
        ledger.transition_lifecycle("A", LifecycleStage.SHADOW, LifecycleStage.LIVE, "nobody", "none")


def test_all_strategy_definitions_use_jpy_reporting(tmp_path):
    ledger = initialize_multi_strategy_ledger(tmp_path / "ledger.db")
    assert {row["base_currency"] for row in ledger.rows("strategy_definitions")} == {"JPY"}


def test_no_executable_broker_trading_symbols():
    package = Path(config.BASE_DIR)
    banned_names = {"place_order", "unlock_trade", "OpenSecTradeContext"}
    violations = []
    for path in package.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Name, ast.Attribute)) and getattr(node, "id", getattr(node, "attr", None)) in banned_names:
                violations.append(f"{path.name}:{node.lineno}")
            if isinstance(node, ast.Attribute) and node.attr == "REAL":
                violations.append(f"{path.name}:{node.lineno}")
    assert violations == []
