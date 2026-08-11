from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from moomoo_quant import config
from moomoo_quant.jobs.simulate_runner import _reserve_modeled_execution_costs, run_bootstrap, run_preflight
from moomoo_quant.multi_strategy.models import MarketPrice, TargetRequest
from moomoo_quant.trading.moomoo_simulate_adapter import SimulateSafetyError
from moomoo_quant.trading.moomoo_simulate_gateway import (
    OpenDSimulateGateway,
    SimulateAccount,
    SimulatePreflight,
    _map_order_status,
)


SDK = {
    "RET_OK": 0,
    "TrdEnv": SimpleNamespace(SIMULATE="SIMULATE"),
    "TrdMarket": SimpleNamespace(US="US"),
    "SimAccType": SimpleNamespace(STOCK="STOCK", STOCK_AND_OPTION="STOCK_AND_OPTION"),
}


class AccountContext:
    def __init__(self, rows):
        self.rows = rows

    def get_acc_list(self):
        return 0, pd.DataFrame(self.rows)


def account(account_id, environment="SIMULATE", account_type="STOCK"):
    return {
        "acc_id": account_id,
        "trd_env": environment,
        "sim_acc_type": account_type,
        "trdmarket_auth": ["US"],
        "acc_status": "ACTIVE",
    }


def test_account_selector_ignores_live_rows_and_never_exposes_account_id():
    gateway = OpenDSimulateGateway()
    selected = gateway._select_account(
        AccountContext([account(123456, "LIVE"), account(998877)]),
        SDK,
    )
    assert selected.account_id == 998877
    assert "998877" not in selected.fingerprint


def test_account_selector_fails_closed_when_paper_account_is_ambiguous():
    gateway = OpenDSimulateGateway()
    with pytest.raises(SimulateSafetyError, match="exactly one"):
        gateway._select_account(AccountContext([account(1), account(2)]), SDK)


def test_configured_account_resolves_an_ambiguous_paper_account_list():
    selected = OpenDSimulateGateway("2")._select_account(
        AccountContext([account(1), account(2)]), SDK
    )
    assert selected.account_id == 2


@pytest.mark.parametrize(
    ("broker", "internal"),
    [
        ("SUBMITTED", "SUBMITTED"),
        ("FILLED_PART", "PARTIALLY_FILLED"),
        ("FILLED_ALL", "FILLED"),
        ("FAILED", "REJECTED"),
        ("CANCELLED_ALL", "CANCELLED"),
    ],
)
def test_order_status_mapping(broker, internal):
    assert _map_order_status(broker) == internal


def test_simulate_target_reserves_each_strategy_own_modeled_costs():
    now = datetime(2026, 8, 11, tzinfo=timezone.utc)
    requests = [TargetRequest("A", "1", "signal", 100_000, 100_000, {"US.SPY": 1.0})]
    prices = {"US.SPY": MarketPrice("US.SPY", 500, 150, now, now)}
    adjusted = _reserve_modeled_execution_costs(requests, prices)[0]
    invested = 100_000 * sum(adjusted.target_weights.values())
    assert invested < 100_000
    assert 100_000 - invested > 100


class PreflightGateway:
    def __init__(self, positions=(), orders=()):
        self.calls = 0
        self.value = SimulatePreflight(
            SimulateAccount(123, "sim-safe", "STOCK", "ACTIVE"),
            1_000_000.0,
            1_000_000.0,
            tuple(positions),
            tuple(orders),
        )

    def preflight(self):
        self.calls += 1
        return self.value


def test_preflight_is_read_only_and_sanitized(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LEDGER_PATH", tmp_path / "ledger.db")
    monkeypatch.setattr(config, "MOOMOO_SIMULATE_ENABLED", True)
    gateway = PreflightGateway()
    result = run_preflight(gateway)
    assert result["state"] == "PREFLIGHT_OK"
    assert result["account_fingerprint"] == "sim-safe"
    assert "account_id" not in result
    assert gateway.calls == 1


def test_bootstrap_needs_confirmation_before_touching_gateway(monkeypatch):
    monkeypatch.setattr(config, "MOOMOO_SIMULATE_ENABLED", True)
    monkeypatch.setattr(config, "MOOMOO_SIMULATE_KILL_SWITCH", False)
    gateway = PreflightGateway()
    with pytest.raises(SimulateSafetyError, match="Explicit"):
        run_bootstrap("wrong", gateway)
    assert gateway.calls == 0


def test_bootstrap_waits_for_market_open_without_submitting(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LEDGER_PATH", tmp_path / "ledger.db")
    monkeypatch.setattr(config, "MOOMOO_SIMULATE_ENABLED", True)
    monkeypatch.setattr(config, "MOOMOO_SIMULATE_KILL_SWITCH", False)
    gateway = PreflightGateway()
    result = run_bootstrap(
        "SIMULATE_ONLY",
        gateway,
        now=datetime(2026, 8, 11, 2, 0, tzinfo=timezone.utc),
    )
    assert result["state"] == "WAITING_MARKET_OPEN"
    assert gateway.calls == 1
