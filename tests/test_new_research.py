import ast
import json

import numpy as np
import pandas as pd
import pytest

from moomoo_quant import config
from moomoo_quant.backtest.defensive_factor import defensive_factor_signals
from moomoo_quant.backtest.risk_parity import _capped_inverse_vol, risk_parity_signals
from moomoo_quant.multi_strategy.bootstrap import initialize_multi_strategy_ledger
from moomoo_quant.trend_data import load_cached_defensive_factor_history


def load(name):
    return json.loads((config.RESULTS_DIR / name).read_text(encoding="utf-8"))


def test_old_mean_reversion_first_result_is_unchanged():
    summary = load("mean_reversion_jpy_research.json")
    assert summary["status"] == "RESEARCH_REJECTED"
    assert summary["stats"]["gross_return"] == pytest.approx(0.1453, abs=0.001)
    assert summary["stats"]["net_return"] == pytest.approx(-0.1263, abs=0.001)
    assert summary["stats"]["trade_count"] == 192


def test_new_strategies_were_preregistered_with_stable_hashes():
    b = config.BASE_DIR / "docs" / "stress_pullback_mean_reversion_v2_preregistration.md"
    c = config.BASE_DIR / "docs" / "jpy_unlevered_risk_parity_v1_preregistration.md"
    assert b.exists() and c.exists()
    assert len(config.STRESS_PULLBACK_V2_HASH) == 64
    assert len(config.RISK_PARITY_V1_HASH) == 64
    assert load("stress_pullback_v2_first_result.json")["parameters_hash"] == config.STRESS_PULLBACK_V2_HASH
    assert load("risk_parity_v1_first_result.json")["parameters_hash"] == config.RISK_PARITY_V1_HASH


def test_factor_versions_are_preregistered_and_first_results_are_immutable():
    v1 = config.BASE_DIR / "docs" / "us_defensive_multifactor_v1_preregistration.md"
    v2 = config.BASE_DIR / "docs" / "us_quality_low_volatility_v2_preregistration.md"
    assert v1.exists() and v2.exists()
    assert load("defensive_factor_v1_first_result.json")["parameters_hash"] == config.DEFENSIVE_FACTOR_V1_HASH
    assert load("defensive_factor_v2_first_result.json")["parameters_hash"] == config.DEFENSIVE_FACTOR_V2_HASH


def test_b_v2_has_no_parameter_grid_or_winner_selection():
    source = (config.BASE_DIR / "backtest" / "stress_pullback.py").read_text(encoding="utf-8")
    assert "ParameterGrid" not in source
    stability = pd.read_csv(config.RESULTS_DIR / "stress_pullback_v2_stability.csv")
    assert not stability["selection_allowed"].any()
    assert len(stability) == 6


def test_c_v1_weights_are_long_only_unlevered_and_capped():
    signals = pd.read_csv(config.RESULTS_DIR / "risk_parity_v1_signals.csv")
    columns = ["SPY_weight", "GLD_weight", "IEF_weight"]
    assert (signals[columns] >= 0).all().all()
    assert (signals[columns] <= 0.45 + 1e-12).all().all()
    assert (signals[columns].sum(axis=1) <= 1 + 1e-12).all()


def test_inverse_vol_cap_is_deterministic():
    weights = _capped_inverse_vol(pd.Series({"SPY": 0.20, "GLD": 0.10, "IEF": 0.05}), 0.45)
    assert weights.sum() == pytest.approx(1.0)
    assert weights.max() <= 0.45


def test_rejected_research_budgets_stay_zero_and_passing_c_gets_shadow_budget(tmp_path):
    ledger = initialize_multi_strategy_ledger(tmp_path / "ledger.db", include_research_slots=True)
    accounts = {(row["strategy_id"], row["strategy_version"]): row for row in ledger.rows("strategy_accounts")}
    assert accounts[(config.STRESS_PULLBACK_STRATEGY_ID, "2")]["allocated_capital_jpy"] == 0
    assert accounts[(config.RISK_PARITY_STRATEGY_ID, "1")]["allocated_capital_jpy"] == 100_000
    assert accounts[(config.DEFENSIVE_FACTOR_STRATEGY_ID, "2")]["allocated_capital_jpy"] == 0


def test_only_shadow_robots_have_positive_budget(tmp_path):
    ledger = initialize_multi_strategy_ledger(tmp_path / "ledger.db", include_research_slots=True)
    funded = {row["strategy_id"] for row in ledger.rows("strategy_accounts") if row["allocated_capital_jpy"] > 0}
    assert funded == {
        config.TREND_STRATEGY_ID,
        config.RISK_PARITY_STRATEGY_ID,
    }


