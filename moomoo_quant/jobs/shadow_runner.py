from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd
from filelock import FileLock, Timeout

from .. import config
from ..backtest.risk_parity import risk_parity_signals
from ..logging_setup import setup_logging
from ..multi_strategy.bootstrap import initialize_multi_strategy_ledger
from ..multi_strategy.ledger import ShadowLedger
from ..multi_strategy.models import RiskInput, RiskScope, RiskStatus
from ..multi_strategy.risk import evaluate_risk
from ..strategies.jpy_multi_asset_trend import monthly_signals, prepare_jpy_daily
from ..trend_data import load_trend_history


logger = logging.getLogger(__name__)
NEW_YORK = ZoneInfo("America/New_York")
ACTIVATION_AFTER = {
    config.TREND_STRATEGY_ID: "2026-07-31",
    config.RISK_PARITY_STRATEGY_ID: "2026-07-31",
}


@dataclass(frozen=True)
class ShadowSignalCandidate:
    strategy_id: str
    strategy_version: str
    signal_date: str
    target_weights: dict[str, float]
    inputs: dict
    explanation: str


def _sessions(start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
    calendar = xcals.get_calendar("XNYS")
    values = calendar.sessions_in_range(start.normalize(), end.normalize())
    if values.tz is not None:
        values = values.tz_localize(None)
    return values.normalize()


def _last_completed_session(now: datetime, sessions: pd.DatetimeIndex) -> pd.Timestamp:
    local = now.astimezone(NEW_YORK)
    candidate = pd.Timestamp(local.date())
    if local.time() < time(18, 0):
        candidate -= pd.Timedelta(days=1)
    eligible = sessions[sessions <= candidate]
    if eligible.empty:
        raise RuntimeError("No completed XNYS session is available")
    return eligible[-1]


def _is_month_end(session: pd.Timestamp, sessions: pd.DatetimeIndex) -> bool:
    position = sessions.searchsorted(session)
    if position >= len(sessions) or sessions[position] != session:
        return False
    return position == len(sessions) - 1 or sessions[position + 1].month != session.month


def _next_session(session: pd.Timestamp, sessions: pd.DatetimeIndex) -> pd.Timestamp | None:
    position = sessions.searchsorted(session, side="right")
    return sessions[position] if position < len(sessions) else None


def _as_utc(date_value: object) -> datetime:
    date = pd.Timestamp(date_value).date()
    return datetime.combine(date, time(21, 0), tzinfo=timezone.utc)


def _normalize_daily(daily: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    output = {}
    for symbol, frame in daily.items():
        normalized = frame.copy()
        normalized.index = pd.DatetimeIndex(normalized.index).tz_localize(None).normalize()
        output[symbol] = normalized.sort_index()
    return output


def build_signal_candidates(daily: dict[str, pd.DataFrame]) -> list[ShadowSignalCandidate]:
    candidates: list[ShadowSignalCandidate] = []
    trend = monthly_signals(daily, config.TREND_MOMENTUM_MONTHS, config.TREND_SMA_MONTHS)
    for _, row in trend.iterrows():
        signal_date = pd.Timestamp(row["signal_date"]).date().isoformat()
        weights = {symbol: float(row[f"{symbol}_weight"]) for symbol in daily}
        inputs = {
            "config_hash": config.stable_config_hash({
                "momentum_months": config.TREND_MOMENTUM_MONTHS,
                "sma_months": config.TREND_SMA_MONTHS,
                "max_assets": config.TREND_MAX_ASSETS,
                "asset_weight": config.TREND_ASSET_WEIGHT,
            }),
            "selected": row["selected"],
            "jpy_cash_weight": float(row["JPY_CASH_weight"]),
            "weights": weights,
            "signal_date": signal_date,
        }
        candidates.append(
            ShadowSignalCandidate(
                config.TREND_STRATEGY_ID, "1", signal_date, weights, inputs,
                "Frozen JPY Trend v1 month-end signal; next-session virtual open only.",
            )
        )

    parity = risk_parity_signals(daily, config.RISK_PARITY_V1)
    for _, row in parity.iterrows():
        signal_date = pd.Timestamp(row["signal_date"]).date().isoformat()
        weights = {symbol: float(row.get(f"{symbol}_weight", 0.0)) for symbol in daily}
        inputs = {
            "config_hash": config.RISK_PARITY_V1_HASH,
            "data_valid": bool(row["data_valid"]),
            "retained_previous": bool(row["retained_previous"]),
            "jpy_cash_weight": float(row["JPY_CASH_weight"]),
            "weights": weights,
            "signal_date": signal_date,
        }
        candidates.append(
            ShadowSignalCandidate(
                config.RISK_PARITY_STRATEGY_ID, "1", signal_date, weights, inputs,
                "Frozen JPY Risk Parity v1 month-end signal; next-session virtual open only.",
            )
        )
    return candidates


def _portfolio_limit(ledger: ShadowLedger) -> float:
    with ledger.connect() as conn:
        value = conn.execute(
            "SELECT COALESCE(SUM(allocated_capital_jpy), 0) FROM strategy_accounts WHERE allocated_capital_jpy > 0"
        ).fetchone()[0]
    return float(value)


def _shadow_risk(
    ledger: ShadowLedger,
    account: dict,
    snapshot_id: str,
    market_timestamp: datetime,
    fx_timestamp: datetime,
    now: datetime,
    target_weights: dict[str, float],
    duplicate: bool = False,
) -> bool:
    allocation = float(account["allocated_capital_jpy"])
    decision = evaluate_risk(
        RiskInput(
            input_snapshot_id=snapshot_id,
            generated_at=now,
            market_timestamp=market_timestamp,
            fx_timestamp=fx_timestamp,
            strategy_versions_match=True,
            portfolio_snapshot_consistent=True,
            opend_healthy=False,
            kill_switch=True,
            duplicate_rebalance=duplicate,
            duplicate_proposal=False,
            instrument_type="US_EQUITY_ETF",
            leverage=sum(target_weights.values()),
            proposed_quantities={symbol: max(0.0, weight) for symbol, weight in target_weights.items()},
            budget_usage_jpy={account["strategy_id"]: allocation * sum(target_weights.values())},
            budget_limits_jpy={account["strategy_id"]: allocation},
            projected_cash_jpy={account["strategy_id"]: allocation * (1 - sum(target_weights.values()))},
            total_target_jpy=allocation * sum(target_weights.values()),
            portfolio_limit_jpy=_portfolio_limit(ledger),
            trading_calendar_ok=True,
            idempotency_ok=not duplicate,
            ledger_consistent=float(account["cash_jpy"]) >= -1e-8,
        ),
        now,
        RiskScope.SHADOW_SCOPE,
    )
    ledger.record_risk_decision(decision)
    if decision.status is RiskStatus.REJECTED:
        logger.warning("Shadow risk rejected %s: %s", account["strategy_id"], decision.reasons)
        return False
    return True


def run_shadow_cycle(
    ledger: ShadowLedger,
    daily: dict[str, pd.DataFrame],
    candidates: list[ShadowSignalCandidate],
    now: datetime,
    sessions: pd.DatetimeIndex | None = None,
    activation_after: dict[str, str] | None = None,
) -> dict:
    daily = _normalize_daily(daily)
    activation_after = activation_after or ACTIVATION_AFTER
    common = next(iter(daily.values())).index
    for frame in daily.values():
        common = common.intersection(frame.index)
    if common.empty:
        raise RuntimeError("No common market dates for Shadow Runner")
    sessions = sessions if sessions is not None else _sessions(common.min() - pd.Timedelta(days=10), pd.Timestamp(now.date()) + pd.Timedelta(days=40))
    sessions = pd.DatetimeIndex(sessions).tz_localize(None).normalize()
    expected = _last_completed_session(now, sessions)
    latest_market = common.max()
    data_complete = latest_market >= expected
    result = {
        "expected_market_date": expected.date().isoformat(),
        "latest_market_date": latest_market.date().isoformat(),
        "data_complete": data_complete,
        "signals_recorded": 0,
        "virtual_fills": 0,
        "equity_snapshots": 0,
        "reconciliations": 0,
        "state": "DATA_INCOMPLETE" if not data_complete else "NO_ACTION",
    }
    if not data_complete:
        return result

    eligible_candidates = sorted(candidates, key=lambda item: (item.signal_date, item.strategy_id))
    for candidate in eligible_candidates:
        signal_date = pd.Timestamp(candidate.signal_date)
        if candidate.signal_date <= activation_after.get(candidate.strategy_id, "9999-12-31"):
            continue
        if signal_date > expected or not _is_month_end(signal_date, sessions):
            continue
        account = ledger.strategy_account(candidate.strategy_id, candidate.strategy_version)
        if float(account["allocated_capital_jpy"]) <= 0:
            continue
        first_symbol = next(iter(candidate.target_weights))
        signal_frame = daily[first_symbol].loc[signal_date]
        if not _shadow_risk(
            ledger,
            account,
            f"risk-signal:{candidate.strategy_id}:{candidate.signal_date}",
            _as_utc(signal_date),
            _as_utc(signal_frame["fx_date"]),
            now,
            candidate.target_weights,
        ):
            continue
        signal_id, inserted = ledger.record_signal(
            candidate.strategy_id,
            candidate.strategy_version,
            candidate.signal_date,
            candidate.inputs,
            candidate.target_weights,
            float(account["allocated_capital_jpy"]),
            candidate.explanation,
        )
        ledger.record_shadow_event(candidate.strategy_id, signal_id, "WAITING_FOR_OPEN")
        result["signals_recorded"] += int(inserted)
        if inserted:
            result["state"] = "WAITING_FOR_OPEN"

    for signal in ledger.pending_signals():
        signal_date = pd.Timestamp(signal["signal_date"])
        fill_date = _next_session(signal_date, sessions)
        if fill_date is None or fill_date > expected or fill_date not in common:
            continue
        weights = ledger.target_weights(signal["signal_id"])
        account = ledger.strategy_account(signal["strategy_id"], signal["strategy_version"])
        first_symbol = next(iter(weights))
        fill_row = daily[first_symbol].loc[fill_date]
        if not _shadow_risk(
            ledger,
            account,
            f"risk-fill:{signal['signal_id']}:{fill_date.date().isoformat()}",
            _as_utc(fill_date),
            _as_utc(fill_row["exec_fx_date"]),
            now,
            weights,
        ):
            continue
        opens = {symbol: float(daily[symbol].at[fill_date, "open_price"]) for symbol in weights}
        inserted = ledger.apply_virtual_rebalance(
            signal["signal_id"], signal["strategy_id"], signal["strategy_version"],
            fill_date.date().isoformat(), opens, float(fill_row["exec_usdjpy"]),
            config.SLIPPAGE_BPS, config.RISK_PARITY_V1["fx_conversion_cost_bps"],
        )
        if inserted:
            result["virtual_fills"] += 1
            result["state"] = "VIRTUAL_FILLED"
        if ledger.reconcile(signal["strategy_id"], signal["strategy_version"], signal["signal_id"]):
            result["reconciliations"] += 1
            result["state"] = "RECONCILED"

    closes = {symbol: float(frame.at[expected, "close_price"]) for symbol, frame in daily.items()}
    fx = float(next(iter(daily.values())).at[expected, "usdjpy"])
    with ledger.connect() as conn:
        accounts = conn.execute(
            "SELECT strategy_id, strategy_version FROM strategy_accounts WHERE allocated_capital_jpy > 0"
        ).fetchall()
    for account in accounts:
        result["equity_snapshots"] += int(
            ledger.record_equity(
                account["strategy_id"], account["strategy_version"],
                expected.date().isoformat(), closes, fx,
            )
        )
    ledger.record_portfolio_attribution(
        config.PORTFOLIO_ID, expected.date().isoformat(), closes, fx
    )
    return result


def run_production_once(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    config.MULTI_STRATEGY_DIR.mkdir(parents=True, exist_ok=True)
    lock = FileLock(config.MULTI_STRATEGY_DIR / "shadow_runner.lock", timeout=0)
    try:
        with lock:
            logger.info("Shadow Runner started; broker execution remains disabled")
            # Update caches without republishing the frozen formal backtest run.
            # Forward accounting and immutable historical evidence are separate.
            assets, fx = load_trend_history()
            daily = prepare_jpy_daily(assets, fx)
            ledger = initialize_multi_strategy_ledger(include_research_slots=True)
            result = run_shadow_cycle(ledger, daily, build_signal_candidates(daily), now)
            logger.info("Shadow Runner result: %s", json.dumps(result, sort_keys=True))
            return result
    except Timeout as exc:
        raise RuntimeError("Another Shadow Runner instance holds the task lock") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Idempotent local Forward Shadow Runner")
    parser.add_argument("--run-once", action="store_true", help="Run one calendar-aware cycle")
    args = parser.parse_args()
    if not args.run_once:
        parser.error("Only --run-once is supported; this command is not a daemon")
    setup_logging()
    run_production_once()


if __name__ == "__main__":
    main()
