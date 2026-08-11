import argparse
import json
import logging
from pprint import pformat

from . import config
from .backtest.engine import run_backtest
from .backtest.trend_analysis import run_cached_trend_analysis, run_full_trend_analysis
from .backtest.mean_reversion_jpy import run_mean_reversion_jpy
from .backtest.research_suite import run_research_suite
from .data_loader import load_or_update_daily
from .daily import initialize_shadow_from_cache, run_daily
from .logging_setup import setup_logging
from .moomoo_client import MoomooApiError, get_market_snapshot

logger = logging.getLogger(__name__)


def cmd_quote_test(_args) -> None:
    try:
        df = get_market_snapshot(config.SYMBOLS)
    except MoomooApiError as exc:
        logger.error("%s", exc)
        raise

    cols = [
        "code",
        "name",
        "last_price",
        "open_price",
        "high_price",
        "low_price",
        "prev_close_price",
        "volume",
    ]
    available = [c for c in cols if c in df.columns]
    logger.info("Market snapshot:\n%s", df[available].to_string(index=False))


def cmd_update_data(_args) -> None:
    df = load_or_update_daily(config.BACKTEST_SYMBOL)
    logger.info(
        "%s rows loaded for %s: %s to %s",
        len(df),
        config.BACKTEST_SYMBOL,
        df["date"].iloc[0],
        df["date"].iloc[-1],
    )


def cmd_backtest(_args) -> None:
    df = load_or_update_daily(config.BACKTEST_SYMBOL)
    _, trades, stats, benchmark = run_backtest(df)
    diagnostics = stats.pop("diagnostics", {})
    logger.info("Backtest stats:\n%s", pformat(stats, sort_dicts=False))
    logger.info("Buy & Hold benchmark:\n%s", pformat(benchmark, sort_dicts=False))
    logger.info("Gross / net diagnostics:\n%s", pformat(diagnostics.get("gross_net", {}), sort_dicts=False))
    logger.info("Single trade diagnostics:\n%s", pformat(diagnostics.get("single_trade", {}), sort_dicts=False))
    logger.info(
        "Exit reason diagnostics:\n%s",
        diagnostics.get("exit_reasons").to_string(index=False)
        if diagnostics.get("exit_reasons") is not None
        else "",
    )
    logger.info(
        "Yearly diagnostics:\n%s",
        diagnostics.get("yearly").to_string(index=False)
        if diagnostics.get("yearly") is not None
        else "",
    )
    logger.info("Exposure diagnostics:\n%s", pformat(diagnostics.get("exposure", {}), sort_dicts=False))
    logger.info("MAE / MFE diagnostics:\n%s", pformat(diagnostics.get("mae_mfe", {}), sort_dicts=False))
    logger.info("Trades written: %s", len(trades))


def cmd_trend_backtest(_args) -> None:
    analysis = run_full_trend_analysis()
    full = analysis["full"]
    logger.info(
        "Trend data: %s to %s; rows=%s",
        analysis["data_start"],
        analysis["data_end"],
        analysis["data_rows"],
    )
    logger.info("JPY trend stats:\n%s", pformat(full["stats"], sort_dicts=False))
    logger.info("Benchmarks:\n%s", pformat(full["benchmark_stats"], sort_dicts=False))
    logger.info("Static equal weight stats:\n%s", pformat(analysis["static"]["stats"], sort_dicts=False))
    logger.info("Development stats:\n%s", pformat(analysis["development"]["stats"], sort_dicts=False))
    logger.info("OOS stats:\n%s", pformat(analysis["oos"]["stats"], sort_dicts=False))
    logger.info("Robustness matrix:\n%s", analysis["robustness"].to_string(index=False))
    logger.info("Latest signal:\n%s", analysis["latest_signal"].to_string())
    logger.info("No order was sent.")


def cmd_trend_backtest_cached(_args) -> None:
    analysis = run_cached_trend_analysis()
    logger.info(
        "Cached trend data: %s to %s; rows=%s",
        analysis["data_start"],
        analysis["data_end"],
        analysis["data_rows"],
    )
    logger.info("JPY trend stats:\n%s", pformat(analysis["full"]["stats"], sort_dicts=False))
    logger.info("Cached data only. No network request or order was sent.")