def test_b_v2_first_result_is_rejected_without_retuning():
    summary = load("stress_pullback_v2_first_result.json")
    assert summary["status"] == "RESEARCH_REJECTED"
    assert summary["stats"]["trade_count"] == 8
    assert summary["acceptance_gates"]["low_frequency_trade_count"] is False


def test_c_v1_first_result_passes_frozen_gates():
    summary = load("risk_parity_v1_first_result.json")
    assert summary["status"] == "SHADOW"
    assert all(summary["acceptance_gates"].values())
    assert summary["budget_jpy"] == 100_000


def test_three_strategy_correlation_matrix_exists():
    matrix = pd.read_csv(config.RESULTS_DIR / "strategy_correlation_matrix.csv", index_col=0)
    assert matrix.shape == (3, 3)
    assert (matrix.columns == matrix.index).all()
    assert "Robot B · Quality Low Vol v2" in matrix.index
    assert "Stress Pullback" not in " ".join(matrix.index)


def test_factor_v1_is_archived_and_v2_passes_all_frozen_gates():
    v1 = load("defensive_factor_v1_first_result.json")
    v2 = load("defensive_factor_v2_first_result.json")
    assert v1["status"] == "RESEARCH_REJECTED"
    assert v1["rejection_reasons"] == ["improves_spy_jpy_max_drawdown"]
    assert v1["budget_jpy"] == 0
    assert v2["status"] == "SHADOW"
    assert all(v2["acceptance_gates"].values())
    assert v2["budget_jpy"] == 100_000


def test_current_factor_v2_reproduction_is_rejected_without_retuning():
    current = load("defensive_factor_v2_research.json")
    assert current["status"] == "RESEARCH_REJECTED"
    assert current["budget_jpy"] == 0
    assert current["rejection_reasons"] == ["improves_spy_jpy_max_drawdown"]


def test_factor_v2_is_half_year_equal_weight_without_momentum():
    dates = pd.date_range("2025-01-02", "2026-08-10", freq="B")
    daily = {
        symbol: pd.DataFrame(index=dates)
        for symbol in config.DEFENSIVE_FACTOR_V2["asset_universe"]
    }
    signals = defensive_factor_signals(daily, config.DEFENSIVE_FACTOR_V2)
    assert set(pd.to_datetime(signals["signal_date"]).dt.month) <= {6, 12}
    assert (signals[["QUAL_weight", "USMV_weight"]] == 0.5).all().all()
    source = (config.BASE_DIR / "backtest" / "defensive_factor.py").read_text(encoding="utf-8")
    assert "momentum" not in source.lower()
    assert "ParameterGrid" not in source


def test_factor_v2_rejects_an_incomplete_half_year_month():
    partial = pd.date_range("2026-06-01", "2026-06-12", freq="B")
    complete = pd.date_range("2026-06-01", "2026-06-30", freq="B")
    partial_daily = {symbol: pd.DataFrame(index=partial) for symbol in ("QUAL", "USMV")}
    complete_daily = {symbol: pd.DataFrame(index=complete) for symbol in ("QUAL", "USMV")}

    assert defensive_factor_signals(partial_daily, config.DEFENSIVE_FACTOR_V2).empty
    signals = defensive_factor_signals(complete_daily, config.DEFENSIVE_FACTOR_V2)
    assert pd.Timestamp(signals.iloc[-1]["signal_date"]) == pd.Timestamp("2026-06-30")


def test_risk_parity_rejects_a_truncated_last_month():
    dates = pd.date_range("2026-01-02", "2026-08-07", freq="B")
    daily = {
        symbol: pd.DataFrame(
            {"jpy_close": 100.0 + offset + np.arange(len(dates))},
            index=dates,
        )
        for offset, symbol in enumerate(("SPY", "GLD", "IEF"))
    }
    signals = risk_parity_signals(daily, config.RISK_PARITY_V1)
    assert pd.Timestamp(signals.iloc[-1]["signal_date"]) == pd.Timestamp("2026-07-31")


def test_factor_v2_cache_does_not_require_archived_vlue(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    for symbol in ("QUAL", "USMV"):
        pd.DataFrame({"date": ["2026-06-30"], "close_price": [100.0]}).to_csv(
            tmp_path / f"{symbol}_daily.csv",
            index=False,
        )

    loaded = load_cached_defensive_factor_history(config.DEFENSIVE_FACTOR_V2["asset_universe"])
    assert set(loaded) == {"QUAL", "USMV"}
