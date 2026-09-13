"""W2 T2S validation-scaffold tests — schema only, zero execution."""
from __future__ import annotations

import pytest

from nepal.framework_v1 import validation_scaffold as t2s
from nepal.framework_v1.provenance import sha256_canonical


def _metric_registry():
    return [
        {"metric_id": "calibration_slope", "class": "calibration",
         "operational_threshold": None},
        {"metric_id": "lead_time_days", "class": "lead_time",
         "operational_threshold": None},
    ]


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
    metric_registry = _metric_registry()
    return {
        "mode": t2s.MODE_SCAFFOLD_ONLY,
        "research_diagnostic_only": True,
        "promotion_eligible": False,
        "references": _refs(),
        "split_spec": {
            "split_id": "T2S-SPLIT-NEPAL-V1",
            "temporal_embargo_days": 30,
            "geographic_holdout": {"min_separation_km": 50.0},
            "event_separation": {"group_disjoint": True},
        },
        "metric_registry": metric_registry,
        "metric_registry_sha256": sha256_canonical(metric_registry),
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


# ---------- N2 scaffold hardening ----------


def _bound_refs(tmp_path):
    """Write real bound-envelope files and return refs bound to them.

    All three references (MEC, FMX, and the B ranked-array envelope) are
    file-bound: each ref's ``envelope_sha256`` equals the on-disk
    envelope's verified ``artifact_sha256`` and ``relative_path`` names
    the file under ``tmp_path``."""
    import json
    from nepal.framework_v1.provenance import bind_artifact_envelope
    refs = _refs()
    bound_docs = {
        "mec_reference": ("mec.json",
                          {"doc": "mec_reference",
                           "research_diagnostic_only": True}),
        "fmx_reference": ("fmx.json",
                          {"doc": "fmx_reference",
                           "research_diagnostic_only": True}),
        "b_reference": ("b_env.json",
                        {"b_status": "B_TO_C_BLOCKED",
                         "ranked_array_canonical_sha256": "ef" * 32,
                         "research_diagnostic_only": True}),
    }
    for rname, (fname, doc_payload) in bound_docs.items():
        doc = bind_artifact_envelope(doc_payload)
        (tmp_path / fname).write_text(json.dumps(doc))
        refs[rname]["relative_path"] = fname
        refs[rname]["envelope_sha256"] = doc["artifact_sha256"]
    return refs


def test_file_bound_refs_verify(tmp_path):
    p = _payload()
    p["references"] = _bound_refs(tmp_path)
    env = t2s.build_scaffold_envelope(p, reference_root=tmp_path)
    ok, problems = t2s.verify_scaffold_envelope(env,
                                              reference_root=tmp_path)
    assert ok, problems


def test_fake_unresolved_digest_rejected(tmp_path):
    """A plausible 64-hex digest that names nothing real on disk."""
    p = _payload()
    p["references"] = _bound_refs(tmp_path)
    p["references"]["mec_reference"]["envelope_sha256"] = "00" * 32
    with pytest.raises(ValueError):
        t2s.build_scaffold_envelope(p, reference_root=tmp_path)


def test_missing_relative_path_with_root_rejected(tmp_path):
    p = _payload()
    p["references"] = _bound_refs(tmp_path)
    del p["references"]["mec_reference"]["relative_path"]
    with pytest.raises(ValueError):
        t2s.build_scaffold_envelope(p, reference_root=tmp_path)


def test_symlink_reference_rejected(tmp_path):
    import json
    from nepal.framework_v1.provenance import bind_artifact_envelope
    p = _payload()
    p["references"] = _bound_refs(tmp_path)
    real = tmp_path / "real.json"
    real.write_text(json.dumps(bind_artifact_envelope(
        {"x": 1, "research_diagnostic_only": True})))
    (tmp_path / "mec.json").unlink()
    (tmp_path / "mec.json").symlink_to(real)
    with pytest.raises(ValueError):
        t2s.build_scaffold_envelope(p, reference_root=tmp_path)


def test_traversal_reference_rejected(tmp_path):
    p = _payload()
    p["references"] = _bound_refs(tmp_path)
    p["references"]["mec_reference"]["relative_path"] = "../escape.json"
    with pytest.raises(ValueError):
        t2s.build_scaffold_envelope(p, reference_root=tmp_path)


def test_nested_forged_ready_rejected():
    p = _payload()
    p["split_spec"]["notes"] = {"verdict_detail": "READY"}
    with pytest.raises(ValueError):
        t2s.build_scaffold_envelope(p)


def test_nested_operational_flag_rejected():
    p = _payload()
    p["metric_registry"][0]["description"] = "ok"
    p["split_spec"]["nested"] = {"warning_path_authorized": True}
    with pytest.raises(ValueError):
        t2s.build_scaffold_envelope(p)


def test_unknown_reference_type_rejected():
    p = _payload()
    p["references"]["extra_ref"] = {"envelope_sha256": "ab" * 32}
    with pytest.raises(ValueError):
        t2s.build_scaffold_envelope(p)


def test_reference_extra_field_rejected():
    p = _payload()
    p["references"]["b_reference"]["top_five"] = "1,2,3"
    with pytest.raises(ValueError):
        t2s.build_scaffold_envelope(p)


def test_duplicate_metric_id_rejected():
    p = _payload()
    p["metric_registry"].append(
        {"metric_id": "calibration_slope", "class": "skill",
         "operational_threshold": None})
    with pytest.raises(ValueError):
        t2s.build_scaffold_envelope(p)


def test_operational_metric_field_rejected():
    p = _payload()
    p["metric_registry"][0]["alert_threshold"] = 0.9
    with pytest.raises(ValueError):
        t2s.build_scaffold_envelope(p)


# ---------- T2S audit hardening (T2S-01..04) ----------


def test_strict_verify_requires_reference_root(tmp_path):
    """T2S-01: strict verification fails closed without a root."""
    p = _payload()
    p["references"] = _bound_refs(tmp_path)
    env = t2s.build_scaffold_envelope(p, reference_root=tmp_path)
    ok, problems = t2s.verify_scaffold_envelope(env, strict=True)
    assert not ok
    assert any("reference_root" in pr for pr in problems)


def test_strict_verify_with_bound_refs(tmp_path):
    """T2S-01: strict verification passes when every reference is
    file-bound under the supplied root."""
    p = _payload()
    p["references"] = _bound_refs(tmp_path)
    env = t2s.build_scaffold_envelope(p, reference_root=tmp_path)
    ok, problems = t2s.verify_scaffold_envelope(
        env, reference_root=tmp_path, strict=True)
    assert ok, problems


def test_strict_verify_missing_b_relative_path_rejected(tmp_path):
    """T2S-01: strict mode requires the B reference to be file-bound."""
    p = _payload()
    p["references"] = _bound_refs(tmp_path)
    del p["references"]["b_reference"]["relative_path"]
    with pytest.raises(ValueError):
        t2s.build_scaffold_envelope(p, reference_root=tmp_path)


def test_b_reference_fake_envelope_digest_rejected(tmp_path):
    """T2S-02: a plausible 64-hex B digest that matches no envelope."""
    p = _payload()
    p["references"] = _bound_refs(tmp_path)
    p["references"]["b_reference"]["envelope_sha256"] = "00" * 32
    with pytest.raises(ValueError):
        t2s.build_scaffold_envelope(p, reference_root=tmp_path)


def test_b_reference_envelope_missing_ranked_digest_rejected(tmp_path):
    """T2S-02: the bound B envelope must contain the declared ranked
    array canonical digest among its values."""
    p = _payload()
    p["references"] = _bound_refs(tmp_path)
    p["references"]["b_reference"]["ranked_array_canonical_sha256"] = \
        "11" * 32
    with pytest.raises(ValueError):
        t2s.build_scaffold_envelope(p, reference_root=tmp_path)


def test_b_status_disagreement_with_artifact_rejected(tmp_path):
    """T2S-03: a bound B envelope whose b_status disagrees with the
    caller-asserted statuses is rejected."""
    import json
    from nepal.framework_v1.provenance import bind_artifact_envelope
    p = _payload()
    p["references"] = _bound_refs(tmp_path)
    doc = bind_artifact_envelope(
        {"b_status": "B_TO_C_DEFERRED",
         "ranked_array_canonical_sha256": "ef" * 32,
         "research_diagnostic_only": True})
    (tmp_path / "b_env.json").write_text(json.dumps(doc))
    p["references"]["b_reference"]["envelope_sha256"] = \
        doc["artifact_sha256"]
    with pytest.raises(ValueError):
        t2s.build_scaffold_envelope(p, reference_root=tmp_path)


def test_missing_metric_registry_sha256_rejected():
    """T2S-04: the frozen metric-registry digest is required."""
    p = _payload()
    del p["metric_registry_sha256"]
    with pytest.raises(ValueError):
        t2s.build_scaffold_envelope(p)


def test_tampered_metric_registry_invalidates_digest():
    """T2S-04: editing the registry after freezing the digest fails."""
    p = _payload()
    p["metric_registry"].append(
        {"metric_id": "brier_score", "class": "calibration",
         "operational_threshold": None})
    with pytest.raises(ValueError):
        t2s.build_scaffold_envelope(p)


def test_missing_split_id_rejected():
    """T2S-04: split_spec must name a frozen cohort split id."""
    p = _payload()
    del p["split_spec"]["split_id"]
    with pytest.raises(ValueError):
        t2s.build_scaffold_envelope(p)
