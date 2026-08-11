from datetime import datetime, timedelta, timezone

import pytest

from moomoo_quant.multi_strategy.models import MarketPrice, RiskInput, RiskScope, RiskStatus, TargetRequest
from moomoo_quant.multi_strategy.portfolio import PortfolioError, aggregate_targets
from moomoo_quant.multi_strategy.risk import evaluate_risk


NOW = datetime(2026, 8, 11, tzinfo=timezone.utc)


def price(symbol="SPY", days_old=0, fx_days_old=0):
    return MarketPrice(symbol, 100.0, 150.0, NOW - timedelta(days=days_old), NOW - timedelta(days=fx_days_old))


def request(strategy, weight, capital=100_000.0, cash=100_000.0):
    return TargetRequest(strategy, "1", f"signal:{strategy}", capital, cash, {"SPY": weight})


def risk_input(**overrides):
    values = dict(
        input_snapshot_id="snapshot-1", generated_at=NOW, market_timestamp=NOW,
        fx_timestamp=NOW, strategy_versions_match=True,
        portfolio_snapshot_consistent=True, opend_healthy=True, kill_switch=False,
        duplicate_rebalance=False, duplicate_proposal=False,
        instrument_type="US_EQUITY_ETF", leverage=1.0,
        proposed_quantities={"SPY": 1.0}, budget_usage_jpy={"A": 50_000.0},
        budget_limits_jpy={"A": 100_000.0}, projected_cash_jpy={"A": 50_000.0},
        total_target_jpy=50_000.0, portfolio_limit_jpy=100_000.0,
    )
    values.update(overrides)
    return RiskInput(**values)


def test_two_strategies_aggregate_same_spy():
    targets = aggregate_targets([request("A", 0.5), request("B", 0.25)], {"SPY": price()}, {}, 200_000)
    assert targets[0].target_quantity_fractional == pytest.approx(5.0)
    assert len(targets[0].contributions) == 2


def test_a_exit_does_not_clear_b_spy():
    current = {("A", "SPY"): 4.0, ("B", "SPY"): 2.0}
    target = aggregate_targets([request("A", 0.0), request("B", 0.30)], {"SPY": price()}, current, 200_000)[0]
    assert target.target_quantity_fractional == pytest.approx(2.0)
    assert target.proposed_net_change == pytest.approx(-4.0)
    assert target.contributions[1].current_quantity == 2.0


def test_fractional_and_whole_share_estimates():
    target = aggregate_targets([request("A", 0.5)], {"SPY": price()}, {}, 100_000)[0]
    assert target.target_quantity_fractional == pytest.approx(50_000 / 15_000)
    assert target.target_quantity_whole == 3


def test_fx_conversion_and_timestamps():
    quote = price()
    target = aggregate_targets([request("A", 0.5)], {"SPY": quote}, {}, 100_000)[0]
    assert target.target_notional_jpy == 50_000
    assert target.price_usd * target.usdjpy * target.target_quantity_fractional == pytest.approx(50_000)
    assert target.data_timestamp == quote.market_timestamp
    assert target.fx_timestamp == quote.fx_timestamp


def test_short_target_is_forbidden():
    with pytest.raises(PortfolioError):
        aggregate_targets([request("A", -0.1)], {"SPY": price()}, {}, 100_000)


def test_strategy_cannot_exceed_own_budget():
    decision = evaluate_risk(risk_input(budget_usage_jpy={"A": 110_000}, budget_limits_jpy={"A": 100_000}), NOW)
    assert decision.status is RiskStatus.REJECTED
    assert "STRATEGY_BUDGET_EXCEEDED:A" in decision.reasons


def test_negative_strategy_cash_is_rejected():
    decision = evaluate_risk(risk_input(projected_cash_jpy={"A": -1}), NOW)
    assert "NEGATIVE_STRATEGY_CASH" in decision.reasons


def test_stale_market_data_is_rejected():
    decision = evaluate_risk(risk_input(market_timestamp=NOW - timedelta(days=5)), NOW)
    assert "DATA_STALE" in decision.reasons


def test_stale_fx_is_rejected():
    decision = evaluate_risk(risk_input(fx_timestamp=NOW - timedelta(days=5)), NOW)
    assert "FX_STALE" in decision.reasons


def test_kill_switch_rejects_proposal():
    decision = evaluate_risk(risk_input(kill_switch=True), NOW)
    assert decision.status is RiskStatus.REJECTED
    assert "KILL_SWITCHED" in decision.reasons


def test_clean_risk_input_only_approves_proposal():
    decision = evaluate_risk(risk_input(), NOW)
    assert decision.status is RiskStatus.APPROVED_FOR_PROPOSAL


def test_simulate_scope_requires_its_own_approval_and_kill_switch():
    approved = evaluate_risk(risk_input(), NOW, RiskScope.SIMULATE_SCOPE)
    blocked = evaluate_risk(risk_input(kill_switch=True), NOW, RiskScope.SIMULATE_SCOPE)
    assert approved.status is RiskStatus.APPROVED_FOR_SIMULATE
    assert approved.scope is RiskScope.SIMULATE_SCOPE
    assert blocked.status is RiskStatus.REJECTED
    assert "KILL_SWITCHED" in blocked.reasons
