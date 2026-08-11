from datetime import datetime, timezone

import pandas as pd

from moomoo_quant import config
from moomoo_quant.jobs.shadow_runner import ShadowSignalCandidate, run_shadow_cycle
from moomoo_quant.multi_strategy.bootstrap import initialize_multi_strategy_ledger
from moomoo_quant.multi_strategy.models import RiskInput, RiskScope, RiskStatus
from moomoo_quant.multi_strategy.risk import evaluate_risk


SESSIONS = pd.DatetimeIndex(["2026-08-27", "2026-08-28", "2026-08-31", "2026-09-01"])


def fixture_daily(end="2026-09-01", stale_fx=False):
    index = SESSIONS[SESSIONS <= pd.Timestamp(end)]
    output = {}
    for offset, symbol in enumerate(("SPY", "QQQ", "GLD", "IEF")):
        values = [100 + offset + i for i in range(len(index))]
        output[symbol] = pd.DataFrame(
            {
                "open_price": values,
                "close_price": [value + 0.5 for value in values],
                "usdjpy": 150.0,
                "exec_usdjpy": 150.0,
                "fx_date": pd.Timestamp("2026-08-01") if stale_fx else index,
                "exec_fx_date": pd.Timestamp("2026-08-01") if stale_fx else index - pd.Timedelta(days=1),
            },
            index=index,
        )
    return output


def candidate():
    return ShadowSignalCandidate(
        config.TREND_STRATEGY_ID,
        "1",
        "2026-08-31",
        {"SPY": 0.5, "QQQ": 0.5, "GLD": 0.0, "IEF": 0.0},
        {"fixture": True, "weights": {"SPY": 0.5, "QQQ": 0.5}},
        "fixture month-end signal",
    )


def initialized(tmp_path):
    return initialize_multi_strategy_ledger(tmp_path / "ledger.db", include_research_slots=True)


def first_cycle(ledger):
    return run_shadow_cycle(
        ledger,
        fixture_daily("2026-08-31"),
        [candidate()],
        datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc),
        sessions=SESSIONS,
    )


def second_cycle(ledger, daily=None, now=None):
    return run_shadow_cycle(
        ledger,
        daily or fixture_daily(),
        [candidate()],
        now or datetime(2026, 9, 1, 23, 0, tzinfo=timezone.utc),
        sessions=SESSIONS,
    )


def test_month_end_signal_is_idempotent(tmp_path):
    ledger = initialized(tmp_path)
    assert first_cycle(ledger)["signals_recorded"] == 1
    assert first_cycle(ledger)["signals_recorded"] == 0
    assert len(ledger.rows("signals")) == 1


def test_full_shadow_state_machine_reaches_reconciled(tmp_path):
    ledger = initialized(tmp_path)
    assert first_cycle(ledger)["state"] == "WAITING_FOR_OPEN"
    assert second_cycle(ledger)["state"] == "RECONCILED"
    states = {row["state"] for row in ledger.rows("shadow_run_events")}
    assert {"WAITING_FOR_OPEN", "VIRTUAL_FILLED", "RECONCILED"} <= states


def test_virtual_fill_is_idempotent_across_restart(tmp_path):
    path = tmp_path / "ledger.db"
    ledger = initialize_multi_strategy_ledger(path, include_research_slots=True)
    first_cycle(ledger)
    second_cycle(ledger)
    fills = len(ledger.rows("virtual_fills"))
    restarted = initialize_multi_strategy_ledger(path, include_research_slots=True)
    assert second_cycle(restarted)["virtual_fills"] == 0
    assert len(restarted.rows("virtual_fills")) == fills


def test_equity_snapshot_is_idempotent(tmp_path):
    ledger = initialized(tmp_path)
    first_cycle(ledger)
    before = len(ledger.rows("equity_snapshots"))
    first_cycle(ledger)
    assert len(ledger.rows("equity_snapshots")) == before


def test_shadow_scope_ignores_execution_kill_switch():
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    risk = RiskInput(
        "snap", now, now, now, True, True, False, True, False, False,
        "US_EQUITY_ETF", 1.0, {"SPY": 1.0}, {"A": 100}, {"A": 100},
        {"A": 0}, 100, 100,
    )
    decision = evaluate_risk(risk, now, RiskScope.SHADOW_SCOPE)
    assert decision.status is RiskStatus.APPROVED_FOR_SHADOW
    assert "KILL_SWITCHED" not in decision.reasons


def test_stale_market_data_blocks_fill(tmp_path):
    ledger = initialized(tmp_path)
    first_cycle(ledger)
    result = second_cycle(ledger, now=datetime(2026, 9, 10, 23, 0, tzinfo=timezone.utc))
    assert result["virtual_fills"] == 0
    assert len(ledger.rows("virtual_fills")) == 0


def test_stale_fx_blocks_fill(tmp_path):
    ledger = initialized(tmp_path)
    first_cycle(ledger)
    result = second_cycle(ledger, daily=fixture_daily(stale_fx=True))
    assert result["virtual_fills"] == 0
    assert len(ledger.rows("virtual_fills")) == 0


def test_no_baseline_backfill(tmp_path):
    ledger = initialized(tmp_path)
    old = ShadowSignalCandidate(config.TREND_STRATEGY_ID, "1", "2026-07-31", {"SPY": 1.0}, {}, "old")
    run_shadow_cycle(ledger, fixture_daily("2026-08-31"), [old], datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc), sessions=SESSIONS)
    assert ledger.rows("signals") == []

