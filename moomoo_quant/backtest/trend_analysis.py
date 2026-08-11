import json
import logging
from datetime import datetime

import pandas as pd

from .. import config
from ..strategies.jpy_multi_asset_trend import prepare_jpy_daily
from ..trend_data import load_cached_trend_history, load_trend_history
from ..run_manifest import publish_trend_run
from .trend_engine import run_static_equal_weight_backtest, run_trend_backtest

logger = logging.getLogger(__name__)


def _comparison_outputs(full: dict, static: dict) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    benchmarks = full["benchmarks"].set_index("date")
    comparison = pd.DataFrame(index=benchmarks.index)
    comparison["Strategy"] = full["equity_curve"].set_index("date")["equity_jpy"]
    comparison["SPY JPY"] = benchmarks["SPY_JPY"]
    comparison["QQQ JPY"] = benchmarks["QQQ_JPY"]
    comparison["Static Equal Weight"] = static["equity_curve"].set_index("date")["equity_jpy"]
    for column in list(comparison.columns):
        comparison[f"{column} Drawdown"] = comparison[column] / comparison[column].cummax() - 1
    comparison.index.name = "date"
    comparison = comparison.reset_index()

    stats_by_name = {
        "JPY Multi-Asset Trend v1": full["stats"],
        "SPY Buy & Hold JPY": full["benchmark_stats"]["SPY_JPY"],
        "QQQ Buy & Hold JPY": full["benchmark_stats"]["QQQ_JPY"],
        "Static Equal Weight": static["stats"],
    }
    performance = pd.DataFrame(
        [
            {
                "portfolio": name,
                "total_return": stats["total_return"],
                "cagr": stats["cagr"],
                "max_drawdown": stats["max_drawdown"],
                "sharpe": stats["sharpe"],
                "sortino": stats["sortino"],
                "volatility": stats["volatility"],
                "calmar": stats["calmar"],
            }
            for name, stats in stats_by_name.items()
        ]
    )

    daily_returns = comparison.set_index("date")[
        ["Strategy", "SPY JPY", "QQQ JPY", "Static Equal Weight"]
    ].pct_change().fillna(0.0)
    daily_returns["USDJPY"] = benchmarks["USDJPY"].pct_change().fillna(0.0)
    yearly = daily_returns.groupby(daily_returns.index.year).apply(lambda x: (1 + x).prod() - 1)
    yearly = yearly.reset_index(names="year")
    return comparison, performance, yearly


def _monthly_crisis_data(full: dict, daily: dict[str, pd.DataFrame]) -> pd.DataFrame:
    signals = full["signals"].copy()
    signals["month"] = pd.to_datetime(signals["signal_date"]).dt.to_period("M").astype(str)
    rename = {"usdjpy": "usd_jpy", "JPY_CASH_weight": "jpy_cash_weight"}
    for symbol in daily:
        lower = symbol.lower()
        rename.update(
            {
                f"{symbol}_momentum": f"{lower}_momentum",
                f"{symbol}_jpy_price": f"{lower}_jpy_price",
                f"{symbol}_sma": f"{lower}_sma",
                f"{symbol}_above_sma": f"{lower}_above_sma",
                f"{symbol}_eligible": f"{lower}_eligible",
                f"{symbol}_rank": f"{lower}_rank",
                f"{symbol}_weight": f"{lower}_weight",
            }
        )
    crisis = signals.rename(columns=rename)

    equity = full["equity_curve"].copy()
    equity["month"] = pd.to_datetime(equity["date"]).dt.to_period("M").astype(str)
    month_end = equity.groupby("month").tail(1)[["month", "equity_jpy"]].copy()
    month_end["strategy_return"] = month_end["equity_jpy"].pct_change()
    month_end = month_end.rename(columns={"equity_jpy": "portfolio_equity"})
    crisis = crisis.merge(month_end, on="month", how="left")

    for symbol in daily:
        lower = symbol.lower()
        actual = equity.groupby("month")[f"{symbol}_weight"].mean().rename(f"{lower}_holding_weight")
        asset = daily[symbol][["jpy_close"]].copy()
        asset["month"] = asset.index.to_period("M").astype(str)
        asset_month = asset.groupby("month").tail(1).set_index("month")["jpy_close"].pct_change()
        crisis = crisis.merge(actual, left_on="month", right_index=True, how="left")
        crisis = crisis.merge(asset_month.rename(f"{lower}_jpy_return"), left_on="month", right_index=True, how="left")
        crisis[f"{lower}_approx_contribution"] = (
            crisis[f"{lower}_holding_weight"] * crisis[f"{lower}_jpy_return"]
        )

    ordered = ["month"]
    for symbol in daily:
        lower = symbol.lower()
        ordered.extend(
            [
                f"{lower}_momentum",
                f"{lower}_jpy_price",
                f"{lower}_sma",
                f"{lower}_above_sma",
                f"{lower}_eligible",
                f"{lower}_rank",
                f"{lower}_weight",
                f"{lower}_holding_weight",
                f"{lower}_jpy_return",
                f"{lower}_approx_contribution",
            ]
        )
    ordered.extend(["jpy_cash_weight", "strategy_return", "portfolio_equity", "usd_jpy", "signal_date"])
    return crisis[ordered].sort_values("month").reset_index(drop=True)


