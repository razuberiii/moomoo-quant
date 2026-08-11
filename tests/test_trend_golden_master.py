import json

import pytest

from moomoo_quant import config
from moomoo_quant.run_manifest import resolve_current_run


def _signal(result, date):
    signals = result["full"]["signals"]
    return signals[signals["signal_date"].astype(str).str.startswith(date)].iloc[0]


def test_frozen_parameters():
    assert config.TREND_MOMENTUM_MONTHS == 12
    assert config.TREND_SMA_MONTHS == 10
    assert config.TREND_MAX_ASSETS == 2
    assert config.TREND_ASSET_WEIGHT == 0.5


def test_primary_metrics_unchanged(trend_result):
    stats = trend_result["full"]["stats"]
    assert stats["total_return"] == pytest.approx(6.184631432646909, abs=1e-12)
    assert stats["cagr"] == pytest.approx(0.10584305933652494, abs=1e-12)
    assert stats["max_drawdown"] == pytest.approx(-0.28203688657721326, abs=1e-12)
    assert stats["sharpe"] == pytest.approx(0.7196094039476918, abs=1e-12)


def test_latest_complete_signal_unchanged(trend_result):
    signal = trend_result["latest_signal"]
    assert str(signal["signal_date"]).startswith("2026-07-31")
    assert signal["selected"] == "QQQ,SPY"
    assert signal["SPY_weight"] == 0.5
    assert signal["QQQ_weight"] == 0.5
    assert signal["GLD_weight"] == 0.0
    assert signal["IEF_weight"] == 0.0
    assert signal["JPY_CASH_weight"] == 0.0


def test_2008_cash_signal(trend_result):
    signal = _signal(trend_result, "2008-12-31")
    assert signal["selected"] == ""
    assert not signal["SPY_eligible"]
    assert not signal["QQQ_eligible"]
    assert signal["JPY_CASH_weight"] == 1.0


def test_2020_defensive_signal(trend_result):
    signal = _signal(trend_result, "2020-03-31")
    assert signal["selected"] == "GLD,IEF"
    assert signal["GLD_rank"] == 1
    assert signal["IEF_rank"] == 2
    assert signal["GLD_weight"] == signal["IEF_weight"] == 0.5


def test_2022_defensive_signal(trend_result):
    signal = _signal(trend_result, "2022-06-30")
    assert signal["selected"] == "GLD,IEF"
    assert not signal["SPY_eligible"]
    assert not signal["QQQ_eligible"]


def test_2022_crisis_summary_unchanged(trend_result):
    summary = trend_result["dashboard"]["summary_2022"]
    assert summary["year_return"] == pytest.approx(-0.17919808885922273)
    assert summary["spy_lost_eligible"] == ["2022-06", "2022-09", "2022-12"]
    assert summary["alignment_audit_passed"] is True


def test_run_manifest_locks_one_artifact_batch():
    run_dir, manifest = resolve_current_run()
    assert manifest["strategy_id"] == config.TREND_STRATEGY_ID
    assert manifest["parameters"]["momentum_months"] == 12
    assert all((run_dir / name).exists() for name in manifest["artifacts"])
    assert json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))["run_id"] == manifest["run_id"]
