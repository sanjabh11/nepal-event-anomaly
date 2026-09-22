"""Focused adversarial tests for the model-reexecution proof validator."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

from scripts.validate_reexecution_report import validate_report


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/validate_reexecution_report.py"
GATES = {
    "seed_policy": True,
    "modal_k_unanimous": False,
    "seed_ari": True,
    "seed_coverage": True,
    "loro": True,
    "temporal_bootstrap": True,
    "season_refits": True,
    "elevation": True,
    "missingness": True,
    "effort": True,
    "era_drift": True,
    "shuffled_null": False,
    "season_matched_null": False,
}
AUTHORITY = {
    "operational_claim": False,
    "production_authorized": False,
    "promotion_eligible": False,
    "warning_path_authorized": False,
}


def _lane(name: str, fidelity: str = "exact") -> dict:
    digest = "a" * 64 if name == "daily" else "b" * 64
    record = {
        "associable": False,
        "config_digest": digest,
        "environment_digest": "c" * 64,
        "freeze_digest": "d" * 64,
        "regime_artifact_digest": "e" * 64,
        "required_gates": copy.deepcopy(GATES),
        "status": "UNSUPERVISED_STRUCTURE_NOT_STABLE",
        "train_mask_digest": "f" * 64,
    }
    checks = {
        "authority_flags_all_false_recomputed": True,
        "config_digest_match": True,
        "environment_digest_match": True,
        "freeze_ok": True,
        "input_fidelity": fidelity,
        "manifest_substitution_verified": True,
        "recorded_envelope_digest_match": True,
        "recorded_freeze_digest_match": True,
        "reconstructed_config_digest_match": True,
        "required_gates_equality": copy.deepcopy(GATES),
        "source_manifest_byte_verified": True,
        "status_match": True,
        "train_mask_digest_match": True,
    }
    if fidelity == "roundtrip_drift":
        checks["input_fidelity_note"] = "bounded CSV float64 ulp drift"
    return {
        "artifact": f"{name}/artifact.json",
        "artifact_sha256": "1" * 64,
        "checks": checks,
        "differences": {"n_differing_fields": 0, "semantic": [], "volatile": []},
        "frame": f"{name}/frame.csv",
        "frame_sha256": "2" * 64,
        "lane": name,
        "problems": [],
        "recomputed": copy.deepcopy(record),
        "recorded": copy.deepcopy(record),
        "root_id": f"{name}_root",
        "sidecars": {"artifact": True, "frame": True},
        "verdict": "REPRODUCED",
    }


def _report() -> dict:
    return {
        "activity_id": "activity-round3",
        "authority": copy.deepcopy(AUTHORITY),
        "claim_scope": "research_only_no_operational_authorization",
        "completed_utc": "2026-09-22T06:11:25Z",
        "execution_context": {
            "host_daily_root": "/private/daily",
            "host_seasonal_root": "/private/seasonal",
            "note": "host paths are execution context only",
        },
        "lanes": {"daily": _lane("daily"), "seasonal": _lane("seasonal", "roundtrip_drift")},
        "proof_scope": "model_reexecution",
        "root_ids": {"daily": "daily_root", "seasonal": "seasonal_root"},
        "schema": "P5_MODEL_REEXECUTION_PROOF_V0",
        "started_utc": "2026-09-22T05:41:12Z",
        "status": "MODEL_REEXECUTED",
    }


def test_real_v2_shape_semantics_are_accepted() -> None:
    assert validate_report(_report()) == []


def test_roundtrip_drift_requires_an_explanation() -> None:
    report = _report()
    del report["lanes"]["seasonal"]["checks"]["input_fidelity_note"]
    problems = validate_report(report)
    assert any("input_fidelity_note" in problem for problem in problems)


def test_authority_true_is_never_accepted() -> None:
    report = _report()
    report["authority"]["promotion_eligible"] = True
    assert any("promotion_eligible" in problem for problem in validate_report(report))


def test_digest_mismatch_is_not_hidden_by_true_check_flags() -> None:
    report = _report()
    report["lanes"]["daily"]["recomputed"]["config_digest"] = "9" * 64
    assert any("config_digest differs" in problem for problem in validate_report(report))


def test_model_reexecuted_requires_both_lane_verdicts() -> None:
    report = _report()
    report["lanes"]["seasonal"]["verdict"] = "UNAVAILABLE"
    assert any("MODEL_REEXECUTED" in problem for problem in validate_report(report))


def test_historical_unavailable_report_can_be_validated_without_false_promotion() -> None:
    report = _report()
    report["status"] = "MODEL_REEXECUTION_UNAVAILABLE"
    report["lanes"]["seasonal"]["verdict"] = "UNAVAILABLE"
    report["lanes"]["seasonal"]["recomputed"] = {}
    report["lanes"]["seasonal"]["checks"].pop("input_fidelity")
    report["lanes"]["seasonal"]["checks"].pop("required_gates_equality")
    assert validate_report(report) == []


def test_cli_reports_fail_closed_status(tmp_path: Path) -> None:
    path = tmp_path / "proof.json"
    path.write_text(json.dumps(_report()), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(path)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert '"status": "REEXECUTION_REPORT_OK"' in result.stdout
