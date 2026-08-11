import hashlib
import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PERSISTENT_ROOT = Path(os.environ.get("MOOMOO_QUANT_DATA_ROOT", BASE_DIR))
DATA_DIR = PERSISTENT_ROOT / "data"
RESULTS_DIR = PERSISTENT_ROOT / "results"
LOG_DIR = PERSISTENT_ROOT / "logs"
SHADOW_DIR = PERSISTENT_ROOT / "shadow"
RUNS_DIR = RESULTS_DIR / "runs"
MULTI_STRATEGY_DIR = PERSISTENT_ROOT / "multi_strategy"
LEDGER_PATH = MULTI_STRATEGY_DIR / "shadow_ledger.sqlite3"

OPEND_HOST = "127.0.0.1"
OPEND_PORT = 11112

SYMBOLS = ["US.SPY", "US.QQQ", "US.AAPL"]
BACKTEST_SYMBOL = "US.SPY"
BACKTEST_START = "2018-01-01"
# Keep the offline configuration import free of OpenD SDK side effects.  The
# SDK accepts these exact string values and converts them to protocol enums at
# the quote-context boundary.
KLINE_TYPE = "K_DAY"
ADJUST_TYPE = "qfq"
MAX_KLINE_COUNT = 1000

INITIAL_CASH_USD = 700.0
MAX_POSITION_PCT = 0.25
ALLOW_FRACTIONAL_SHARES = True

# Operational replay / Forward Shadow execution assumptions.  Frozen research
# baselines keep their original parameters; these values are a separate,
# conservative broker-executable overlay used for all headline NET results.
OPERATIONAL_QUANTITY_STEP = 0.001
OPERATIONAL_FX_FEE_JPY_PER_USD = 0.25
OPERATIONAL_EXECUTION_PROFILE_VERSION = "moomoo-jp-auto-fx-fractional-v1"

SMA_WINDOW = 200
RSI_WINDOW = 5
ENTRY_RSI = 25.0
EXIT_RSI = 55.0
MAX_HOLDING_DAYS = 5
STOP_LOSS_PCT = 0.02

# Moomoo Japan US Stock/ETF Basic Course commission model.
COMMISSION_RATE = 0.00132
COMMISSION_CAP_USD = 22.00
COMMISSION_MIN_USD = 0.01
SLIPPAGE_BPS = 5.0
FX_CONVERSION_COST_BPS = 0.0
RISK_FREE_RATE = 0.0

TREND_SYMBOLS = ["US.SPY", "US.QQQ", "US.GLD", "US.IEF"]
TREND_START = "2007-01-01"
TREND_INITIAL_CASH_JPY = 100_000.0
TREND_MOMENTUM_MONTHS = 12
TREND_SMA_MONTHS = 10
TREND_MAX_ASSETS = 2
TREND_ASSET_WEIGHT = 0.50
FX_YAHOO_SYMBOL = "JPY=X"
FX_CACHE_NAME = "USDJPY_daily.csv"

SHADOW_INITIAL_CASH_JPY = 100_000.0
SHADOW_STRATEGY_VERSION = "JPY Multi-Asset Trend v1"

# Multi-strategy research portfolio. The legacy JPY 100,000 shadow account stays
# read-only and is not silently split. This is a separately identified portfolio.
PORTFOLIO_ID = "jpy-shadow-portfolio-v1"
PORTFOLIO_CAPITAL_JPY = 100_000.0
TREND_STRATEGY_ID = "jpy-multi-asset-trend"
MEAN_REVERSION_STRATEGY_ID = "us-etf-short-term-mean-reversion"
RISK_POLICY_VERSION = "shadow-proposal-simulate-v2"
BACKTEST_ENGINE_VERSION = "trend-engine-v1.0"
COST_MODEL_VERSION = "moomoo-jp-basic-v1"
ADMISSION_POLICY_VERSION = "strategy-admission-v1"
QUANT_ADMIN_MODE = os.environ.get("QUANT_ADMIN_MODE", "false").lower() == "true"
KILL_SWITCH = os.environ.get("QUANT_KILL_SWITCH", "true").lower() == "true"
MOOMOO_SIMULATE_ENABLED = os.environ.get("MOOMOO_SIMULATE_ENABLED", "false").lower() == "true"
MOOMOO_SIMULATE_KILL_SWITCH = os.environ.get("MOOMOO_SIMULATE_KILL_SWITCH", "true").lower() == "true"
MOOMOO_SIMULATE_AUTO_ENABLED = os.environ.get("MOOMOO_SIMULATE_AUTO_ENABLED", "false").lower() == "true"
MOOMOO_SIMULATE_ACC_ID = os.environ.get("MOOMOO_SIMULATE_ACC_ID", "").strip()
SIMULATE_OBSERVATION_DAYS = int(os.environ.get("SIMULATE_OBSERVATION_DAYS", "30"))
MOOMOO_SIMULATE_ALLOWED_SYMBOLS = frozenset(
    {"US.SPY", "US.QQQ", "US.GLD", "US.IEF", "US.QUAL", "US.USMV"}
)

