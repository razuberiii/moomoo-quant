from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from .. import config
from ..run_manifest import resolve_current_run
from .ledger import ShadowLedger
from .models import MarketPrice, RiskInput, TargetRequest
from .portfolio import aggregate_targets
from .risk import evaluate_risk


def record_current_baseline_risk_preview(ledger: ShadowLedger) -> dict:
    result_dir, _ = resolve_current_run()
    current = pd.read_csv(result_dir / "trend_current_signal.csv").iloc[-1]
    signal_date = pd.Timestamp(current["signal_date"])
    timestamp = signal_date.tz_localize("America/New_York").tz_convert("UTC").to_pydatetime()
    prices = {
        symbol: MarketPrice(
            symbol=symbol,
            price_usd=float(current[f"{symbol}_usd_close"]),
            usdjpy=float(current["usdjpy"]),
            market_timestamp=timestamp,
            fx_timestamp=timestamp,
        )
        for symbol in ("SPY", "QQQ", "GLD", "IEF")
    }
    request = TargetRequest(
        strategy_id=config.TREND_STRATEGY_ID,
        strategy_version="1",
        signal_id=f"baseline:{signal_date.date().isoformat()}",
        allocated_capital_jpy=config.PORTFOLIO_CAPITAL_JPY,
        cash_jpy=config.PORTFOLIO_CAPITAL_JPY,
        target_weights={symbol: float(current[f"{symbol}_weight"]) for symbol in prices},
    )
    targets = aggregate_targets([request], prices, {}, config.PORTFOLIO_CAPITAL_JPY)
    snapshot_id = f"portfolio-baseline:{signal_date.date().isoformat()}"
    ledger.record_portfolio_targets(
        snapshot_id,
        config.PORTFOLIO_ID,
        config.PORTFOLIO_CAPITAL_JPY,
        config.PORTFOLIO_CAPITAL_JPY,
        targets,
    )
    decision = evaluate_risk(
        RiskInput(
            input_snapshot_id=snapshot_id,
            generated_at=datetime.now(timezone.utc),
            market_timestamp=timestamp,
            fx_timestamp=timestamp,
            strategy_versions_match=True,
            portfolio_snapshot_consistent=True,
            opend_healthy=False,
            kill_switch=True,
            duplicate_rebalance=False,
            duplicate_proposal=False,
            instrument_type="US_EQUITY_ETF",
            leverage=1.0,
            proposed_quantities={target.symbol: target.target_quantity_fractional for target in targets},
            budget_usage_jpy={config.TREND_STRATEGY_ID: sum(target.target_notional_jpy for target in targets)},
            budget_limits_jpy={config.TREND_STRATEGY_ID: config.PORTFOLIO_CAPITAL_JPY},
            projected_cash_jpy={config.TREND_STRATEGY_ID: 0.0},
            total_target_jpy=sum(target.target_notional_jpy for target in targets),
            portfolio_limit_jpy=config.PORTFOLIO_CAPITAL_JPY,
        )
    )
    ledger.record_risk_decision(decision)
    return {"targets": targets, "risk_decision": decision, "proposed_orders": []}
