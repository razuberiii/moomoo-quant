from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class LifecycleStage(str, Enum):
    RESEARCH = "RESEARCH"
    BACKTEST = "BACKTEST"
    SHADOW = "SHADOW"
    SIMULATE = "SIMULATE"
    LIVE = "LIVE"


class RiskStatus(str, Enum):
    APPROVED_FOR_SHADOW = "APPROVED_FOR_SHADOW"
    APPROVED_FOR_PROPOSAL = "APPROVED_FOR_PROPOSAL"
    REJECTED = "REJECTED"


class RiskScope(str, Enum):
    SHADOW_SCOPE = "SHADOW_SCOPE"
    PROPOSAL_SCOPE = "PROPOSAL_SCOPE"
    EXECUTION_SCOPE = "EXECUTION_SCOPE"


@dataclass(frozen=True)
class StrategyDefinition:
    strategy_id: str
    name: str
    version: str
    signal_currency: str
    base_currency: str = "JPY"
    benchmark_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class TargetRequest:
    strategy_id: str
    strategy_version: str
    signal_id: str
    allocated_capital_jpy: float
    cash_jpy: float
    target_weights: dict[str, float]


@dataclass(frozen=True)
class MarketPrice:
    symbol: str
    price_usd: float
    usdjpy: float
    market_timestamp: datetime
    fx_timestamp: datetime


@dataclass
class StrategyContribution:
    strategy_id: str
    symbol: str
    target_weight: float
    target_notional_jpy: float
    target_quantity_fractional: float
    target_quantity_whole: int
    current_quantity: float


@dataclass
class AggregatedTarget:
    symbol: str
    target_quantity_fractional: float
    target_quantity_whole: int
    target_notional_jpy: float
    current_quantity: float
    current_value_jpy: float
    proposed_net_change: float
    target_weight: float
    price_usd: float
    usdjpy: float
    data_timestamp: datetime
    fx_timestamp: datetime
    contributions: list[StrategyContribution] = field(default_factory=list)


@dataclass(frozen=True)
class RiskInput:
    input_snapshot_id: str
    generated_at: datetime
    market_timestamp: datetime
    fx_timestamp: datetime
    strategy_versions_match: bool
    portfolio_snapshot_consistent: bool
    opend_healthy: bool
    kill_switch: bool
    duplicate_rebalance: bool
    duplicate_proposal: bool
    instrument_type: str
    leverage: float
    proposed_quantities: dict[str, float]
    budget_usage_jpy: dict[str, float]
    budget_limits_jpy: dict[str, float]
    projected_cash_jpy: dict[str, float]
    total_target_jpy: float
    portfolio_limit_jpy: float
    trading_calendar_ok: bool = True
    idempotency_ok: bool = True
    ledger_consistent: bool = True
    symbol_allowlist_ok: bool = True
    quantity_limits_ok: bool = True
    attribution_consistent: bool = True


@dataclass(frozen=True)
class RiskDecision:
    decision_id: str
    status: RiskStatus
    reasons: tuple[str, ...]
    checked_at: datetime
    input_snapshot_id: str
    risk_policy_version: str
    scope: RiskScope = RiskScope.PROPOSAL_SCOPE


class BrokerOrderStatus(str, Enum):
    PROPOSED = "PROPOSED"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class ProposedOrder:
    proposed_order_id: str
    idempotency_key: str
    portfolio_id: str
    input_snapshot_id: str
    symbol: str
    side: str
    theoretical_quantity: float
    whole_share_estimate: int
    estimated_notional_jpy: float
    estimated_commission_jpy: float
    estimated_slippage_jpy: float
    estimated_fx_cost_jpy: float
    status: str
    created_at: datetime


def jsonable(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        value = asdict(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return value