# US ETF Short-Term Mean Reversion v1 pre-registered parameters. These values
# intentionally match the existing baseline and must not be tuned in v1.
MR_INITIAL_CASH_JPY = 100_000.0
MR_MAX_POSITION_PCT = 0.25
MR_SIGNAL_CURRENCY = "USD"
MR_BASE_CURRENCY = "JPY"
MR_FX_CONVERSION_COST_BPS = 10.0
MR_OOS_START = "2022-01-01"
MR_MIN_NET_EXPECTANCY = 0.0
MR_MIN_OOS_SHARPE = 0.0
MR_MAX_ABS_TREND_CORRELATION = 0.70


def stable_config_hash(parameters: dict) -> str:
    canonical = json.dumps(parameters, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# Robot B v2 was pre-registered before its first complete run. Nearby values are
# reported only as fragility diagnostics and never used to select this version.
STRESS_PULLBACK_STRATEGY_ID = "stress-pullback-mean-reversion"
STRESS_PULLBACK_V2 = {
    "version": "2",
    "asset_universe": ["SPY"],
    "signal_currency": "USD",
    "base_currency": "JPY",
    "stress_lookback_days": 3,
    "stress_return_threshold": -0.05,
    "rsi_window": 3,
    "entry_rsi_threshold": 15.0,
    "long_term_sma_days": 200,
    "position_size": 0.50,
    "bounce_exit_rsi": 50.0,
    "maximum_holding_days": 5,
    "stop_loss": -0.04,
    "reentry_cooldown_days": 10,
    "execution": "next_trading_day_open",
    "commission_model": COST_MODEL_VERSION,
    "slippage_bps": SLIPPAGE_BPS,
    "fx_conversion_cost_bps": 10.0,
    "cash_model": "convert_JPY_to_USD_on_buy_and_USD_to_JPY_on_sell",
    "initial_cash_jpy": 100_000.0,
    "oos_start": "2022-01-01",
    "acceptance": {
        "minimum_trades": 15,
        "maximum_trades": 100,
        "minimum_net_expectancy": 0.0,
        "minimum_oos_sharpe": 0.0,
        "maximum_abs_trend_correlation": 0.70,
        "minimum_positive_years": 3,
        "maximum_positive_year_concentration": 0.75,
    },
}
STRESS_PULLBACK_V2_HASH = stable_config_hash(STRESS_PULLBACK_V2)


# Robot C v1 is an unlevered JPY inverse-volatility allocation. The full sample
# result must not be used to alter these frozen values.
RISK_PARITY_STRATEGY_ID = "jpy-unlevered-risk-parity"
RISK_PARITY_V1 = {
    "version": "1",
    "asset_universe": ["SPY", "GLD", "IEF"],
    "cash_asset": "JPY_CASH",
    "base_currency": "JPY",
    "method": "inverse_volatility",
    "volatility_lookback_days": 63,
    "covariance_estimator": "sample_covariance_no_shrinkage",
    "maximum_asset_weight": 0.45,
    "minimum_asset_weight": 0.0,
    "target_volatility": None,
    "rebalance_threshold": 0.05,
    "cash_rule": "residual_or_invalid_data_only",
    "warmup_days": 63,
    "rebalance_frequency": "month_end",
    "execution": "next_common_trading_day_open",
    "commission_model": COST_MODEL_VERSION,
    "slippage_bps": SLIPPAGE_BPS,
    "fx_conversion_cost_bps": 10.0,
    "initial_cash_jpy": 100_000.0,
    "oos_start": "2019-01-01",
    "acceptance": {
        "minimum_net_cagr": 0.0,
        "minimum_oos_sharpe": 0.50,
        "maximum_abs_trend_correlation": 0.85,
        "minimum_positive_year_fraction": 0.60,
        "must_improve_spy_jpy_max_drawdown": True,
    },
}
RISK_PARITY_V1_HASH = stable_config_hash(RISK_PARITY_V1)


# Robot B v1 is deliberately a different edge from Robot A's time-series
# trend and Robot C's cross-asset inverse-volatility allocation.  It owns
# three transparent US equity factor ETFs at fixed weights and rebalances only
# at calendar quarter ends.  These values are frozen before the first run.
DEFENSIVE_FACTOR_STRATEGY_ID = "us-defensive-multifactor"
DEFENSIVE_FACTOR_SYMBOLS = ["US.QUAL", "US.VLUE", "US.USMV"]
DEFENSIVE_FACTOR_V1 = {
    "version": "1",
    "strategy_family": "long_only_equity_factor_premia",
    "asset_universe": ["QUAL", "VLUE", "USMV"],
    "target_weights": {"QUAL": 1 / 3, "VLUE": 1 / 3, "USMV": 1 / 3},
    "base_currency": "JPY",
    "signal_currency": "JPY",
    "rebalance_frequency": "calendar_quarter_end",
    "execution": "next_common_trading_day_open",
    "commission_model": COST_MODEL_VERSION,
    "slippage_bps": SLIPPAGE_BPS,
    "fx_conversion_cost_bps": 10.0,
    "initial_cash_jpy": 100_000.0,
    "oos_start": "2021-01-01",
    "acceptance": {
        "minimum_history_years": 10.0,
        "minimum_net_cagr": 0.0,
        "minimum_oos_sharpe": 0.50,
        "minimum_positive_year_fraction": 0.60,
        "maximum_annualized_turnover": 1.50,
        "maximum_cost_drag": 0.05,
        "maximum_abs_trend_correlation": 0.85,
        "maximum_abs_risk_parity_correlation": 0.90,
        "must_improve_spy_jpy_max_drawdown": True,
    },
}
DEFENSIVE_FACTOR_V1_HASH = stable_config_hash(DEFENSIVE_FACTOR_V1)


# v1 retained a value sleeve and failed only its pre-registered defensive
# drawdown gate.  v2 is a new, narrower quality/low-volatility mandate rather
# than a parameter search over v1.  It is frozen before its first run.
DEFENSIVE_FACTOR_V2 = {
    "version": "2",
    "strategy_family": "long_only_quality_low_volatility",
    "asset_universe": ["QUAL", "USMV"],
    "target_weights": {"QUAL": 0.50, "USMV": 0.50},
    "base_currency": "JPY",
    "signal_currency": "JPY",
    "rebalance_frequency": "calendar_half_year_end",
    "execution": "next_common_trading_day_open",
    "commission_model": COST_MODEL_VERSION,
    "slippage_bps": SLIPPAGE_BPS,
    "fx_conversion_cost_bps": 10.0,
    "initial_cash_jpy": 100_000.0,
    "oos_start": "2021-01-01",
    "acceptance": {
        "minimum_history_years": 10.0,
        "minimum_net_cagr": 0.0,
        "minimum_oos_sharpe": 0.50,
        "minimum_positive_year_fraction": 0.60,
        "maximum_annualized_turnover": 1.00,
        "maximum_cost_drag": 0.03,
        "maximum_abs_trend_correlation": 0.85,
        "maximum_abs_risk_parity_correlation": 0.90,
        "must_improve_spy_jpy_max_drawdown": True,
    },
}
DEFENSIVE_FACTOR_V2_HASH = stable_config_hash(DEFENSIVE_FACTOR_V2)
