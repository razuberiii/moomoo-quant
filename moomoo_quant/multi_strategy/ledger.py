from __future__ import annotations

import json
import hashlib
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .. import config
from .models import AggregatedTarget, LifecycleStage, ProposedOrder, RiskDecision, StrategyDefinition, jsonable
from ..costs import commission_usd


APPEND_ONLY_TABLES = (
    "lifecycle_events",
    "cash_ledger_entries",
    "signals",
    "signal_input_snapshots",
    "target_allocations",
    "virtual_fills",
    "equity_snapshots",
    "portfolio_snapshots",
    "aggregated_targets",
    "proposed_orders",
    "risk_decisions",
    "reconciliation_records",
    "migration_events",
    "shadow_run_events",
    "broker_order_records",
)


class ShadowLedger:
    """SQLite ledger containing only research and theoretical shadow records."""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def migrate(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS strategy_definitions (
                    strategy_id TEXT NOT NULL, version TEXT NOT NULL, name TEXT NOT NULL,
                    signal_currency TEXT NOT NULL, base_currency TEXT NOT NULL,
                    benchmark_ids_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY (strategy_id, version)
                );
                CREATE TABLE IF NOT EXISTS strategy_accounts (
                    account_id TEXT PRIMARY KEY, portfolio_id TEXT NOT NULL,
                    strategy_id TEXT NOT NULL, strategy_version TEXT NOT NULL,
                    allocated_capital_jpy REAL NOT NULL CHECK (allocated_capital_jpy >= 0),
                    cash_jpy REAL NOT NULL CHECK (cash_jpy >= 0), status TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE (portfolio_id, strategy_id, strategy_version)
                );
                CREATE TABLE IF NOT EXISTS lifecycle_events (
                    event_id TEXT PRIMARY KEY, strategy_id TEXT NOT NULL,
                    from_stage TEXT, to_stage TEXT NOT NULL, entered_at TEXT NOT NULL,
                    approved_by TEXT NOT NULL, evidence_run_id TEXT, notes TEXT,
                    rejection_reason TEXT
                );
                CREATE TABLE IF NOT EXISTS strategy_budgets (
                    budget_id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
                    allocated_capital_jpy REAL NOT NULL, effective_at TEXT NOT NULL,
                    config_source TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS backtest_runs (
                    run_id TEXT PRIMARY KEY, strategy_id TEXT NOT NULL,
                    strategy_version TEXT NOT NULL, status TEXT NOT NULL,
                    created_at TEXT NOT NULL, completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS run_manifests (
                    run_id TEXT PRIMARY KEY, manifest_json TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES backtest_runs(run_id)
                );
                CREATE TABLE IF NOT EXISTS signal_input_snapshots (
                    snapshot_id TEXT PRIMARY KEY, strategy_id TEXT NOT NULL,
                    data_json TEXT NOT NULL, captured_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS signals (
                    signal_id TEXT PRIMARY KEY, strategy_id TEXT NOT NULL,
                    strategy_version TEXT NOT NULL, signal_date TEXT NOT NULL,
                    input_snapshot_id TEXT NOT NULL, state TEXT NOT NULL,
                    explanation TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE (strategy_id, strategy_version, signal_date)
                );
                CREATE TABLE IF NOT EXISTS target_allocations (
                    allocation_id TEXT PRIMARY KEY, signal_id TEXT NOT NULL,
                    symbol TEXT NOT NULL, target_weight REAL NOT NULL,
                    target_notional_jpy REAL NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE (signal_id, symbol)
                );
                CREATE TABLE IF NOT EXISTS strategy_positions (
                    account_id TEXT NOT NULL, symbol TEXT NOT NULL, quantity REAL NOT NULL,
                    average_cost_usd REAL NOT NULL, last_updated TEXT NOT NULL,
                    PRIMARY KEY (account_id, symbol)
                );
                CREATE TABLE IF NOT EXISTS position_attributions (
                    snapshot_id TEXT NOT NULL, strategy_id TEXT NOT NULL, symbol TEXT NOT NULL,
                    quantity REAL NOT NULL, value_jpy REAL NOT NULL, weight REAL NOT NULL,
                    PRIMARY KEY (snapshot_id, strategy_id, symbol)
                );
                CREATE TABLE IF NOT EXISTS cash_ledger_entries (
                    entry_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, amount_jpy REAL NOT NULL,
                    entry_type TEXT NOT NULL, reference_id TEXT, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS virtual_fills (
                    fill_id TEXT PRIMARY KEY, signal_id TEXT NOT NULL, account_id TEXT NOT NULL,
                    symbol TEXT NOT NULL, side TEXT NOT NULL, quantity REAL NOT NULL,
                    execution_price_usd REAL NOT NULL, usdjpy REAL NOT NULL,
                    commission_jpy REAL NOT NULL, slippage_jpy REAL NOT NULL,
                    fx_cost_jpy REAL NOT NULL, filled_at TEXT NOT NULL,
                    UNIQUE (signal_id, account_id, symbol, side)
                );
                CREATE TABLE IF NOT EXISTS equity_snapshots (
                    snapshot_id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
                    market_date TEXT NOT NULL, equity_jpy REAL NOT NULL, cash_jpy REAL NOT NULL,
                    drawdown REAL NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE (account_id, market_date)
                );
                CREATE TABLE IF NOT EXISTS benchmark_definitions (
                    benchmark_id TEXT PRIMARY KEY, strategy_id TEXT NOT NULL,
                    name TEXT NOT NULL, base_currency TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS benchmark_snapshots (
                    benchmark_id TEXT NOT NULL, market_date TEXT NOT NULL,
                    equity_jpy REAL NOT NULL, PRIMARY KEY (benchmark_id, market_date)
                );
                CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                    snapshot_id TEXT PRIMARY KEY, portfolio_id TEXT NOT NULL,
                    equity_jpy REAL NOT NULL, cash_jpy REAL NOT NULL,
                    data_timestamp TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS aggregated_targets (
                    target_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL, symbol TEXT NOT NULL,
                    target_quantity REAL NOT NULL, whole_share_estimate INTEGER NOT NULL,
                    target_notional_jpy REAL NOT NULL, current_quantity REAL NOT NULL,
                    proposed_net_change REAL NOT NULL, contributions_json TEXT NOT NULL,
                    created_at TEXT NOT NULL, UNIQUE (snapshot_id, symbol)
                );
                CREATE TABLE IF NOT EXISTS proposed_orders (
                    proposed_order_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
                    portfolio_id TEXT NOT NULL, input_snapshot_id TEXT NOT NULL,
                    symbol TEXT NOT NULL, side TEXT NOT NULL,
                    theoretical_quantity REAL NOT NULL, whole_share_estimate INTEGER NOT NULL,
                    estimated_notional_jpy REAL NOT NULL,
                    estimated_commission_jpy REAL NOT NULL,
                    estimated_slippage_jpy REAL NOT NULL,
                    estimated_fx_cost_jpy REAL NOT NULL, status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS risk_decisions (
                    decision_id TEXT PRIMARY KEY, status TEXT NOT NULL,
                    reasons_json TEXT NOT NULL, checked_at TEXT NOT NULL,
                    input_snapshot_id TEXT NOT NULL, risk_policy_version TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reconciliation_records (
                    reconciliation_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL,
                    status TEXT NOT NULL, details_json TEXT NOT NULL, checked_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS migration_events (
                    migration_id TEXT PRIMARY KEY, event_type TEXT NOT NULL,
                    source TEXT NOT NULL, destination TEXT NOT NULL,
                    details_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS shadow_run_events (
                    event_id TEXT PRIMARY KEY, strategy_id TEXT NOT NULL,
                    reference_id TEXT NOT NULL, state TEXT NOT NULL,
                    details_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE (strategy_id, reference_id, state)
                );
                CREATE TABLE IF NOT EXISTS broker_order_records (
                    record_id TEXT PRIMARY KEY, rebalance_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL, strategy_id TEXT NOT NULL,
                    symbol TEXT NOT NULL, side TEXT NOT NULL, quantity REAL NOT NULL,
                    status TEXT NOT NULL, order_id TEXT, details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (idempotency_key, status)
                );
                """
            )
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (1, ?)",
                (now,),
            )
            for table in APPEND_ONLY_TABLES:
                conn.execute(
                    f"CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table} "
                    "BEGIN SELECT RAISE(ABORT, 'append-only table'); END"
                )
                conn.execute(
                    f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table} "
                    "BEGIN SELECT RAISE(ABORT, 'append-only table'); END"
                )

    def register_strategy(
        self,
        definition: StrategyDefinition,
        portfolio_id: str,
        allocated_capital_jpy: float,
        stage: LifecycleStage,
        status: str,
        evidence_run_id: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        account_id = f"{portfolio_id}:{definition.strategy_id}:v{definition.version}"
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO strategy_definitions VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    definition.strategy_id,
                    definition.version,
                    definition.name,
                    definition.signal_currency,
                    definition.base_currency,
                    json.dumps(definition.benchmark_ids),
                    now,
                ),
            )
            account_insert = conn.execute(
                "INSERT OR IGNORE INTO strategy_accounts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    account_id,
                    portfolio_id,
                    definition.strategy_id,
                    definition.version,
                    allocated_capital_jpy,
                    allocated_capital_jpy,
                    status,
                    now,
                    now,
                ),
            )
            if account_insert.rowcount == 1:
                conn.execute(
                    "INSERT INTO strategy_budgets VALUES (?, ?, ?, ?, ?)",
                    (f"budget:{account_id}:initial", account_id, allocated_capital_jpy, now, "config.py"),
                )
                conn.execute(
                    "INSERT INTO lifecycle_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        f"lifecycle:{definition.strategy_id}:{stage.value}:initial",
                        definition.strategy_id,
                        None,
                        stage.value,
                        now,
                        "USER_POLICY",
                        evidence_run_id,
                        "Initial explicit lifecycle registration",
                        None,
                    ),
                )

    def apply_admission_budget(self, admission: dict, runtime_status: str) -> bool:
        """Apply an immutable SHADOW admission to a previously empty account.

        This is intentionally one-way: it can fund a pristine research account,
        but it cannot resize an active strategy or withdraw its capital.
        """
        if admission.get("decision") != "SHADOW_READY" or not admission.get("immutable"):
            return False
        strategy_id = admission["strategy_id"]
        version = str(admission["strategy_version"])
        target = float(admission["allocated_capital_jpy"])
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            account = conn.execute(
                "SELECT * FROM strategy_accounts WHERE strategy_id=? AND strategy_version=?",
                (strategy_id, version),
            ).fetchone()
            if account is None:
                raise KeyError(f"Unknown strategy account: {strategy_id} v{version}")
            current = float(account["allocated_capital_jpy"])
            if abs(current - target) <= 1e-8:
                return False
            if current != 0.0:
                raise RuntimeError("Admission cannot resize an already funded strategy")
            if abs(float(account["cash_jpy"])) > 1e-8:
                raise RuntimeError("Cannot fund a research account with nonzero cash")
            activity = conn.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM strategy_positions WHERE account_id=?) +
                  (SELECT COUNT(*) FROM virtual_fills WHERE account_id=?)
                """,
                (account["account_id"], account["account_id"]),
            ).fetchone()[0]
            if activity:
                raise RuntimeError("Cannot fund a research account with existing position activity")
            conn.execute(
                """
                UPDATE strategy_accounts
                SET allocated_capital_jpy=?, cash_jpy=cash_jpy+?, status=?, updated_at=?
                WHERE account_id=?
                """,
                (target, target, runtime_status, now, account["account_id"]),
            )
            conn.execute(
                "INSERT OR IGNORE INTO strategy_budgets VALUES (?, ?, ?, ?, ?)",
                (
                    f"budget:{account['account_id']}:{admission['admission_id']}",
                    account["account_id"],
                    target,
                    now,
                    admission["admission_id"],
                ),
            )
            conn.execute(
                "INSERT OR IGNORE INTO cash_ledger_entries VALUES (?, ?, ?, ?, ?, ?)",
                (
                    f"cash:{account['account_id']}:{admission['admission_id']}",
                    account["account_id"],
                    target,
                    "ADMISSION_CAPITAL",
                    admission["admission_id"],
                    now,
                ),
            )
            conn.execute(
                "INSERT OR IGNORE INTO lifecycle_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"lifecycle:{strategy_id}:SHADOW:{admission['admission_id']}",
                    strategy_id,
                    LifecycleStage.RESEARCH.value,
                    LifecycleStage.SHADOW.value,
                    now,
                    "ADMISSION_POLICY",
                    admission["evidence_run_id"],
                    f"Funded by immutable admission {admission['admission_id']}",
                    None,
                ),
            )
        return True

    def record_migration(self, migration_id: str, details: dict) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO migration_events VALUES (?, ?, ?, ?, ?, ?)",
                (
                    migration_id,
                    "LEGACY_SHADOW_PRESERVED_READ_ONLY",
                    "shadow/*.csv",
                    "multi_strategy/shadow_ledger.sqlite3",
                    json.dumps(details, sort_keys=True),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return cursor.rowcount == 1

    def record_risk_decision(self, decision: RiskDecision) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO risk_decisions VALUES (?, ?, ?, ?, ?, ?)",
                (
                    decision.decision_id,
                    decision.status.value,
                    json.dumps(decision.reasons),
                    decision.checked_at.isoformat(),
                    decision.input_snapshot_id,
                    decision.risk_policy_version,
                ),
            )
            return cursor.rowcount == 1

    def record_portfolio_targets(
        self,
        snapshot_id: str,
        portfolio_id: str,
        equity_jpy: float,
        cash_jpy: float,
        targets: list[AggregatedTarget],
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        data_timestamp = min(target.data_timestamp for target in targets).isoformat()
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO portfolio_snapshots VALUES (?, ?, ?, ?, ?, ?)",
                (snapshot_id, portfolio_id, equity_jpy, cash_jpy, data_timestamp, now),
            )
            for target in targets:
                conn.execute(
                    "INSERT OR IGNORE INTO aggregated_targets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        f"target:{snapshot_id}:{target.symbol}",
                        snapshot_id,
                        target.symbol,
                        target.target_quantity_fractional,
                        target.target_quantity_whole,
                        target.target_notional_jpy,
                        target.current_quantity,
                        target.proposed_net_change,
                        json.dumps(jsonable(target.contributions), sort_keys=True),
                        now,
                    ),
                )

    def transition_lifecycle(
        self,
        strategy_id: str,
        current: LifecycleStage,
        target: LifecycleStage,
        approved_by: str,
        evidence_run_id: str,
        notes: str = "",
    ) -> None:
        if target in (LifecycleStage.SIMULATE, LifecycleStage.LIVE):
            raise PermissionError("SIMULATE and LIVE lifecycle stages are disabled")
        order = list(LifecycleStage)
        if order.index(target) != order.index(current) + 1:
            raise ValueError("Lifecycle transitions must be explicit and sequential")
        now = datetime.now(timezone.utc).isoformat()
        event_id = f"lifecycle:{strategy_id}:{target.value}:{now}"
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO lifecycle_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (event_id, strategy_id, current.value, target.value, now, approved_by, evidence_run_id, notes, None),
            )

    def record_proposed_order(self, order: ProposedOrder) -> bool:
        values = jsonable(order)
        with self.connect() as conn:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO proposed_orders VALUES
                (:proposed_order_id, :idempotency_key, :portfolio_id, :input_snapshot_id,
                 :symbol, :side, :theoretical_quantity, :whole_share_estimate,
                 :estimated_notional_jpy, :estimated_commission_jpy,
                 :estimated_slippage_jpy, :estimated_fx_cost_jpy, :status, :created_at)""",
                values,
            )
            return cursor.rowcount == 1

    def record_signal(
        self,
        strategy_id: str,
        strategy_version: str,
        signal_date: str,
        inputs: dict,
        target_weights: dict[str, float],
        account_equity_jpy: float,
        explanation: str,
    ) -> tuple[str, bool]:
        canonical = json.dumps(inputs, sort_keys=True, separators=(",", ":"), default=str)
        input_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        snapshot_id = f"signal-input:{strategy_id}:v{strategy_version}:{signal_date}:{input_hash[:16]}"
        signal_id = f"signal:{strategy_id}:v{strategy_version}:{signal_date}"
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO signal_input_snapshots VALUES (?, ?, ?, ?)",
                (snapshot_id, strategy_id, canonical, now),
            )
            cursor = conn.execute(
                "INSERT OR IGNORE INTO signals VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    signal_id,
                    strategy_id,
                    strategy_version,
                    signal_date,
                    snapshot_id,
                    "SIGNAL_RECORDED",
                    explanation,
                    now,
                ),
            )
            for symbol, weight in sorted(target_weights.items()):
                conn.execute(
                    "INSERT OR IGNORE INTO target_allocations VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        f"allocation:{signal_id}:{symbol}",
                        signal_id,
                        symbol,
                        float(weight),
                        account_equity_jpy * float(weight),
                        now,
                    ),
                )
        return signal_id, cursor.rowcount == 1

    def record_shadow_event(
        self, strategy_id: str, reference_id: str, state: str, details: dict | None = None
    ) -> bool:
        event_id = f"shadow-event:{strategy_id}:{reference_id}:{state}"
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO shadow_run_events VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    strategy_id,
                    reference_id,
                    state,
                    json.dumps(details or {}, sort_keys=True, default=str),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return cursor.rowcount == 1

    def strategy_account(self, strategy_id: str, strategy_version: str) -> dict:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM strategy_accounts WHERE strategy_id=? AND strategy_version=?",
                (strategy_id, strategy_version),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown strategy account: {strategy_id} v{strategy_version}")
            return dict(row)

    def pending_signals(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT s.* FROM signals s
                WHERE NOT EXISTS (SELECT 1 FROM virtual_fills f WHERE f.signal_id=s.signal_id)
                  AND NOT EXISTS (
                    SELECT 1 FROM shadow_run_events e
                    WHERE e.reference_id=s.signal_id AND e.state='VIRTUAL_FILLED'
                  )
                ORDER BY s.signal_date, s.strategy_id
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def target_weights(self, signal_id: str) -> dict[str, float]:
        with self.connect() as conn:
            return {
                row["symbol"]: float(row["target_weight"])
                for row in conn.execute(
                    "SELECT symbol, target_weight FROM target_allocations WHERE signal_id=?",
                    (signal_id,),
                )
            }

    def apply_virtual_rebalance(
        self,
        signal_id: str,
        strategy_id: str,
        strategy_version: str,
        fill_date: str,
        opens_usd: dict[str, float],
        usdjpy: float,
        slippage_bps: float,
        fx_cost_bps: float,
        quantity_step: float = config.OPERATIONAL_QUANTITY_STEP,
        fx_fee_jpy_per_usd: float = config.OPERATIONAL_FX_FEE_JPY_PER_USD,
    ) -> bool:
        account = self.strategy_account(strategy_id, strategy_version)
        weights = self.target_weights(signal_id)
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            already = conn.execute(
                "SELECT 1 FROM shadow_run_events WHERE reference_id=? AND state='VIRTUAL_FILLED'",
                (signal_id,),
            ).fetchone()
            if already:
                return False
            position_rows = conn.execute(
                "SELECT symbol, quantity, average_cost_usd FROM strategy_positions WHERE account_id=?",
                (account["account_id"],),
            ).fetchall()
            positions = {row["symbol"]: float(row["quantity"]) for row in position_rows}
            average_costs = {row["symbol"]: float(row["average_cost_usd"]) for row in position_rows}
            cash = float(account["cash_jpy"])
            symbols = sorted(set(weights) | set(positions))
            pre_equity = cash + sum(
                positions.get(symbol, 0.0) * opens_usd[symbol] * usdjpy for symbol in symbols
            )
            desired = {
                symbol: pre_equity * weights.get(symbol, 0.0) / (opens_usd[symbol] * usdjpy)
                for symbol in symbols
            }
            for side in ("SELL", "BUY"):
                for symbol in symbols:
                    current = positions.get(symbol, 0.0)
                    delta = desired[symbol] - current
                    if side == "SELL" and delta >= -1e-10:
                        continue
                    if side == "BUY" and delta <= 1e-10:
                        continue
                    raw_price = opens_usd[symbol]
                    slip = slippage_bps / 10_000
                    execution_price = raw_price * (1 - slip if side == "SELL" else 1 + slip)
                    quantity = min(abs(delta), current) if side == "SELL" else abs(delta)
                    if side == "BUY":
                        per_share = execution_price * usdjpy
                        quantity = min(quantity, cash / per_share if per_share > 0 else 0.0)
                        for _ in range(30):
                            notional_usd = quantity * execution_price
                            raw_jpy = quantity * raw_price * usdjpy
                            cost = (
                                notional_usd * usdjpy
                                + commission_usd(notional_usd) * usdjpy
                                + raw_jpy * fx_cost_bps / 10_000
                                + notional_usd * fx_fee_jpy_per_usd
                            )
                            if cost <= cash + 1e-8:
                                break
                            quantity *= 0.999
                    quantity = math.floor((quantity + 1e-12) / quantity_step) * quantity_step
                    if quantity <= 1e-10:
                        continue
                    notional_usd = quantity * execution_price
                    raw_notional_jpy = quantity * raw_price * usdjpy
                    commission_jpy = commission_usd(notional_usd) * usdjpy
                    slippage_jpy = quantity * abs(execution_price - raw_price) * usdjpy
                    fx_cost_jpy = (
                        raw_notional_jpy * fx_cost_bps / 10_000
                        + notional_usd * fx_fee_jpy_per_usd
                    )
                    fill_id = f"fill:{signal_id}:{symbol}:{side}"
                    conn.execute(
                        "INSERT INTO virtual_fills VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            fill_id, signal_id, account["account_id"], symbol, side, quantity,
                            execution_price, usdjpy, commission_jpy, slippage_jpy, fx_cost_jpy, now,
                        ),
                    )
                    if side == "SELL":
                        positions[symbol] = current - quantity
                        cash += notional_usd * usdjpy - commission_jpy - fx_cost_jpy
                    else:
                        new_quantity = current + quantity
                        average_costs[symbol] = (
                            current * average_costs.get(symbol, 0.0) + quantity * execution_price
                        ) / new_quantity
                        positions[symbol] = new_quantity
                        cash -= notional_usd * usdjpy + commission_jpy + fx_cost_jpy
                    conn.execute(
                        "INSERT INTO cash_ledger_entries VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            f"cash:{fill_id}", account["account_id"],
                            (notional_usd * usdjpy - commission_jpy - fx_cost_jpy) if side == "SELL" else -(notional_usd * usdjpy + commission_jpy + fx_cost_jpy),
                            f"VIRTUAL_{side}", fill_id, now,
                        ),
                    )
            for symbol in symbols:
                conn.execute(
                    """
                    INSERT INTO strategy_positions VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(account_id, symbol) DO UPDATE SET
                        quantity=excluded.quantity,
                        average_cost_usd=excluded.average_cost_usd,
                        last_updated=excluded.last_updated
                    """,
                    (account["account_id"], symbol, max(0.0, positions.get(symbol, 0.0)), average_costs.get(symbol, 0.0), now),
                )
            conn.execute(
                "UPDATE strategy_accounts SET cash_jpy=?, status=?, updated_at=? WHERE account_id=?",
                (max(0.0, cash), "VIRTUAL_FILLED", now, account["account_id"]),
            )
            conn.execute(
                "INSERT INTO shadow_run_events VALUES (?, ?, ?, ?, ?, ?)",
                (
                    f"shadow-event:{strategy_id}:{signal_id}:VIRTUAL_FILLED",
                    strategy_id, signal_id, "VIRTUAL_FILLED",
                    json.dumps({"fill_date": fill_date, "pre_equity_jpy": pre_equity}, sort_keys=True), now,
                ),
            )
        return True

    def record_equity(
        self,
        strategy_id: str,
        strategy_version: str,
        market_date: str,
        closes_usd: dict[str, float],
        usdjpy: float,
    ) -> bool:
        account = self.strategy_account(strategy_id, strategy_version)
        with self.connect() as conn:
            positions = conn.execute(
                "SELECT symbol, quantity FROM strategy_positions WHERE account_id=?",
                (account["account_id"],),
            ).fetchall()
            equity = float(account["cash_jpy"]) + sum(
                float(row["quantity"]) * closes_usd[row["symbol"]] * usdjpy for row in positions
            )
            peak_row = conn.execute(
                "SELECT MAX(equity_jpy) FROM equity_snapshots WHERE account_id=?",
                (account["account_id"],),
            ).fetchone()
            peak = max(equity, float(peak_row[0])) if peak_row[0] is not None else equity
            snapshot_id = f"equity:{account['account_id']}:{market_date}"
            cursor = conn.execute(
                "INSERT OR IGNORE INTO equity_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
                (snapshot_id, account["account_id"], market_date, equity, account["cash_jpy"], equity / peak - 1 if peak else 0.0, datetime.now(timezone.utc).isoformat()),
            )
            return cursor.rowcount == 1

    def reconcile(self, strategy_id: str, strategy_version: str, signal_id: str) -> bool:
        account = self.strategy_account(strategy_id, strategy_version)
        with self.connect() as conn:
            positions = [dict(row) for row in conn.execute(
                "SELECT symbol, quantity FROM strategy_positions WHERE account_id=?",
                (account["account_id"],),
            )]
            consistent = float(account["cash_jpy"]) >= -1e-8 and all(row["quantity"] >= -1e-10 for row in positions)
            reconciliation_id = f"reconcile:{signal_id}"
            cursor = conn.execute(
                "INSERT OR IGNORE INTO reconciliation_records VALUES (?, ?, ?, ?, ?)",
                (
                    reconciliation_id, signal_id, "RECONCILED" if consistent else "MISMATCH",
                    json.dumps({"account_id": account["account_id"], "positions": positions}, sort_keys=True),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        if consistent:
            self.record_shadow_event(strategy_id, signal_id, "RECONCILED")
        return cursor.rowcount == 1

    def record_portfolio_attribution(
        self,
        portfolio_id: str,
        market_date: str,
        closes_usd: dict[str, float],
        usdjpy: float,
    ) -> bool:
        snapshot_id = f"portfolio-equity:{portfolio_id}:{market_date}"
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            accounts = conn.execute(
                """
                SELECT a.* FROM strategy_accounts a
                WHERE a.portfolio_id=? AND a.allocated_capital_jpy > 0
                  AND EXISTS (
                    SELECT 1 FROM lifecycle_events l
                    WHERE l.strategy_id=a.strategy_id AND l.to_stage='SHADOW'
                  )
                """,
                (portfolio_id,),
            ).fetchall()
            cash = sum(float(account["cash_jpy"]) for account in accounts)
            parts = []
            position_value = 0.0
            for account in accounts:
                for row in conn.execute(
                    "SELECT symbol, quantity FROM strategy_positions WHERE account_id=?",
                    (account["account_id"],),
                ):
                    value = float(row["quantity"]) * closes_usd[row["symbol"]] * usdjpy
                    position_value += value
                    parts.append((account["strategy_id"], row["symbol"], row["quantity"], value))
            equity = cash + position_value
            cursor = conn.execute(
                "INSERT OR IGNORE INTO portfolio_snapshots VALUES (?, ?, ?, ?, ?, ?)",
                (snapshot_id, portfolio_id, equity, cash, market_date, now),
            )
            for strategy_id, symbol, quantity, value in parts:
                conn.execute(
                    "INSERT OR IGNORE INTO position_attributions VALUES (?, ?, ?, ?, ?, ?)",
                    (snapshot_id, strategy_id, symbol, quantity, value, value / equity if equity else 0.0),
                )
            return cursor.rowcount == 1

    def broker_order_by_key(self, idempotency_key: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM broker_order_records WHERE idempotency_key=? ORDER BY created_at DESC LIMIT 1",
                (idempotency_key,),
            ).fetchone()
            return dict(row) if row else None

    def record_broker_order_event(
        self,
        rebalance_id: str,
        idempotency_key: str,
        strategy_id: str,
        symbol: str,
        side: str,
        quantity: float,
        status: str,
        order_id: str | None,
        details: dict,
    ) -> bool:
        record_id = f"broker-order:{idempotency_key}:{status}"
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO broker_order_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record_id, rebalance_id, idempotency_key, strategy_id, symbol,
                    side, quantity, status, order_id,
                    json.dumps(details, sort_keys=True, default=str),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return cursor.rowcount == 1

    def rows(self, table: str) -> list[dict]:
        allowed = {
            "strategy_definitions", "strategy_accounts", "lifecycle_events",
            "migration_events", "risk_decisions", "proposed_orders",
            "signals", "signal_input_snapshots", "target_allocations",
            "strategy_positions", "virtual_fills", "equity_snapshots",
            "portfolio_snapshots", "aggregated_targets", "reconciliation_records",
            "shadow_run_events", "broker_order_records", "cash_ledger_entries",
        }
        if table not in allowed:
            raise ValueError(f"Table is not available for generic reads: {table}")
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
