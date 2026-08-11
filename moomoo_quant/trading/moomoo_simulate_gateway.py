from __future__ import annotations

import hashlib
import math
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterator

import pandas as pd

from .. import config
from ..moomoo_client import MoomooApiError, ensure_opend_reachable
from ..multi_strategy.models import BrokerOrderStatus
from .moomoo_simulate_adapter import SIMULATE_ENVIRONMENT, SimulateSafetyError


@dataclass(frozen=True)
class SimulateAccount:
    account_id: int
    fingerprint: str
    account_type: str
    status: str


@dataclass(frozen=True)
class SimulatePreflight:
    account: SimulateAccount
    cash_usd: float
    total_assets_usd: float
    positions: tuple[dict, ...]
    open_orders: tuple[dict, ...]

    def public_dict(self) -> dict:
        return {
            "account_fingerprint": self.account.fingerprint,
            "account_type": self.account.account_type,
            "account_status": self.account.status,
            "cash_usd": self.cash_usd,
            "total_assets_usd": self.total_assets_usd,
            "positions": list(self.positions),
            "open_orders": list(self.open_orders),
        }


def _sdk():
    # Importing the SDK opens its local logging subsystem. Keep it isolated to
    # an explicitly invoked SIMULATE command, never Dashboard/research imports.
    from moomoo import (
        Currency,
        OpenSecTradeContext,
        OrderType,
        RET_OK,
        SimAccType,
        TimeInForce,
        TrdEnv,
        TrdMarket,
        TrdSide,
    )

    return {
        "Currency": Currency,
        "OpenSecTradeContext": OpenSecTradeContext,
        "OrderType": OrderType,
        "RET_OK": RET_OK,
        "SimAccType": SimAccType,
        "TimeInForce": TimeInForce,
        "TrdEnv": TrdEnv,
        "TrdMarket": TrdMarket,
        "TrdSide": TrdSide,
    }


def _require_ok(ret_code: int, payload, action: str, ret_ok: int):
    if ret_code != ret_ok:
        raise MoomooApiError(f"SIMULATE {action} failed: {payload}")
    return payload


def _fingerprint(account_id: int) -> str:
    digest = hashlib.sha256(str(account_id).encode("utf-8")).hexdigest()[:10]
    return f"sim-{digest}"


def _clean_records(frame: pd.DataFrame, columns: tuple[str, ...]) -> tuple[dict, ...]:
    if frame.empty:
        return ()
    records = []
    for row in frame.to_dict("records"):
        record = {}
        for column in columns:
            value = row.get(column)
            if pd.isna(value):
                value = None
            elif hasattr(value, "item"):
                value = value.item()
            record[column] = value
        records.append(record)
    return tuple(records)