def cmd_daily(_args) -> None:
    state = run_daily()
    logger.info(
        "Daily result: market_date=%s status=%s equity_jpy=%.2f",
        state["last_market_date"],
        state["status"],
        state["current_equity_jpy"],
    )
    logger.info("Shadow accounting only. No securities order was sent.")


def cmd_shadow_init(_args) -> None:
    state = initialize_shadow_from_cache()
    logger.info(
        "Shadow state: market_date=%s status=%s equity_jpy=%.2f",
        state["last_market_date"],
        state["status"],
        state["current_equity_jpy"],
    )
    logger.info("Cached data and local theoretical accounting only. No order was sent.")


def cmd_mean_reversion_research(_args) -> None:
    result = run_mean_reversion_jpy()
    logger.info("Mean Reversion JPY research:\n%s", pformat(result["summary"], sort_dicts=False))
    logger.info("Research only. No broker context was opened and no order was sent.")


def cmd_research_suite(_args) -> None:
    result = run_research_suite()
    logger.info("Quality & Low Volatility v2:\n%s", pformat(result["defensive_factor"]["summary"], sort_dicts=False))
    logger.info("Risk Parity v1:\n%s", pformat(result["risk_parity"]["summary"], sort_dicts=False))
    logger.info("Correlation matrix:\n%s", result["correlation"].to_string())
    logger.info("Operational NET replay:\n%s", pformat(result["operational"], sort_dicts=False))
    logger.info("Research only. No broker or trading context was opened.")


def cmd_multi_strategy_init(_args) -> None:
    from .multi_strategy.bootstrap import initialize_multi_strategy_ledger
    from .multi_strategy.preview import record_current_baseline_risk_preview

    research_path = config.RESULTS_DIR / "mean_reversion_jpy_research.json"
    status = "RESEARCH_PENDING"
    if research_path.exists():
        status = json.loads(research_path.read_text(encoding="utf-8")).get("status", status)
    manifest_path = config.RESULTS_DIR / "current_run.json"
    run_id = None
    if manifest_path.exists():
        run_id = json.loads(manifest_path.read_text(encoding="utf-8")).get("run_id")
    ledger = initialize_multi_strategy_ledger(
        trend_run_id=run_id,
        mean_reversion_status=status,
        include_research_slots=True,
    )
    preview = record_current_baseline_risk_preview(ledger)
    logger.info("Multi-strategy theory-only ledger initialized at %s", ledger.path)
    logger.info("Baseline risk preview: %s; no proposed order created", preview["risk_decision"].status.value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Moomoo OpenAPI personal quant experiment")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("quote-test", help="Test OpenD quote connection").set_defaults(func=cmd_quote_test)
    sub.add_parser("update-data", help="Download or incrementally update SPY daily data").set_defaults(func=cmd_update_data)
    sub.add_parser("backtest", help="Run SPY mean-reversion backtest").set_defaults(func=cmd_backtest)
    sub.add_parser("trend-backtest", help="Run JPY multi-asset trend analysis without ordering").set_defaults(func=cmd_trend_backtest)
    sub.add_parser("trend-backtest-cached", help="Rebuild trend results from local cache only").set_defaults(func=cmd_trend_backtest_cached)
    sub.add_parser("shadow-init", help="Initialize or refresh the shadow account from local cache").set_defaults(func=cmd_shadow_init)
    sub.add_parser("mean-reversion-research", help="Run fixed JPY Mean Reversion v1 research").set_defaults(func=cmd_mean_reversion_research)
    sub.add_parser("research-suite", help="Run frozen Robot B v2 and Robot C v1 research").set_defaults(func=cmd_research_suite)
    sub.add_parser("multi-strategy-init", help="Initialize the broker-disconnected SQLite shadow ledger").set_defaults(func=cmd_multi_strategy_init)
    sub.add_parser("daily", help="Update market data, dashboard results, and local shadow account").set_defaults(func=cmd_daily)
    return parser


def main() -> None:
    setup_logging()
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
