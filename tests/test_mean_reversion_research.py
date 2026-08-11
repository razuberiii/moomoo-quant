import json

from moomoo_quant import config


def test_preregistered_spec_exists():
    path = config.BASE_DIR / "docs" / "mean_reversion_v1_preregistration.md"
    text = path.read_text(encoding="utf-8")
    assert "RSI(5) below 25" in text
    assert "five trading days" in text
    assert "must not be overwritten" in text


def test_research_result_keeps_failed_strategy_out_of_shadow():
    summary = json.loads((config.RESULTS_DIR / "mean_reversion_jpy_research.json").read_text(encoding="utf-8"))
    assert summary["status"] == "RESEARCH_REJECTED"
    assert summary["acceptance_gates"]["positive_net_expectancy"] is False
    assert summary["stats"]["gross_return"] > 0
    assert summary["stats"]["net_return"] < 0


def test_research_is_jpy_denominated_and_complementary():
    summary = json.loads((config.RESULTS_DIR / "mean_reversion_jpy_research.json").read_text(encoding="utf-8"))
    assert summary["base_currency"] == "JPY"
    assert summary["signal_currency"] == "USD"
    assert abs(summary["monthly_correlation_with_trend_v1"]) < 0.70


def test_no_parameter_grid_was_created_for_v1():
    assert not (config.RESULTS_DIR / "mean_reversion_parameter_grid.csv").exists()