def _monthly_returns(full: dict) -> pd.DataFrame:
    equity = full["equity_curve"][["date", "equity_jpy"]].copy()
    equity["date"] = pd.to_datetime(equity["date"])
    monthly = equity.groupby(equity["date"].dt.to_period("M")).tail(1).copy()
    monthly["return"] = monthly["equity_jpy"].pct_change()
    monthly["year"] = monthly["date"].dt.year
    monthly["month"] = monthly["date"].dt.month
    return monthly[["date", "year", "month", "return"]].reset_index(drop=True)


def _crisis_2022_summary(crisis: pd.DataFrame, daily: dict[str, pd.DataFrame]) -> dict:
    year = crisis[crisis["month"].str.startswith("2022-")].copy()
    worst = year.nsmallest(3, "strategy_return")

    def loss_dates(symbol: str) -> list[str]:
        values = crisis[["month", f"{symbol}_eligible"]].copy()
        values["previous"] = values[f"{symbol}_eligible"].shift(1).astype("boolean")
        return values[
            values["month"].str.startswith("2022-")
            & values["previous"].fillna(False)
            & ~values[f"{symbol}_eligible"]
        ]["month"].tolist()

    worst_details = []
    for _, row in worst.iterrows():
        holdings = {
            symbol.upper(): float(row[f"{symbol}_holding_weight"])
            for symbol in ("spy", "qqq", "gld", "ief")
            if float(row[f"{symbol}_holding_weight"]) > 0.01
        }
        worst_details.append(
            {"month": row["month"], "return": float(row["strategy_return"]), "holdings": holdings}
        )

    gld_months = year[year["gld_holding_weight"] > 0.01]
    ief_months = year[year["ief_holding_weight"] > 0.01]
    alignment_ok = all(
        bool((frame["fx_date"] <= frame.index).all() and (frame["exec_fx_date"] < frame.index).all())
        for frame in daily.values()
    )
    return {
        "year_return": float((1 + year["strategy_return"].dropna()).prod() - 1),
        "worst_months": worst_details,
        "spy_lost_eligible": loss_dates("spy"),
        "qqq_lost_eligible": loss_dates("qqq"),
        "gld_held_months": gld_months["month"].tolist(),
        "gld_average_return_when_held": float(gld_months["gld_jpy_return"].mean()) if not gld_months.empty else 0.0,
        "ief_held_months": ief_months["month"].tolist(),
        "ief_average_return_when_held": float(ief_months["ief_jpy_return"].mean()) if not ief_months.empty else 0.0,
        "alignment_audit_passed": alignment_ok,
        "implementation_assessment": (
            "2022 drawdown is consistent with the fixed monthly trend rules and lagged signals; "
            "the FX alignment audit found no future-data fill."
            if alignment_ok
            else "FX alignment audit failed; inspect implementation before interpreting results."
        ),
    }


