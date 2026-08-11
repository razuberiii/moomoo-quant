from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import replace
from datetime import datetime, time, timezone

import exchange_calendars as xcals
import pandas as pd

from .. import config
from ..costs import commission_usd
from ..moomoo_client import get_market_snapshot
from ..multi_strategy.bootstrap import initialize_multi_strategy_ledger
from ..multi_strategy.execution import ProposedOrderService
from ..multi_strategy.models import MarketPrice, RiskInput, RiskScope, RiskStatus, TargetRequest
from ..multi_strategy.portfolio import aggregate_targets
from ..multi_strategy.risk import evaluate_risk
from ..trading.moomoo_simulate_adapter import MoomooSimulateExecutionAdapter, SimulateSafetyError
from ..trading.moomoo_simulate_gateway import OpenDSimulateGateway


CONFIRMATION = "SIMULATE_ONLY"
PORTFOLIO_ORDER_OWNER = "PORTFOLIO_MANAGER"


def _current_requests(ledger) -> list[TargetRequest]:
    trend = pd.read_csv(config.RESULTS_DIR / "trend_current_signal.csv").iloc[-1]
    factor = json.loads((config.RESULTS_DIR / "defensive_factor_v2_research.json").read_text(encoding="utf-8"))[
        "latest_target"
    ]
    parity = json.loads((config.RESULTS_DIR / "risk_parity_v1_research.json").read_text(encoding="utf-8"))[
        "latest_target"
    ]
    specifications = (
        (
            config.TREND_STRATEGY_ID,
            "1",
            str(trend["signal_date"]),
            {f"US.{symbol}": float(trend[f"{symbol}_weight"]) for symbol in ("SPY", "QQQ", "GLD", "IEF")},
        ),
        (
            config.DEFENSIVE_FACTOR_STRATEGY_ID,
            "2",
            str(factor["signal_date"]),
            {f"US.{symbol}": float(factor.get(f"{symbol}_weight", 0.0)) for symbol in ("QUAL", "USMV")},
        ),
        (
            config.RISK_PARITY_STRATEGY_ID,
            "1",
            str(parity["signal_date"]),
            {f"US.{symbol}": float(parity.get(f"{symbol}_weight", 0.0)) for symbol in ("SPY", "GLD", "IEF")},
        ),
    )
    requests = []
    for strategy_id, version, signal_date, weights in specifications:
        account = ledger.strategy_account(strategy_id, version)
        budget = float(account["allocated_capital_jpy"])
        if budget <= 0:
            continue
        requests.append(
            TargetRequest(
                strategy_id=strategy_id,
                strategy_version=version,
                signal_id=f"simulate-baseline:{strategy_id}:v{version}:{signal_date}",
                allocated_capital_jpy=budget,
                cash_jpy=budget,
                target_weights={symbol: weight for symbol, weight in weights.items() if weight > 0},
            )
        )
    return requests


def _latest_fx() -> tuple[float, datetime]:
    frame = pd.read_csv(config.DATA_DIR / config.FX_CACHE_NAME)
    row = frame.sort_values("date").iloc[-1]
    fx_date = pd.Timestamp(row["date"]).date()
    return float(row["close"]), datetime.combine(fx_date, time(21, 0), tzinfo=timezone.utc)


def _market_prices(symbols: list[str]) -> dict[str, MarketPrice]:
    snapshot = get_market_snapshot(symbols)
    if snapshot.empty:
        raise SimulateSafetyError("OpenD returned no market snapshots")
    usdjpy, fx_timestamp = _latest_fx()
    now = datetime.now(timezone.utc)
    output = {}
    for row in snapshot.to_dict("records"):
        symbol = str(row.get("code"))
        last_price = float(row.get("last_price", 0.0) or 0.0)
        if symbol not in symbols or not math.isfinite(last_price) or last_price <= 0:
            continue
        output[symbol] = MarketPrice(symbol, last_price, usdjpy, now, fx_timestamp)
    missing = sorted(set(symbols) - set(output))
    if missing:
        raise SimulateSafetyError(f"Missing valid OpenD snapshots: {', '.join(missing)}")
    return output


def _reserve_modeled_execution_costs(
    requests: list[TargetRequest],
    prices: dict[str, MarketPrice],
) -> list[TargetRequest]:
    """Leave enough strategy cash for the same costs used by net JPY replay."""
    output = []
    for request in requests:
        invested_jpy = request.allocated_capital_jpy * sum(request.target_weights.values())
        estimated_cost_jpy = 0.0
        for symbol, weight in request.target_weights.items():
            notional_jpy = request.allocated_capital_jpy * weight
            quote = prices[symbol]
            notional_usd = notional_jpy / quote.usdjpy
            estimated_cost_jpy += (
                commission_usd(notional_usd) * quote.usdjpy
                + notional_jpy * config.SLIPPAGE_BPS / 10_000
                + notional_usd * config.OPERATIONAL_FX_FEE_JPY_PER_USD
            )
        denominator = invested_jpy + estimated_cost_jpy
        scale = min(1.0, request.allocated_capital_jpy / denominator) if denominator > 0 else 1.0
        output.append(
            replace(
                request,
                target_weights={symbol: weight * scale for symbol, weight in request.target_weights.items()},
            )
        )
    return output


