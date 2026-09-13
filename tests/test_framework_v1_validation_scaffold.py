"""W2 T2S validation-scaffold tests — schema only, zero execution."""
from __future__ import annotations

import pytest

from nepal.framework_v1 import validation_scaffold as t2s


def _refs():
    return {
        "mec_reference": {"envelope_sha256": "ab" * 32,
                          "envelope_type": "MULTI_EVENT_CONTRACT_V1"},
        "fmx_reference": {"envelope_sha256": "cd" * 32,
                          "fmx_status": "FMX_BLOCKED_PENDING_EXPLICIT_FREEZE"},
        "b_reference": {"status": "B_TO_C_BLOCKED",
                        "ranked_array_canonical_sha256": "ef" * 32},
    }


def _payload():
    return {
        "mode": t2s.MODE_SCAFFOLD_ONLY,
        "research_diagnostic_only": True,
        "promotion_eligible": False,
        "references": _refs(),
        "split_spec": {
            "temporal_embargo_days": 30,
            "geographic_holdout": {"min_separation_km": 50.0},
            "event_separation": {"group_disjoint": True},
        },
        "metric_registry": [
            {"metric_id": "calibration_slope", "class": "calibration",
             "operational_threshold": None},
            {"metric_id": "lead_time_days", "class": "lead_time",
             "operational_threshold": None},
        ],
        "inherited_state": {"b_status": "B_TO_C_BLOCKED",
                            "ranking_rerun": False,
                            "e_status": "E_BLOCKED",
                            "f_status": "F_BLOCKED"},
    }


def test_scaffold_builds_and_verifies():
    env = t2s.build_scaffold_envelope(_payload())
    assert env["mode"] == t2s.MODE_SCAFFOLD_ONLY
    assert env["scaffold_status"] == t2s.BLOCKED_PENDING_FMX
    ok, problems = t2s.verify_scaffold_envelope(env)
    assert ok, problems


def test_self_hash_tamper_rejected():
    env = t2s.build_scaffold_envelope(_payload())
    env["mode"] = "MUTATED"
    ok, _ = t2s.verify_scaffold_envelope(env)
    assert not ok


def test_caller_supplied_gate_boolean_rejected():
    p = _payload()
    p["gate_override"] = True
    with pytest.raises(ValueError):
        t2s.build_scaffold_envelope(p)


def test_b_ready_state_rejected():
    p = _payload()
    p["inherited_state"]["b_status"] = "B_TO_C_READY"
    with pytest.raises(ValueError):
        t2s.build_scaffold_envelope(p)


def test_fmx_ready_without_token_rejected():
    p = _payload()
    p["references"]["fmx_reference"]["fmx_status"] = "FMX_READY"
    with pytest.raises(ValueError):
        t2s.build_scaffold_envelope(p)


def test_ranked_payload_reference_rejected():
    p = _payload()
    p["references"]["b_reference"]["ranked"] = [
        {"analysis_unit_id": "AU-E0001-N0001", "priority_index": 0.9}]
    with pytest.raises(ValueError):
        t2s.build_scaffold_envelope(p)


def test_legacy_module_reference_rejected():
    p = _payload()
    p["references"]["legacy_runner"] = "multi_event_validation"
    with pytest.raises(ValueError):
        t2s.build_scaffold_envelope(p)


def test_missing_metric_registry_rejected():
    p = _payload()
    del p["metric_registry"]
    with pytest.raises(ValueError):
        t2s.build_scaffold_envelope(p)


def test_operational_threshold_rejected():
    p = _payload()
    p["metric_registry"][0]["operational_threshold"] = 0.8
    with pytest.raises(ValueError):
        t2s.build_scaffold_envelope(p)


def test_missing_split_spec_rejected():
    p = _payload()
    del p["split_spec"]
    with pytest.raises(ValueError):
        t2s.build_scaffold_envelope(p)


def test_no_execution_surfaces():
    """The scaffold must expose no execution entry points."""
    assert not hasattr(t2s, "run_validation")
    assert not hasattr(t2s, "fit")
    assert not hasattr(t2s, "predict")
    assert not hasattr(t2s, "rank_box")
    assert not hasattr(t2s, "leave_one_layer_out_top5")


def test_scaffold_module_has_no_forbidden_imports():
    import inspect
    from nepal.framework_v1 import research_boundaries
    src = inspect.getsource(t2s)
    ok, hits = research_boundaries.scan_source_for_quarantined_imports(src)
    assert ok, hits


def test_scaffold_result_blocked_pending_fmx():
    env = t2s.build_scaffold_envelope(_payload())
    assert env["scaffold_status"] == "BLOCKED_PENDING_FMX"
    assert env["inherited_state"]["b_status"] == "B_TO_C_BLOCKED"
    assert env["inherited_state"]["ranking_rerun"] is False
