from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .. import config
from ..multi_strategy.ledger import ShadowLedger
from ..multi_strategy.models import BrokerOrderStatus, ProposedOrder, RiskDecision, RiskScope, RiskStatus


class SimulateExecutionDisabled(RuntimeError):
    pass


class SimulateSafetyError(RuntimeError):
    pass


SIMULATE_ENVIRONMENT = "SIMULATE"


class SimulateGateway(Protocol):
    def submit_simulated_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        trading_environment: object,
        idempotency_key: str,
    ) -> dict:
        """Submit through a caller-supplied fake or separately approved gateway."""

    def query_simulated_order(self, order_id: str) -> dict:
        """Read one paper order from the selected paper account."""


@dataclass(frozen=True)
class SimulateSubmission:
    order_id: str | None
    status: BrokerOrderStatus
    idempotency_key: str


class MoomooSimulateExecutionAdapter:
    """Explicit, SIMULATE-only submission boundary with ledger idempotency."""

    trading_environment = SIMULATE_ENVIRONMENT

    def __init__(
        self,
        ledger: ShadowLedger,
        gateway: SimulateGateway,
        *,
        enabled: bool | None = None,
        kill_switch: bool | None = None,
        requested_environment: object = SIMULATE_ENVIRONMENT,
        symbol_allowlist: frozenset[str] = config.MOOMOO_SIMULATE_ALLOWED_SYMBOLS,
        strategy_allowlist: frozenset[str] = frozenset(),
        maximum_quantity: float = 10_000.0,
    ):
        if requested_environment != SIMULATE_ENVIRONMENT:
            raise SimulateSafetyError("Only the fixed SIMULATE environment is accepted")
        self.ledger = ledger
        self.gateway = gateway
        self.enabled = config.MOOMOO_SIMULATE_ENABLED if enabled is None else enabled
        self.kill_switch = config.MOOMOO_SIMULATE_KILL_SWITCH if kill_switch is None else kill_switch
        self.symbol_allowlist = symbol_allowlist
        self.strategy_allowlist = strategy_allowlist
        self.maximum_quantity = maximum_quantity

    def submit(
        self,
        order: ProposedOrder,
        risk: RiskDecision,
        *,
        rebalance_id: str,
        strategy_id: str,
    ) -> SimulateSubmission:
        if not self.enabled:
            raise SimulateExecutionDisabled(
                "MOOMOO_SIMULATE_ENABLED=false; separate user approval is required"
            )
        if self.kill_switch:
            raise SimulateExecutionDisabled(
                "MOOMOO_SIMULATE_KILL_SWITCH=true; paper submission is disabled"
            )
        if risk.scope is not RiskScope.SIMULATE_SCOPE or risk.status is not RiskStatus.APPROVED_FOR_SIMULATE:
            raise SimulateSafetyError("Portfolio and SIMULATE_SCOPE risk approval are required")
        if order.symbol not in self.symbol_allowlist:
            raise SimulateSafetyError(f"Symbol is not allowlisted: {order.symbol}")
        if strategy_id not in self.strategy_allowlist:
            raise SimulateSafetyError(f"Strategy is not allowlisted: {strategy_id}")
        if order.theoretical_quantity <= 0 or order.theoretical_quantity > self.maximum_quantity:
            raise SimulateSafetyError("Order quantity is outside the configured limit")
        if not rebalance_id or not order.idempotency_key:
            raise SimulateSafetyError("rebalance_id and idempotency key are mandatory")
        existing = self.ledger.broker_order_by_key(order.idempotency_key)
        if existing:
            return SimulateSubmission(
                existing["order_id"], BrokerOrderStatus(existing["status"]), order.idempotency_key
            )

        response = self.gateway.submit_simulated_order(
            symbol=order.symbol,
            side=order.side,
            quantity=order.theoretical_quantity,
            trading_environment=self.trading_environment,
            idempotency_key=order.idempotency_key,
        )
        status = BrokerOrderStatus(response.get("status", BrokerOrderStatus.REJECTED.value))
        order_id = response.get("order_id")
        self.ledger.record_broker_order_event(
            rebalance_id, order.idempotency_key, strategy_id, order.symbol,
            order.side, order.theoretical_quantity, status.value, order_id, response,
        )
        return SimulateSubmission(order_id, status, order.idempotency_key)

    def sync(self, idempotency_key: str) -> SimulateSubmission:
        existing = self.ledger.broker_order_by_key(idempotency_key)
        if not existing or not existing.get("order_id"):
            raise SimulateSafetyError("No submitted SIMULATE order is available for sync")
        response = self.gateway.query_simulated_order(str(existing["order_id"]))
        status = BrokerOrderStatus(response.get("status", BrokerOrderStatus.REJECTED.value))
        self.ledger.record_broker_order_event(
            existing["rebalance_id"],
            idempotency_key,
            existing["strategy_id"],
            existing["symbol"],
            existing["side"],
            float(existing["quantity"]),
            status.value,
            response.get("order_id") or existing["order_id"],
            response,
        )
        return SimulateSubmission(
            response.get("order_id") or existing["order_id"], status, idempotency_key
        )