class OpenDSimulateGateway:
    """The only production file allowed to reach OpenD trade APIs.

    Every account-scoped call supplies the fixed paper environment and the
    selected paper account id. No unlock method or live-environment selector is
    exposed by this class.
    """

    trading_environment = SIMULATE_ENVIRONMENT

    def __init__(
        self,
        configured_account_id: str | None = None,
        *,
        enabled: bool | None = None,
        kill_switch: bool | None = None,
    ):
        account_id = config.MOOMOO_SIMULATE_ACC_ID if configured_account_id is None else configured_account_id
        self.configured_account_id = account_id.strip()
        self.enabled = config.MOOMOO_SIMULATE_ENABLED if enabled is None else enabled
        self.kill_switch = config.MOOMOO_SIMULATE_KILL_SWITCH if kill_switch is None else kill_switch

    def _require_read_access(self) -> None:
        if not self.enabled:
            raise SimulateSafetyError("MOOMOO_SIMULATE_ENABLED=false; OpenD paper access is disabled")

    def _require_submit_access(self) -> None:
        self._require_read_access()
        if self.kill_switch:
            raise SimulateSafetyError("MOOMOO_SIMULATE_KILL_SWITCH=true; paper submission is disabled")

    @contextmanager
    def _context(self) -> Iterator[tuple[object, dict]]:
        ensure_opend_reachable()
        sdk = _sdk()
        context = sdk["OpenSecTradeContext"](
            filter_trdmarket=sdk["TrdMarket"].US,
            host=config.OPEND_HOST,
            port=config.OPEND_PORT,
        )
        try:
            yield context, sdk
        finally:
            context.close()

    def _select_account(self, context, sdk: dict) -> SimulateAccount:
        ret, data = context.get_acc_list()
        accounts = _require_ok(ret, data, "account discovery", sdk["RET_OK"])
        allowed_types = {sdk["SimAccType"].STOCK, sdk["SimAccType"].STOCK_AND_OPTION}
        candidates = []
        for row in accounts.to_dict("records"):
            if row.get("trd_env") != sdk["TrdEnv"].SIMULATE:
                continue
            if row.get("sim_acc_type") not in allowed_types:
                continue
            authorizations = row.get("trdmarket_auth") or []
            if sdk["TrdMarket"].US not in authorizations and "US" not in authorizations:
                continue
            if str(row.get("acc_status", "")).upper() != "ACTIVE":
                continue
            account_id = int(row["acc_id"])
            if self.configured_account_id and str(account_id) != self.configured_account_id:
                continue
            candidates.append(
                SimulateAccount(
                    account_id=account_id,
                    fingerprint=_fingerprint(account_id),
                    account_type=str(row.get("sim_acc_type")),
                    status=str(row.get("acc_status")),
                )
            )
        if len(candidates) != 1:
            hint = "Set MOOMOO_SIMULATE_ACC_ID to one US stock paper account." if len(candidates) > 1 else "Check that an active US stock paper account exists."
            raise SimulateSafetyError(
                f"Expected exactly one eligible US SIMULATE account; found {len(candidates)}. {hint}"
            )
        return candidates[0]

    def preflight(self) -> SimulatePreflight:
        self._require_read_access()
        with self._context() as (context, sdk):
            account = self._select_account(context, sdk)
            ret, assets = context.accinfo_query(
                trd_env=sdk["TrdEnv"].SIMULATE,
                acc_id=account.account_id,
                refresh_cache=True,
                currency=sdk["Currency"].USD,
            )
            assets = _require_ok(ret, assets, "account info", sdk["RET_OK"])
            ret, positions = context.position_list_query(
                trd_env=sdk["TrdEnv"].SIMULATE,
                acc_id=account.account_id,
                refresh_cache=True,
                position_market=sdk["TrdMarket"].US,
                currency=sdk["Currency"].USD,
            )
            positions = _require_ok(ret, positions, "positions", sdk["RET_OK"])
            ret, orders = context.order_list_query(
                trd_env=sdk["TrdEnv"].SIMULATE,
                acc_id=account.account_id,
                refresh_cache=True,
                order_market=sdk["TrdMarket"].US,
            )
            orders = _require_ok(ret, orders, "orders", sdk["RET_OK"])
        asset = assets.iloc[0].to_dict() if not assets.empty else {}
        active_statuses = {
            "UNSUBMITTED", "WAITING_SUBMIT", "SUBMITTING", "SUBMITTED",
            "FILLED_PART", "CANCELLING_PART", "CANCELLING_ALL", "TIMEOUT",
        }
        if not orders.empty:
            orders = orders[orders["order_status"].astype(str).isin(active_statuses)]
        return SimulatePreflight(
            account=account,
            cash_usd=float(asset.get("cash", 0.0) or 0.0),
            total_assets_usd=float(asset.get("total_assets", 0.0) or 0.0),
            positions=_clean_records(
                positions[positions["qty"].astype(float).abs() > 1e-10] if not positions.empty else positions,
                ("code", "qty", "can_sell_qty", "average_cost", "market_val", "position_side"),
            ),
            open_orders=_clean_records(
                orders,
                ("code", "trd_side", "order_status", "order_id", "qty", "price", "dealt_qty", "dealt_avg_price", "last_err_msg", "remark"),
            ),
        )

    def submit_simulated_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        trading_environment: object,
        idempotency_key: str,
    ) -> dict:
        self._require_submit_access()
        if trading_environment != SIMULATE_ENVIRONMENT:
            raise SimulateSafetyError("Gateway accepts only the fixed SIMULATE environment")
        if symbol not in config.MOOMOO_SIMULATE_ALLOWED_SYMBOLS:
            raise SimulateSafetyError(f"Symbol is not allowlisted: {symbol}")
        if side not in {"BUY", "SELL"}:
            raise SimulateSafetyError("Only long-position BUY and SELL are allowed")
        if (
            not math.isfinite(quantity)
            or quantity <= 0
            or quantity > 10_000
            or abs(quantity / config.OPERATIONAL_QUANTITY_STEP - round(quantity / config.OPERATIONAL_QUANTITY_STEP)) > 1e-7
        ):
            raise SimulateSafetyError("Quantity must be positive, bounded, and aligned to the configured step")
        if not idempotency_key:
            raise SimulateSafetyError("An idempotency key is mandatory")
        remark = f"mq:{idempotency_key[:20]}"
        with self._context() as (context, sdk):
            account = self._select_account(context, sdk)
            ret, recent = context.order_list_query(
                start=(date.today() - timedelta(days=7)).isoformat(),
                end=date.today().isoformat(),
                trd_env=sdk["TrdEnv"].SIMULATE,
                acc_id=account.account_id,
                refresh_cache=True,
                order_market=sdk["TrdMarket"].US,
            )
            recent = _require_ok(ret, recent, "recent orders", sdk["RET_OK"])
            matches = recent[recent["remark"] == remark] if not recent.empty else recent
            if not matches.empty:
                return _order_response(matches.iloc[-1].to_dict(), account)
            if side == "SELL":
                self._require_sellable_long_position(context, sdk, account, symbol, quantity)
            ret, data = context.place_order(
                price=0.01,
                qty=float(quantity),
                code=symbol,
                trd_side=sdk["TrdSide"].BUY if side == "BUY" else sdk["TrdSide"].SELL,
                order_type=sdk["OrderType"].MARKET,
                trd_env=sdk["TrdEnv"].SIMULATE,
                acc_id=account.account_id,
                remark=remark,
                time_in_force=sdk["TimeInForce"].DAY,
            )
            if ret != sdk["RET_OK"]:
                return {
                    "order_id": None,
                    "status": BrokerOrderStatus.REJECTED.value,
                    "error": str(data),
                    "account_fingerprint": account.fingerprint,
                }
        row = data.iloc[0].to_dict() if not data.empty else {}
        return _order_response(row, account)

    @staticmethod
    def _require_sellable_long_position(
        context,
        sdk: dict,
        account: SimulateAccount,
        symbol: str,
        quantity: float,
    ) -> None:
        ret, positions = context.position_list_query(
            code=symbol,
            trd_env=sdk["TrdEnv"].SIMULATE,
            acc_id=account.account_id,
            refresh_cache=True,
            position_market=sdk["TrdMarket"].US,
            currency=sdk["Currency"].USD,
        )
        positions = _require_ok(ret, positions, "sellable positions", sdk["RET_OK"])
        required_columns = {"code", "position_side", "can_sell_qty"}
        if positions.empty or not required_columns.issubset(positions.columns):
            raise SimulateSafetyError(f"No sellable LONG position is available for {symbol}")
        normalized_side = positions["position_side"].map(
            lambda value: str(value).upper().rsplit(".", 1)[-1]
        )
        long_rows = positions[(positions["code"] == symbol) & (normalized_side == "LONG")]
        if long_rows.empty:
            raise SimulateSafetyError(f"No sellable LONG position is available for {symbol}")
        available = float(pd.to_numeric(long_rows["can_sell_qty"], errors="coerce").fillna(0.0).sum())
        if available + 1e-9 < quantity:
            raise SimulateSafetyError(
                f"SELL would exceed the current sellable LONG quantity for {symbol}: "
                f"requested={quantity}, available={available}"
            )

    def query_simulated_order(self, order_id: str) -> dict:
        self._require_read_access()
        with self._context() as (context, sdk):
            account = self._select_account(context, sdk)
            ret, data = context.order_list_query(
                order_id=str(order_id),
                trd_env=sdk["TrdEnv"].SIMULATE,
                acc_id=account.account_id,
                refresh_cache=True,
                order_market=sdk["TrdMarket"].US,
            )
            data = _require_ok(ret, data, "order status", sdk["RET_OK"])
        if data.empty:
            raise MoomooApiError(f"SIMULATE order not found: {order_id}")
        return _order_response(data.iloc[-1].to_dict(), account)


def _order_response(row: dict, account: SimulateAccount) -> dict:
    return {
        "order_id": str(row.get("order_id")) if row.get("order_id") is not None else None,
        "status": _map_order_status(row.get("order_status")),
        "broker_status": str(row.get("order_status", "")),
        "dealt_qty": float(row.get("dealt_qty", 0.0) or 0.0),
        "dealt_avg_price": float(row.get("dealt_avg_price", 0.0) or 0.0),
        "last_err_msg": str(row.get("last_err_msg", "") or ""),
        "account_fingerprint": account.fingerprint,
    }


def _map_order_status(status: object) -> str:
    value = str(status or "")
    if value == "FILLED_ALL":
        return BrokerOrderStatus.FILLED.value
    if value in {"FILLED_PART", "CANCELLED_PART"}:
        return BrokerOrderStatus.PARTIALLY_FILLED.value
    if value in {"CANCELLED_ALL", "DELETED", "DISABLED", "FILL_CANCELLED"}:
        return BrokerOrderStatus.CANCELLED.value
    if value in {"SUBMIT_FAILED", "FAILED"}:
        return BrokerOrderStatus.REJECTED.value
    return BrokerOrderStatus.SUBMITTED.value
