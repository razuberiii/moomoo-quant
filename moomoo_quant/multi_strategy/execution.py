from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone

from .. import config
from ..costs import commission_usd
from .ledger import ShadowLedger
from .models import AggregatedTarget, ProposedOrder, RiskDecision, RiskStatus


class ExecutionDisabledError(RuntimeError):
    pass


class ProposedOrderService:
    """Creates theory-only records and has no broker SDK dependency."""

    def __init__(self, ledger: ShadowLedger):
        self.ledger = ledger

    def create(
        self,
        portfolio_id: str,
        target: AggregatedTarget,
        risk: RiskDecision,
    ) -> ProposedOrder | None:
        if risk.status not in (
            RiskStatus.APPROVED_FOR_PROPOSAL,
            RiskStatus.APPROVED_FOR_SIMULATE,
        ):
            return None
        delta = target.proposed_net_change
        if abs(delta) <= 1e-10:
            return None
        side = "BUY" if delta > 0 else "SELL"
        quantity = math.floor((abs(delta) + 1e-12) / config.OPERATIONAL_QUANTITY_STEP) * config.OPERATIONAL_QUANTITY_STEP
        if quantity <= 1e-10:
            return None
        unit_jpy = target.price_usd * target.usdjpy
        notional_jpy = quantity * unit_jpy
        notional_usd = quantity * target.price_usd
        key_source = f"{portfolio_id}|{risk.input_snapshot_id}|{target.symbol}|{side}|{quantity:.12f}"
        key = hashlib.sha256(key_source.encode()).hexdigest()
        order = ProposedOrder(
            proposed_order_id=f"proposal:{key[:20]}",
            idempotency_key=key,
            portfolio_id=portfolio_id,
            input_snapshot_id=risk.input_snapshot_id,
            symbol=target.symbol,
            side=side,
            theoretical_quantity=quantity,
            whole_share_estimate=math.floor(quantity),
            estimated_notional_jpy=notional_jpy,
            estimated_commission_jpy=commission_usd(notional_usd) * target.usdjpy,
            estimated_slippage_jpy=notional_jpy * config.SLIPPAGE_BPS / 10_000,
            estimated_fx_cost_jpy=notional_usd * config.OPERATIONAL_FX_FEE_JPY_PER_USD,
            status=(
                "SIMULATE_PROPOSED"
                if risk.status is RiskStatus.APPROVED_FOR_SIMULATE
                else "PROPOSED_ONLY"
            ),
            created_at=datetime.now(timezone.utc),
        )
        if self.ledger.record_proposed_order(order):
            return order
        return self.ledger.proposed_order_by_key(key)

    def execute(self, _order: ProposedOrder) -> None:
        raise ExecutionDisabledError(
            "Execution is disabled: this project persists proposed orders only."
        )