def _market_is_open(now: datetime | None = None) -> bool:
    current = pd.Timestamp(now or datetime.now(timezone.utc)).floor("min")
    if current.tzinfo is None:
        current = current.tz_localize("UTC")
    else:
        current = current.tz_convert("UTC")
    return bool(xcals.get_calendar("XNYS").is_open_on_minute(current))


def _activity_is_owned(preflight, ledger) -> bool:
    records = ledger.rows("broker_order_records")
    if not preflight.positions and not preflight.open_orders:
        return True
    if not records:
        return False
    positions_ok = all(row.get("code") in config.MOOMOO_SIMULATE_ALLOWED_SYMBOLS for row in preflight.positions)
    orders_ok = all(
        row.get("code") in config.MOOMOO_SIMULATE_ALLOWED_SYMBOLS
        and str(row.get("remark") or "").startswith("mq:")
        for row in preflight.open_orders
    )
    return positions_ok and orders_ok


def _latest_broker_records(ledger) -> list[dict]:
    rows = sorted(ledger.rows("broker_order_records"), key=lambda row: row["created_at"])
    latest = {}
    for row in rows:
        latest[row["idempotency_key"]] = row
    return list(latest.values())


def run_preflight(gateway: OpenDSimulateGateway | None = None) -> dict:
    if not config.MOOMOO_SIMULATE_ENABLED:
        raise SimulateSafetyError("MOOMOO_SIMULATE_ENABLED must be true for OpenD paper-account access")
    ledger = initialize_multi_strategy_ledger(include_research_slots=True)
    gateway = gateway or OpenDSimulateGateway()
    preflight = gateway.preflight()
    owned = _activity_is_owned(preflight, ledger)
    snapshot_id = f"simulate-preflight:{datetime.now(timezone.utc).date().isoformat()}"
    details = {**preflight.public_dict(), "activity_owned_by_project": owned}
    ledger.record_simulate_reconciliation(snapshot_id, "PREFLIGHT_OK" if owned else "FOREIGN_ACTIVITY", details)
    return {"state": "PREFLIGHT_OK" if owned else "FOREIGN_ACTIVITY", **details}


def run_sync(gateway: OpenDSimulateGateway | None = None) -> dict:
    if not config.MOOMOO_SIMULATE_ENABLED:
        raise SimulateSafetyError("MOOMOO_SIMULATE_ENABLED must be true for OpenD paper-account access")
    ledger = initialize_multi_strategy_ledger(include_research_slots=True)
    gateway = gateway or OpenDSimulateGateway()
    adapter = MoomooSimulateExecutionAdapter(
        ledger,
        gateway,
        enabled=True,
        strategy_allowlist=frozenset({PORTFOLIO_ORDER_OWNER}),
    )
    synced = []
    for row in _latest_broker_records(ledger):
        if row.get("order_id"):
            submission = adapter.sync(row["idempotency_key"])
            synced.append({"idempotency_key": submission.idempotency_key, "status": submission.status.value})
    preflight = gateway.preflight()
    details = {**preflight.public_dict(), "synced_orders": synced}
    snapshot_id = f"simulate-sync:{datetime.now(timezone.utc).date().isoformat()}"
    ledger.record_simulate_reconciliation(snapshot_id, "SYNCED", details)
    return {"state": "SYNCED", **details}


