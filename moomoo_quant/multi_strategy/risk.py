from __future__ import annotations

from datetime import datetime, timezone

from .. import config
from .models import RiskDecision, RiskInput, RiskScope, RiskStatus


def evaluate_risk(
    input_data: RiskInput,
    now: datetime | None = None,
    scope: RiskScope = RiskScope.PROPOSAL_SCOPE,
) -> RiskDecision:
    checked_at = now or datetime.now(timezone.utc)
    reasons: list[str] = []
    age_market = checked_at - input_data.market_timestamp
    age_fx = checked_at - input_data.fx_timestamp

    if scope is RiskScope.EXECUTION_SCOPE:
        reasons.append("EXECUTION_DISABLED")
    if age_market.total_seconds() > 4 * 24 * 3600:
        reasons.append("DATA_STALE")
    if age_fx.total_seconds() > 4 * 24 * 3600:
        reasons.append("FX_STALE")
    if not input_data.trading_calendar_ok:
        reasons.append("TRADING_CALENDAR_INVALID")
    if not input_data.strategy_versions_match:
        reasons.append("STRATEGY_VERSION_MISMATCH")
    if not input_data.portfolio_snapshot_consistent:
        reasons.append("PORTFOLIO_SNAPSHOT_MISMATCH")
    if input_data.duplicate_rebalance or not input_data.idempotency_ok:
        reasons.append("DUPLICATE_REBALANCE")
    if not input_data.ledger_consistent:
        reasons.append("LEDGER_INCONSISTENT")
    if input_data.instrument_type != "US_EQUITY_ETF":
        reasons.append("INSTRUMENT_NOT_ALLOWED")
    if input_data.leverage > 1.0:
        reasons.append("LEVERAGE_FORBIDDEN")
    if any(quantity < -1e-10 for quantity in input_data.proposed_quantities.values()):
        reasons.append("SHORT_POSITION_FORBIDDEN")
    if any(value < -1e-8 for value in input_data.projected_cash_jpy.values()):
        reasons.append("NEGATIVE_STRATEGY_CASH")
    for strategy_id, used in input_data.budget_usage_jpy.items():
        if used > input_data.budget_limits_jpy.get(strategy_id, 0.0) + 1e-8:
            reasons.append(f"STRATEGY_BUDGET_EXCEEDED:{strategy_id}")
    if input_data.total_target_jpy > input_data.portfolio_limit_jpy + 1e-8:
        reasons.append("PORTFOLIO_LIMIT_EXCEEDED")

    if scope in (RiskScope.PROPOSAL_SCOPE, RiskScope.SIMULATE_SCOPE):
        if input_data.kill_switch:
            reasons.append("KILL_SWITCHED")
        if not input_data.opend_healthy:
            reasons.append("OPEND_UNHEALTHY")
        if input_data.duplicate_proposal:
            reasons.append("DUPLICATE_PROPOSAL")
        if not input_data.symbol_allowlist_ok:
            reasons.append("SYMBOL_NOT_ALLOWED")
        if not input_data.quantity_limits_ok:
            reasons.append("QUANTITY_LIMIT_EXCEEDED")
        if not input_data.attribution_consistent:
            reasons.append("ATTRIBUTION_MISMATCH")

    if reasons:
        status = RiskStatus.REJECTED
    elif scope is RiskScope.SHADOW_SCOPE:
        status = RiskStatus.APPROVED_FOR_SHADOW
    elif scope is RiskScope.SIMULATE_SCOPE:
        status = RiskStatus.APPROVED_FOR_SIMULATE
    else:
        status = RiskStatus.APPROVED_FOR_PROPOSAL
    return RiskDecision(
        decision_id=f"risk:{input_data.input_snapshot_id}:{scope.value}:{config.RISK_POLICY_VERSION}",
        status=status,
        reasons=tuple(reasons),
        checked_at=checked_at,
        input_snapshot_id=input_data.input_snapshot_id,
        risk_policy_version=config.RISK_POLICY_VERSION,
        scope=scope,
    )