def _save_dashboard_data(
    full: dict,
    static: dict,
    daily: dict[str, pd.DataFrame],
    assets: dict[str, pd.DataFrame],
    fx: pd.DataFrame,
) -> dict:
    comparison, performance, yearly = _comparison_outputs(full, static)
    crisis = _monthly_crisis_data(full, daily)
    monthly = _monthly_returns(full)
    summary_2022 = _crisis_2022_summary(crisis, daily)

    comparison.to_csv(config.RESULTS_DIR / "trend_comparison_equity.csv", index=False)
    performance.to_csv(config.RESULTS_DIR / "trend_performance_comparison.csv", index=False)
    yearly.to_csv(config.RESULTS_DIR / "trend_yearly_comparison.csv", index=False)
    monthly.to_csv(config.RESULTS_DIR / "trend_monthly_returns.csv", index=False)
    crisis.to_csv(config.RESULTS_DIR / "trend_crisis_monthly.csv", index=False)
    crisis[(crisis["month"] >= "2021-01") & (crisis["month"] <= "2023-12")].to_csv(
        config.RESULTS_DIR / "crisis_2022_monthly.csv", index=False
    )
    with (config.RESULTS_DIR / "crisis_2022_summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary_2022, handle, ensure_ascii=False, indent=2)

    prices = pd.DataFrame({"date": next(iter(daily.values())).index})
    prices = prices.set_index("date")
    for symbol, frame in daily.items():
        prices[f"{symbol}_jpy_price"] = frame["jpy_close"]
    prices["Strategy_equity"] = full["equity_curve"].set_index("date")["equity_jpy"]
    prices["USDJPY"] = daily["SPY"]["usdjpy"]
    prices.reset_index().to_csv(config.RESULTS_DIR / "trend_asset_prices_jpy.csv", index=False)

    metadata_rows = []
    for symbol, frame in assets.items():
        metadata_rows.append(
            {
                "series": symbol,
                "source": "moomoo OpenD AuType.QFQ",
                "start_date": frame["date"].min(),
                "end_date": frame["date"].max(),
                "observations": len(frame),
            }
        )
    metadata_rows.append(
        {
            "series": "USDJPY",
            "source": "Yahoo Finance JPY=X",
            "start_date": fx["date"].min(),
            "end_date": fx["date"].max(),
            "observations": len(fx),
        }
    )
    metadata = pd.DataFrame(metadata_rows)
    metadata["last_updated"] = datetime.now().isoformat(timespec="seconds")
    metadata["alignment"] = (
        "Signal close uses latest known same-or-prior FX close; execution open uses strictly prior FX close; no bfill."
    )
    metadata.to_csv(config.RESULTS_DIR / "trend_data_metadata.csv", index=False)
    return {
        "comparison": comparison,
        "performance": performance,
        "yearly": yearly,
        "crisis": crisis,
        "monthly": monthly,
        "summary_2022": summary_2022,
    }


def _run_trend_analysis(assets: dict[str, pd.DataFrame], fx: pd.DataFrame) -> dict:
    daily = prepare_jpy_daily(assets, fx)
    full = run_trend_backtest(daily, save=True)
    static = run_static_equal_weight_backtest(daily, save=True)

    robustness_rows = []
    for momentum in (9, 12, 15):
        for sma in (8, 10, 12):
            test = run_trend_backtest(daily, momentum_months=momentum, sma_months=sma)
            stats = test["stats"]
            robustness_rows.append(
                {
                    "momentum_months": momentum,
                    "sma_months": sma,
                    "cagr": stats["cagr"],
                    "max_drawdown": stats["max_drawdown"],
                    "sharpe": stats["sharpe"],
                    "total_return": stats["total_return"],
                }
            )
    robustness = pd.DataFrame(robustness_rows)
    robustness.to_csv(config.RESULTS_DIR / "trend_robustness.csv", index=False)

    development = run_trend_backtest(daily, end="2018-12-31")
    oos = run_trend_backtest(daily, start="2019-01-01")
    pd.DataFrame(
        [
            {"segment": "Development", **development["stats"]},
            {"segment": "Out of Sample", **oos["stats"]},
        ]
    ).to_csv(config.RESULTS_DIR / "trend_oos_performance.csv", index=False)

    latest_signal = full["signals"].iloc[-1].copy()
    pd.DataFrame([latest_signal]).to_csv(config.RESULTS_DIR / "trend_current_signal.csv", index=False)
    dashboard_data = _save_dashboard_data(full, static, daily, assets, fx)
    manifest = publish_trend_run(
        next(iter(daily.values())).index[0].date().isoformat(),
        next(iter(daily.values())).index[-1].date().isoformat(),
    )

    return {
        "full": full,
        "static": static,
        "development": development,
        "oos": oos,
        "robustness": robustness,
        "latest_signal": latest_signal,
        "dashboard": dashboard_data,
        "run_manifest": manifest,
        "daily": daily,
        "data_rows": {symbol: len(frame) for symbol, frame in daily.items()},
        "data_start": next(iter(daily.values())).index[0].date().isoformat(),
        "data_end": next(iter(daily.values())).index[-1].date().isoformat(),
    }


def run_full_trend_analysis() -> dict:
    assets, fx = load_trend_history()
    return _run_trend_analysis(assets, fx)


def run_cached_trend_analysis() -> dict:
    assets, fx = load_cached_trend_history()
    return _run_trend_analysis(assets, fx)
