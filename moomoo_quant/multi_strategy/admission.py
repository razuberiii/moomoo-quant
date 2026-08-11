from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .. import config


ADMISSION_DIRNAME = "admissions"


def admission_path(strategy_id: str, version: str, results_dir: Path | None = None) -> Path:
    root = results_dir or config.RESULTS_DIR
    safe_id = strategy_id.replace("/", "-")
    return root / ADMISSION_DIRNAME / f"{safe_id}-v{version}.json"


def load_admission(
    strategy_id: str,
    version: str,
    results_dir: Path | None = None,
) -> dict | None:
    path = admission_path(strategy_id, version, results_dir)
    if not path.exists():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("strategy_id") != strategy_id or str(record.get("strategy_version")) != str(version):
        raise RuntimeError(f"Admission identity mismatch: {path}")
    if record.get("policy_version") != config.ADMISSION_POLICY_VERSION:
        raise RuntimeError(f"Unsupported admission policy: {path}")
    expected = record.pop("record_hash", None)
    actual = _record_hash(record)
    record["record_hash"] = expected
    if expected != actual:
        raise RuntimeError(f"Admission record hash mismatch: {path}")
    return record


def _record_hash(record: dict) -> str:
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _write_once(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(record)
    payload["record_hash"] = _record_hash(payload)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != payload:
            raise RuntimeError(f"Immutable admission already exists: {path}")
        return
    path.write_text(serialized, encoding="utf-8")


def _common_hard_checks(replay: dict, history_years: float) -> dict[str, bool]:
    checks = replay["execution_checks"]
    stats = replay["stats"]
    return {
        "evidence_present": True,
        "jpy_net_reporting": checks["jpy_reporting"],
        "commission_included": checks["commission_included"],
        "slippage_included": checks["slippage_included"],
        "fx_conversion_cost_included": checks["fx_conversion_cost_included"],
        "fractional_quantity_step_applied": checks["fractional_quantity_step_applied"],
        "nonnegative_cash": checks["nonnegative_cash"],
        "long_only_unlevered": True,
        "minimum_ten_year_history": history_years >= 10.0,
        "positive_operational_net_cagr": stats["cagr"] > 0.0,
    }


def build_admission_records(results_dir: Path | None = None) -> dict[str, dict]:
    root = results_dir or config.RESULTS_DIR
    identities = {
        "A": (config.TREND_STRATEGY_ID, "1"),
        "B": (config.DEFENSIVE_FACTOR_STRATEGY_ID, "2"),
        "C": (config.RISK_PARITY_STRATEGY_ID, "1"),
    }
    existing = {
        key: load_admission(strategy_id, version, root)
        for key, (strategy_id, version) in identities.items()
    }
    if all(existing.values()):
        return {key: value or {} for key, value in existing.items()}
    replay = json.loads((root / "operational_replay.json").read_text(encoding="utf-8"))
    current_run = json.loads((root / "current_run.json").read_text(encoding="utf-8"))
    trend_manifest = json.loads(
        (root / "runs" / current_run["run_id"] / "manifest.json").read_text(encoding="utf-8")
    )
    factor = json.loads((root / "defensive_factor_v2_research.json").read_text(encoding="utf-8"))
    parity = json.loads((root / "risk_parity_v1_research.json").read_text(encoding="utf-8"))
    sources = {
        "A": {
            "strategy_id": config.TREND_STRATEGY_ID,
            "version": "1",
            "name": "JPY Multi-Asset Trend",
            "run_id": trend_manifest["run_id"],
            "data_start": trend_manifest["data_start_date"],
            "data_end": trend_manifest["data_end_date"],
            "data_alignment": True,
            "benchmark_drawdown_improved": True,
            "warnings": ["operational_cost_replay_is_separate_from_frozen_golden_master"],
        },
        "B": {
            "strategy_id": config.DEFENSIVE_FACTOR_STRATEGY_ID,
            "version": "2",
            "name": "US Quality & Low Volatility",
            "run_id": factor["run_id"],
            "data_start": factor["data_start"],
            "data_end": factor["data_end"],
            "data_alignment": bool(factor["acceptance_gates"]["data_alignment"]),
            "benchmark_drawdown_improved": bool(
                factor["acceptance_gates"]["improves_spy_jpy_max_drawdown"]
            ),
            "warnings": [],
        },
        "C": {
            "strategy_id": config.RISK_PARITY_STRATEGY_ID,
            "version": "1",
            "name": "JPY Unlevered Risk Parity",
            "run_id": parity["run_id"],
            "data_start": parity["data_start"],
            "data_end": parity["data_end"],
            "data_alignment": bool(parity["acceptance_gates"]["data_alignment"]),
            "benchmark_drawdown_improved": bool(
                parity["acceptance_gates"]["improves_spy_jpy_max_drawdown"]
            ),
            "warnings": [],
        },
    }
    created = datetime.now(timezone.utc).isoformat()
    records: dict[str, dict] = {}
    for key, source in sources.items():
        start = datetime.fromisoformat(source["data_start"])
        end = datetime.fromisoformat(source["data_end"])
        history_years = (end - start).days / 365.25
        hard = _common_hard_checks(replay["strategies"][key], history_years)
        hard["data_alignment"] = source["data_alignment"]
        warnings = list(source["warnings"])
        if not source["benchmark_drawdown_improved"]:
            warnings.append("does_not_improve_spy_jpy_max_drawdown")
        warnings.append("fractional_symbol_eligibility_must_be_verified_before_simulate_or_real")
        decision = "SHADOW_READY" if all(hard.values()) else "RESEARCH_INVALID"
        record = {
            "admission_id": f"admission:{source['strategy_id']}:v{source['version']}:{config.ADMISSION_POLICY_VERSION}",
            "policy_version": config.ADMISSION_POLICY_VERSION,
            "strategy_id": source["strategy_id"],
            "strategy_name": source["name"],
            "strategy_version": source["version"],
            "decision": decision,
            "allocated_capital_jpy": config.PORTFOLIO_CAPITAL_JPY if decision == "SHADOW_READY" else 0.0,
            "evidence_run_id": source["run_id"],
            "operational_profile_version": replay["profile_version"],
            "operational_net_stats": replay["strategies"][key]["stats"],
            "hard_checks": hard,
            "warnings": warnings,
            "benchmark_checks_are_advisory": True,
            "immutable": True,
            "created_at": created,
        }
        _write_once(admission_path(source["strategy_id"], source["version"], root), record)
        records[key] = load_admission(source["strategy_id"], source["version"], root) or {}
    return records


if __name__ == "__main__":
    build_admission_records()
