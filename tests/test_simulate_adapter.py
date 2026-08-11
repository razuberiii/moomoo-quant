import ast
from datetime import datetime, timezone

import pytest

from moomoo_quant import config
from moomoo_quant.multi_strategy.ledger import ShadowLedger
from moomoo_quant.multi_strategy.models import ProposedOrder, RiskDecision, RiskScope, RiskStatus
from moomoo_quant.trading.moomoo_simulate_adapter import (
    MoomooSimulateExecutionAdapter,
    SIMULATE_ENVIRONMENT,
    SimulateExecutionDisabled,
    SimulateSafetyError,
)


class FakeGateway:
    def __init__(self):
        self.calls = []

    def submit_simulated_order(self, **kwargs):
        self.calls.append(kwargs)
        return {"order_id": "fake-1", "status": "SUBMITTED"}


def order():
    now = datetime.now(timezone.utc)
    return ProposedOrder("p1", "key1", "portfolio", "snap", "US.SPY", "BUY", 1.0, 1, 50_000, 10, 5, 5, "PROPOSED", now)


def approved():
    return RiskDecision("r1", RiskStatus.APPROVED_FOR_PROPOSAL, (), datetime.now(timezone.utc), "snap", "v1", RiskScope.PROPOSAL_SCOPE)


def test_adapter_explicitly_fixes_simulate_environment(tmp_path):
    adapter = MoomooSimulateExecutionAdapter(ShadowLedger(tmp_path / "db"), FakeGateway())
    assert adapter.trading_environment == SIMULATE_ENVIRONMENT


def test_adapter_rejects_every_non_simulate_environment(tmp_path):
    with pytest.raises(SimulateSafetyError):
        MoomooSimulateExecutionAdapter(ShadowLedger(tmp_path / "db"), FakeGateway(), requested_environment="REAL")


def test_default_disabled_never_calls_gateway(tmp_path):
    ledger = ShadowLedger(tmp_path / "db")
    ledger.migrate()
    gateway = FakeGateway()
    adapter = MoomooSimulateExecutionAdapter(ledger, gateway, strategy_allowlist=frozenset({"A"}))
    with pytest.raises(SimulateExecutionDisabled):
        adapter.submit(order(), approved(), rebalance_id="rebalance-1", strategy_id="A")
    assert gateway.calls == []


def test_mock_submission_is_idempotent_and_passes_simulate_explicitly(tmp_path):
    ledger = ShadowLedger(tmp_path / "db")
    ledger.migrate()
    gateway = FakeGateway()
    adapter = MoomooSimulateExecutionAdapter(ledger, gateway, enabled=True, strategy_allowlist=frozenset({"A"}))
    first = adapter.submit(order(), approved(), rebalance_id="rebalance-1", strategy_id="A")
    second = adapter.submit(order(), approved(), rebalance_id="rebalance-1", strategy_id="A")
    assert first.order_id == second.order_id == "fake-1"
    assert len(gateway.calls) == 1
    assert gateway.calls[0]["trading_environment"] == SIMULATE_ENVIRONMENT


def test_no_trade_account_or_unlock_api_is_executable():
    banned = {"get_acc_list", "unlock_trade", "place_order", "OpenSecTradeContext"}
    violations = []
    for path in config.BASE_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            name = getattr(node, "id", getattr(node, "attr", None))
            if name in banned:
                violations.append((path.name, node.lineno, name))
    assert violations == []


def test_dashboard_has_status_text_but_no_enable_control():
    source = (config.BASE_DIR / "dashboard.py").read_text(encoding="utf-8")
    assert "Moomoo SIMULATE：已实现，等待用户单独批准启用" in source
    assert "启用 SIMULATE" not in source
    assert "MOOMOO_SIMULATE_ENABLED=false" not in source
