from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from moomoo_quant import config
from moomoo_quant.jobs.simulate_runner import (
    _activity_is_owned,
    _current_requests,
    _reserve_modeled_execution_costs,
    run_auto,
    run_bootstrap,
    run_preflight,
)
from moomoo_quant.multi_strategy.bootstrap import initialize_multi_strategy_ledger
from moomoo_quant.multi_strategy.ledger import ShadowLedger
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
    "Currency": SimpleNamespace(USD="USD"),
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
    ("enabled", "kill_switch", "message"),
    [(False, False, "ENABLED=false"), (True, True, "KILL_SWITCH=true")],
)
def test_gateway_submission_gates_fail_before_opening_a_context(
    enabled, kill_switch, message, monkeypatch
):
    gateway = OpenDSimulateGateway(enabled=enabled, kill_switch=kill_switch)
    touched = False

    def context_must_not_open():
        nonlocal touched
        touched = True
        raise AssertionError("OpenD context should not be opened")

    monkeypatch.setattr(gateway, "_context", context_must_not_open)
    with pytest.raises(SimulateSafetyError, match=message):
        gateway.submit_simulated_order(
            symbol="US.SPY",
            side="BUY",
            quantity=1.0,
            trading_environment="SIMULATE",
            idempotency_key="gate-test",
        )
    assert not touched


class PositionContext:
    def __init__(self, rows):
        self.rows = rows

    def position_list_query(self, **kwargs):
        return 0, pd.DataFrame(self.rows)


def test_gateway_sell_rejects_short_or_insufficient_long_inventory():
    account_record = SimulateAccount(123, "sim-safe", "STOCK", "ACTIVE")
    with pytest.raises(SimulateSafetyError, match="No sellable LONG"):
        OpenDSimulateGateway._require_sellable_long_position(
            PositionContext(
                [{"code": "US.SPY", "position_side": "SHORT", "can_sell_qty": 10.0}]
            ),
            SDK,
            account_record,
            "US.SPY",
            1.0,
        )
    with pytest.raises(SimulateSafetyError, match="exceed"):
        OpenDSimulateGateway._require_sellable_long_position(
            PositionContext(
                [{"code": "US.SPY", "position_side": "LONG", "can_sell_qty": 0.5}]
            ),
            SDK,
            account_record,
            "US.SPY",
            1.0,
        )


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


def _record_broker_event(ledger, *, status="FILLED", dealt_qty=1.0):
    ledger.record_broker_order_event(
        "rebalance-1",
        "owned-position-key",
        "PORTFOLIO_MANAGER",
        "US.SPY",
        "BUY",
        1.0,
        status,
        "order-1",
        {"dealt_qty": dealt_qty},
    )


def test_allowlisted_foreign_position_is_not_treated_as_project_owned(tmp_path):
    ledger = ShadowLedger(tmp_path / "ledger.db")
    ledger.migrate()
    _record_broker_event(ledger, dealt_qty=0.5)
    preflight = PreflightGateway(
        positions=({"code": "US.SPY", "qty": 1.0, "position_side": "LONG"},)
    ).value
    assert not _activity_is_owned(preflight, ledger)


def test_position_must_match_recorded_filled_quantity(tmp_path):
    ledger = ShadowLedger(tmp_path / "ledger.db")
    ledger.migrate()
    _record_broker_event(ledger)
    preflight = PreflightGateway(
        positions=({"code": "US.SPY", "qty": 1.0, "position_side": "LONG"},)
    ).value
    assert _activity_is_owned(preflight, ledger)


def test_open_order_requires_exact_recorded_id_and_remark(tmp_path):
    ledger = ShadowLedger(tmp_path / "ledger.db")
    ledger.migrate()
    _record_broker_event(ledger, status="SUBMITTED", dealt_qty=0.0)
    foreign = PreflightGateway(
        orders=(
            {
                "code": "US.SPY",
                "order_id": "foreign-order",
                "remark": "mq:someone-elses-key",
                "trd_side": "BUY",
                "qty": 1.0,
            },
        )
    ).value
    owned = PreflightGateway(
        orders=(
            {
                "code": "US.SPY",
                "order_id": "order-1",
                "remark": "mq:owned-position-key",
                "trd_side": "BUY",
                "qty": 1.0,
            },
        )
    ).value
    assert not _activity_is_owned(foreign, ledger)
    assert _activity_is_owned(owned, ledger)


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


def test_simulate_cycle_ledger_marks_only_terminal_targets_processed(tmp_path):
    ledger = ShadowLedger(tmp_path / "ledger.db")
    ledger.migrate()
    ledger.record_simulate_cycle_event("digest-1", "AUTO", "WAITING_MARKET_OPEN", ["signal-a"], {})
    assert not ledger.simulate_target_processed("digest-1")
    ledger.record_simulate_cycle_event("digest-1", "BOOTSTRAP", "SUBMITTED", ["signal-a"], {})
    assert ledger.simulate_target_processed("digest-1")
    assert ledger.has_simulate_bootstrap()


def test_rejected_bootstrap_without_broker_order_does_not_start_observer(tmp_path):
    ledger = ShadowLedger(tmp_path / "ledger.db")
    ledger.migrate()
    ledger.record_simulate_cycle_event(
        "digest-1",
        "BOOTSTRAP",
        "ORDER_REJECTED",
        ["signal-a"],
        {"submissions": [{"symbol": "US.SPY", "status": "REJECTED", "order_id": None}]},
    )
    assert not ledger.has_simulate_bootstrap()


def test_partial_rejected_bootstrap_with_broker_order_starts_observer(tmp_path):
    ledger = ShadowLedger(tmp_path / "ledger.db")
    ledger.migrate()
    ledger.record_simulate_cycle_event(
        "digest-1",
        "BOOTSTRAP",
        "ORDER_REJECTED",
        ["signal-a"],
        {"submissions": [{"symbol": "US.IEF", "status": "SUBMITTED", "order_id": "7794062"}]},
    )
    ledger.record_broker_order_event(
        "simulate-bootstrap:2026-08-11:digest-1",
        "order-key-1",
        "PORTFOLIO_MANAGER",
        "US.IEF",
        "BUY",
        3.0,
        "SUBMITTED",
        "7794062",
        {"broker_status": "SUBMITTING"},
    )
    assert ledger.has_simulate_bootstrap()


def test_auto_requires_accepted_bootstrap_before_touching_opend(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LEDGER_PATH", tmp_path / "ledger.db")
    monkeypatch.setattr(config, "MOOMOO_SIMULATE_ENABLED", True)
    monkeypatch.setattr(config, "MOOMOO_SIMULATE_KILL_SWITCH", False)
    monkeypatch.setattr(config, "MOOMOO_SIMULATE_AUTO_ENABLED", True)
    gateway = PreflightGateway()
    assert run_auto(gateway)["state"] == "NEEDS_BOOTSTRAP"
    assert gateway.calls == 0


def test_current_requests_switches_to_new_forward_signal(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LEDGER_PATH", tmp_path / "ledger.db")
    ledger = initialize_multi_strategy_ledger(include_research_slots=True)
    signal_id, inserted = ledger.record_signal(
        config.TREND_STRATEGY_ID,
        "1",
        "2026-08-31",
        {"test": "new-month-end"},
        {"US.GLD": 1.0},
        100_000.0,
        "forward test signal",
    )
    assert inserted
    request = next(item for item in _current_requests(ledger) if item.strategy_id == config.TREND_STRATEGY_ID)
    assert request.signal_id == signal_id
    assert request.target_weights == {"US.GLD": 1.0}