def run_bootstrap(
    confirmation: str,
    gateway: OpenDSimulateGateway | None = None,
    *,
    now: datetime | None = None,
) -> dict:
    if confirmation != CONFIRMATION:
        raise SimulateSafetyError(f"Explicit --confirm {CONFIRMATION} is required")
    if not config.MOOMOO_SIMULATE_ENABLED:
        raise SimulateSafetyError("MOOMOO_SIMULATE_ENABLED must be true")
    if config.MOOMOO_SIMULATE_KILL_SWITCH:
        raise SimulateSafetyError("MOOMOO_SIMULATE_KILL_SWITCH is still true")
    ledger = initialize_multi_strategy_ledger(include_research_slots=True)
    gateway = gateway or OpenDSimulateGateway()
    preflight = gateway.preflight()
    if not _activity_is_owned(preflight, ledger):
        raise SimulateSafetyError("Paper account contains positions/orders not owned by this project")
    if preflight.open_orders:
        synced = run_sync(gateway)
        return {"state": "WAITING_OPEN_ORDERS", "open_orders": preflight.public_dict()["open_orders"], "sync": synced}
    current_time = now or datetime.now(timezone.utc)
    if not _market_is_open(current_time):
        details = {**preflight.public_dict(), "reason": "XNYS_CLOSED"}
        ledger.record_simulate_reconciliation(
            f"simulate-bootstrap:{current_time.date().isoformat()}", "WAITING_MARKET_OPEN", details
        )
        return {"state": "WAITING_MARKET_OPEN", **details}

    requests = _current_requests(ledger)
    symbols = sorted({symbol for request in requests for symbol in request.target_weights})
    prices = _market_prices(symbols)
    requests = _reserve_modeled_execution_costs(requests, prices)
    targets = aggregate_targets(
        requests,
        prices,
        {},
        sum(request.allocated_capital_jpy for request in requests),
    )
    broker_positions = {row["code"]: float(row["qty"]) for row in preflight.positions}
    targets = [
        replace(
            target,
            current_quantity=broker_positions.get(target.symbol, 0.0),
            current_value_jpy=broker_positions.get(target.symbol, 0.0) * target.price_usd * target.usdjpy,
            proposed_net_change=target.target_quantity_fractional - broker_positions.get(target.symbol, 0.0),
        )
        for target in targets
    ]
    target_digest = hashlib.sha256(
        "|".join(sorted(request.signal_id for request in requests)).encode("utf-8")
    ).hexdigest()[:12]
    snapshot_id = f"simulate-bootstrap:{current_time.date().isoformat()}:{target_digest}"
    budget_usage = {
        request.strategy_id: request.allocated_capital_jpy * sum(request.target_weights.values())
        for request in requests
    }
    budget_limits = {request.strategy_id: request.allocated_capital_jpy for request in requests}
    projected_cash = {
        request.strategy_id: request.allocated_capital_jpy - budget_usage[request.strategy_id]
        for request in requests
    }
    decision = evaluate_risk(
        RiskInput(
            input_snapshot_id=snapshot_id,
            generated_at=current_time,
            market_timestamp=min(value.market_timestamp for value in prices.values()),
            fx_timestamp=min(value.fx_timestamp for value in prices.values()),
            strategy_versions_match=True,
            portfolio_snapshot_consistent=True,
            opend_healthy=True,
            kill_switch=config.MOOMOO_SIMULATE_KILL_SWITCH,
            duplicate_rebalance=False,
            duplicate_proposal=False,
            instrument_type="US_EQUITY_ETF",
            leverage=max(sum(request.target_weights.values()) for request in requests),
            proposed_quantities={target.symbol: target.target_quantity_fractional for target in targets},
            budget_usage_jpy=budget_usage,
            budget_limits_jpy=budget_limits,
            projected_cash_jpy=projected_cash,
            total_target_jpy=sum(target.target_notional_jpy for target in targets),
            portfolio_limit_jpy=sum(budget_limits.values()),
            trading_calendar_ok=True,
            idempotency_ok=True,
            ledger_consistent=True,
            symbol_allowlist_ok=all(target.symbol in config.MOOMOO_SIMULATE_ALLOWED_SYMBOLS for target in targets),
            quantity_limits_ok=all(abs(target.proposed_net_change) <= 10_000 for target in targets),
            attribution_consistent=all(
                abs(sum(part.target_notional_jpy for part in target.contributions) - target.target_notional_jpy) <= 0.01
                for target in targets
            ),
        ),
        current_time,
        RiskScope.SIMULATE_SCOPE,
    )
    ledger.record_risk_decision(decision)
    ledger.record_portfolio_targets(
        snapshot_id,
        config.PORTFOLIO_ID,
        sum(budget_limits.values()),
        sum(projected_cash.values()),
        targets,
    )
    if decision.status is not RiskStatus.APPROVED_FOR_SIMULATE:
        return {"state": "RISK_REJECTED", "reasons": list(decision.reasons)}

    proposal_service = ProposedOrderService(ledger)
    proposals = [proposal_service.create(config.PORTFOLIO_ID, target, decision) for target in targets]
    proposals = [proposal for proposal in proposals if proposal is not None]
    adapter = MoomooSimulateExecutionAdapter(
        ledger,
        gateway,
        enabled=True,
        strategy_allowlist=frozenset({PORTFOLIO_ORDER_OWNER}),
    )
    submissions = []
    for proposal in sorted(proposals, key=lambda order: (order.side != "SELL", order.symbol)):
        submission = adapter.submit(
            proposal,
            decision,
            rebalance_id=snapshot_id,
            strategy_id=PORTFOLIO_ORDER_OWNER,
        )
        submissions.append(
            {
                "symbol": proposal.symbol,
                "side": proposal.side,
                "quantity": proposal.theoretical_quantity,
                "order_id": submission.order_id,
                "status": submission.status.value,
            }
        )
    state = "SUBMITTED" if submissions else "ALREADY_AT_TARGET"
    ledger.record_simulate_reconciliation(snapshot_id, state, {"submissions": submissions})
    return {"state": state, "snapshot_id": snapshot_id, "submissions": submissions}


def main() -> None:
    parser = argparse.ArgumentParser(description="Explicit Moomoo US paper-account runner")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true", help="Read-only account/position/order checks")
    mode.add_argument("--bootstrap", action="store_true", help="Build the current combined paper portfolio")
    mode.add_argument("--sync", action="store_true", help="Synchronize recorded paper orders and positions")
    parser.add_argument("--confirm", default="", help=f"Bootstrap requires the literal {CONFIRMATION}")
    args = parser.parse_args()
    if args.preflight:
        result = run_preflight()
    elif args.sync:
        result = run_sync()
    else:
        result = run_bootstrap(args.confirm)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
