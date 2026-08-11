import ast
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from moomoo_quant import config
from moomoo_quant.multi_strategy.bootstrap import initialize_multi_strategy_ledger
from moomoo_quant.multi_strategy.execution import ExecutionDisabledError, ProposedOrderService
from moomoo_quant.multi_strategy.ledger import ShadowLedger
from moomoo_quant.multi_strategy.models import (
    LifecycleStage, MarketPrice, RiskDecision, RiskStatus, TargetRequest,
)
from moomoo_quant.multi_strategy.portfolio import aggregate_targets


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
