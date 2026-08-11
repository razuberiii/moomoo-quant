import ast
import json

import pandas as pd
import pytest

from moomoo_quant import config
from moomoo_quant.backtest.risk_parity import _capped_inverse_vol
from moomoo_quant.multi_strategy.bootstrap import initialize_multi_strategy_ledger


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


def test_failed_b_v2_budget_stays_zero_and_passing_c_gets_shadow_budget(tmp_path):
    ledger = initialize_multi_strategy_ledger(tmp_path / "ledger.db", include_research_slots=True)
    accounts = {(row["strategy_id"], row["strategy_version"]): row for row in ledger.rows("strategy_accounts")}
    assert accounts[(config.STRESS_PULLBACK_STRATEGY_ID, "2")]["allocated_capital_jpy"] == 0
    assert accounts[(config.RISK_PARITY_STRATEGY_ID, "1")]["allocated_capital_jpy"] == 100_000


def test_only_shadow_robots_have_positive_budget(tmp_path):
    ledger = initialize_multi_strategy_ledger(tmp_path / "ledger.db", include_research_slots=True)
    funded = {row["strategy_id"] for row in ledger.rows("strategy_accounts") if row["allocated_capital_jpy"] > 0}
    assert funded == {config.TREND_STRATEGY_ID, config.RISK_PARITY_STRATEGY_ID}


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

