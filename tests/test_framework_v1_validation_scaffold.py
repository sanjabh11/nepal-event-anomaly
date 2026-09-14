"""W2 T2S validation-scaffold tests — schema only, zero execution."""
from __future__ import annotations

import pytest

from nepal.framework_v1 import validation_scaffold as t2s
from nepal.framework_v1.provenance import sha256_canonical

_GENERATION_ID = "T2S-CANDIDATE-GEN-2026-09-12"
_CODE_REVISION = "28b44b9"


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
        # T2S-06: strict-mode fields — declared so strict verification
        # fixtures work; bound reference docs carry matching values.
        "candidate_generation_id": _GENERATION_ID,
        "code_revision": _CODE_REVISION,
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
        # T2S-10: bound docs must self-describe the same typed fields the
        # references declare — envelope_type on the MEC doc, fmx_status
        # on the FMX doc, b_status on the B doc.
        "mec_reference": ("mec.json",
                          {"doc": "mec_reference",
                           "envelope_type": "MULTI_EVENT_CONTRACT_V1",
                           "candidate_generation_id": _GENERATION_ID,
                           "code_revision": _CODE_REVISION,
                           "research_diagnostic_only": True}),
        "fmx_reference": ("fmx.json",
                          {"doc": "fmx_reference",
                           "fmx_status":
                               "FMX_BLOCKED_PENDING_EXPLICIT_FREEZE",
                           "candidate_generation_id": _GENERATION_ID,
                           "code_revision": _CODE_REVISION,
                           "research_diagnostic_only": True}),
        "b_reference": ("b_env.json",
                        {"b_status": "B_TO_C_BLOCKED",
                         "ranked_array_canonical_sha256": "ef" * 32,
                         "candidate_generation_id": _GENERATION_ID,
                         "code_revision": _CODE_REVISION,
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


# ---------- T2S-05/06/08 transition-semantics hardening ----------


def test_caller_forged_binding_status_overwritten():
    """T2S-05: binding_status is asserted by the builder — a
    caller-supplied value is ignored, never trusted."""
    p = _payload()
    p["binding_status"] = "FILE_BOUND"
    env = t2s.build_scaffold_envelope(p)
    assert env["binding_status"] == "UNBOUND_INFORMATIONAL"
    ok, problems = t2s.verify_scaffold_envelope(env)
    assert ok, problems


def test_binding_status_file_bound(tmp_path):
    """T2S-05: building with a reference_root asserts FILE_BOUND even
    when the caller forged a lower status."""
    p = _payload()
    p["binding_status"] = "UNBOUND_INFORMATIONAL"  # forged low
    p["references"] = _bound_refs(tmp_path)
    env = t2s.build_scaffold_envelope(p, reference_root=tmp_path)
    assert env["binding_status"] == "FILE_BOUND"


def test_unbound_informational_never_strict_verifies():
    """T2S-05: an unbound informational scaffold is legal non-strict
    but can never satisfy strict transition semantics."""
    env = t2s.build_scaffold_envelope(_payload())
    assert env["binding_status"] == "UNBOUND_INFORMATIONAL"
    ok, problems = t2s.verify_scaffold_envelope(env)
    assert ok, problems
    ok, problems = t2s.verify_scaffold_envelope(env, strict=True)
    assert not ok
    assert any("binding_status" in pr for pr in problems)


def test_file_bound_claim_unverified_without_root(tmp_path):
    """T2S-05: an envelope claiming FILE_BOUND cannot be honored
    unless the caller supplies the reference_root it was bound under."""
    p = _payload()
    p["references"] = _bound_refs(tmp_path)
    env = t2s.build_scaffold_envelope(p, reference_root=tmp_path)
    assert env["binding_status"] == "FILE_BOUND"
    ok, problems = t2s.verify_scaffold_envelope(env)
    assert not ok
    assert any("reference_root" in pr for pr in problems)


def test_file_bound_non_strict_verify_still_runs_file_checks(tmp_path):
    """T2S-05: supplying the root on a non-strict verify of a
    FILE_BOUND envelope still exercises the file bindings."""
    p = _payload()
    p["references"] = _bound_refs(tmp_path)
    env = t2s.build_scaffold_envelope(p, reference_root=tmp_path)
    (tmp_path / "mec.json").write_text("{}")
    ok, problems = t2s.verify_scaffold_envelope(
        env, reference_root=tmp_path)
    assert not ok


def test_strict_requires_generation_and_revision(tmp_path):
    """T2S-06: strict mode requires non-empty candidate_generation_id
    and code_revision on the payload."""
    p = _payload()
    del p["candidate_generation_id"]
    del p["code_revision"]
    p["references"] = _bound_refs(tmp_path)
    env = t2s.build_scaffold_envelope(p, reference_root=tmp_path)
    ok, problems = t2s.verify_scaffold_envelope(
        env, reference_root=tmp_path, strict=True)
    assert not ok
    assert any("candidate_generation_id" in pr for pr in problems)
    assert any("code_revision" in pr for pr in problems)


def test_mixed_generation_build_rejected(tmp_path):
    """T2S-06: a bound envelope generated from a different candidate
    generation than declared is rejected at build."""
    import json
    from nepal.framework_v1.provenance import bind_artifact_envelope
    p = _payload()
    p["references"] = _bound_refs(tmp_path)
    doc = bind_artifact_envelope({
        "doc": "mec_reference",
        "research_diagnostic_only": True,
        "candidate_generation_id": "T2S-OTHER-GENERATION",
        "code_revision": _CODE_REVISION})
    (tmp_path / "mec.json").write_text(json.dumps(doc))
    p["references"]["mec_reference"]["envelope_sha256"] = \
        doc["artifact_sha256"]
    with pytest.raises(ValueError):
        t2s.build_scaffold_envelope(p, reference_root=tmp_path)


def test_mixed_generation_rejected_on_non_strict_verify(tmp_path):
    """T2S-06: generation equality is enforced whenever bound docs are
    checked — an envelope built unbound is re-checked against the real
    files when a root is supplied to a non-strict verify."""
    import json
    from nepal.framework_v1.provenance import bind_artifact_envelope
    p = _payload()
    refs = _bound_refs(tmp_path)
    # Rebind mec.json to a different-generation envelope and point the
    # declared digest at it — the digest is honest, the generation is
    # mixed.  Build unbound so only verify sees the files.
    doc = bind_artifact_envelope({
        "doc": "mec_reference",
        "research_diagnostic_only": True,
        "candidate_generation_id": "T2S-OTHER-GENERATION",
        "code_revision": _CODE_REVISION})
    (tmp_path / "mec.json").write_text(json.dumps(doc))
    refs["mec_reference"]["envelope_sha256"] = doc["artifact_sha256"]
    p["references"] = refs
    env = t2s.build_scaffold_envelope(p)
    ok, problems = t2s.verify_scaffold_envelope(
        env, reference_root=tmp_path)
    assert not ok
    assert any("generation" in pr for pr in problems)


def test_real_v1_profile_blocked_fmx_rejected():
    """T2S-09: the successor profile REQUIRES a ready FMX — a blocked
    matrix cannot back a real-validation design."""
    p = _payload()
    p["scaffold_profile"] = "T2_REAL_V1"
    with pytest.raises(ValueError):
        t2s.build_scaffold_envelope(p)


def test_real_v1_profile_missing_fmx_status_rejected():
    """T2S-09: under T2_REAL_V1 an fmx_reference without a status does
    not satisfy the required file-bound FMX_READY claim."""
    p = _payload()
    p["scaffold_profile"] = "T2_REAL_V1"
    del p["references"]["fmx_reference"]["fmx_status"]
    with pytest.raises(ValueError):
        t2s.build_scaffold_envelope(p)


def test_unknown_scaffold_profile_rejected():
    """T2S-08: an unrecognized scaffold_profile fails closed."""
    p = _payload()
    p["scaffold_profile"] = "T2_EXECUTE_NOW"
    with pytest.raises(ValueError):
        t2s.build_scaffold_envelope(p)


def test_real_v1_fmx_ready_unbound_rejected():
    """T2S-08: an FMX_READY claim without a reference_root fails at
    build — the claim must be file-bound."""
    p = _payload()
    p["scaffold_profile"] = "T2_REAL_V1"
    p["references"]["fmx_reference"]["fmx_status"] = "FMX_READY"
    with pytest.raises(ValueError):
        t2s.build_scaffold_envelope(p)


def test_real_v1_fmx_ready_bound_doc_without_file_bindings_rejected(
        tmp_path):
    """T2S-08: a file-bound FMX_READY claim whose bound envelope lacks
    file_bindings (real freeze evidence) is rejected."""
    p = _payload()
    p["scaffold_profile"] = "T2_REAL_V1"
    p["references"] = _bound_refs(tmp_path)
    p["references"]["fmx_reference"]["fmx_status"] = "FMX_READY"
    with pytest.raises(ValueError):
        t2s.build_scaffold_envelope(p, reference_root=tmp_path)


def test_real_v1_fmx_ready_bound_with_file_bindings_ok(tmp_path):
    """T2S-08: the successor profile accepts a file-bound FMX_READY
    claim backed by real freeze evidence carried on the bound FMX
    envelope — scaffold_status stays BLOCKED_PENDING_FMX."""
    import json
    from nepal.framework_v1.provenance import bind_artifact_envelope
    p = _payload()
    p["scaffold_profile"] = "T2_REAL_V1"
    p["references"] = _bound_refs(tmp_path)
    doc = bind_artifact_envelope({
        "doc": "fmx_reference",
        "fmx_status": "FMX_READY",
        "file_bindings": {"matrix_freeze_envelope_sha256": "ab" * 32},
        "candidate_generation_id": _GENERATION_ID,
        "code_revision": _CODE_REVISION,
        "research_diagnostic_only": True})
    (tmp_path / "fmx.json").write_text(json.dumps(doc))
    fmx_ref = p["references"]["fmx_reference"]
    fmx_ref["fmx_status"] = "FMX_READY"
    fmx_ref["envelope_sha256"] = doc["artifact_sha256"]
    env = t2s.build_scaffold_envelope(p, reference_root=tmp_path)
    assert env["binding_status"] == "FILE_BOUND"
    assert env["scaffold_status"] == "BLOCKED_PENDING_FMX"
    ok, problems = t2s.verify_scaffold_envelope(
        env, reference_root=tmp_path)
    assert ok, problems
    ok, problems = t2s.verify_scaffold_envelope(
        env, reference_root=tmp_path, strict=True)
    assert ok, problems


# ---------- T2S-09/10/11 strict binding hardening ----------


def test_bound_mec_envelope_type_mismatch_rejected(tmp_path):
    """T2S-10: the declared mec_reference.envelope_type must equal the
    bound envelope's own envelope_type."""
    import json
    from nepal.framework_v1.provenance import bind_artifact_envelope
    p = _payload()
    refs = _bound_refs(tmp_path)
    doc = bind_artifact_envelope({
        "doc": "mec_reference",
        "envelope_type": "SOME_OTHER_ENVELOPE",
        "candidate_generation_id": _GENERATION_ID,
        "code_revision": _CODE_REVISION,
        "research_diagnostic_only": True})
    (tmp_path / "mec.json").write_text(json.dumps(doc))
    refs["mec_reference"]["envelope_sha256"] = doc["artifact_sha256"]
    p["references"] = refs
    with pytest.raises(ValueError):
        t2s.build_scaffold_envelope(p, reference_root=tmp_path)


def test_bound_mec_envelope_type_absent_rejected(tmp_path):
    """T2S-10: a bound MEC doc that records no envelope_type cannot
    satisfy the declared typed reference."""
    import json
    from nepal.framework_v1.provenance import bind_artifact_envelope
    p = _payload()
    refs = _bound_refs(tmp_path)
    doc = bind_artifact_envelope({
        "doc": "mec_reference",
        "candidate_generation_id": _GENERATION_ID,
        "code_revision": _CODE_REVISION,
        "research_diagnostic_only": True})
    (tmp_path / "mec.json").write_text(json.dumps(doc))
    refs["mec_reference"]["envelope_sha256"] = doc["artifact_sha256"]
    p["references"] = refs
    with pytest.raises(ValueError):
        t2s.build_scaffold_envelope(p, reference_root=tmp_path)


def test_bound_fmx_status_mismatch_rejected(tmp_path):
    """T2S-10: the bound FMX envelope's fmx_status must equal the
    declared references.fmx_reference.fmx_status."""
    import json
    from nepal.framework_v1.provenance import bind_artifact_envelope
    p = _payload()
    refs = _bound_refs(tmp_path)
    doc = bind_artifact_envelope({
        "doc": "fmx_reference",
        "fmx_status": "FMX_TAMPERED_STATUS",
        "candidate_generation_id": _GENERATION_ID,
        "code_revision": _CODE_REVISION,
        "research_diagnostic_only": True})
    (tmp_path / "fmx.json").write_text(json.dumps(doc))
    refs["fmx_reference"]["envelope_sha256"] = doc["artifact_sha256"]
    p["references"] = refs
    with pytest.raises(ValueError):
        t2s.build_scaffold_envelope(p, reference_root=tmp_path)


def test_bound_b_doc_gate_passed_maps_status_rejected(tmp_path):
    """T2S-10: a bound B doc carrying gate_passed=True maps to
    B_TO_C_READY, which disagrees with the declared blocked status."""
    import json
    from nepal.framework_v1.provenance import bind_artifact_envelope
    p = _payload()
    refs = _bound_refs(tmp_path)
    doc = bind_artifact_envelope({
        "gate_passed": True,
        "ranked_array_canonical_sha256": "ef" * 32,
        "candidate_generation_id": _GENERATION_ID,
        "code_revision": _CODE_REVISION,
        "research_diagnostic_only": True})
    (tmp_path / "b_env.json").write_text(json.dumps(doc))
    refs["b_reference"]["envelope_sha256"] = doc["artifact_sha256"]
    p["references"] = refs
    with pytest.raises(ValueError):
        t2s.build_scaffold_envelope(p, reference_root=tmp_path)


def test_bound_b_doc_without_status_rejected(tmp_path):
    """T2S-10: a bound B doc recording no b_status/phase_status/
    gate_passed cannot verify the declared status — fail closed."""
    import json
    from nepal.framework_v1.provenance import bind_artifact_envelope
    p = _payload()
    refs = _bound_refs(tmp_path)
    doc = bind_artifact_envelope({
        "doc": "b_reference",
        "ranked_array_canonical_sha256": "ef" * 32,
        "candidate_generation_id": _GENERATION_ID,
        "code_revision": _CODE_REVISION,
        "research_diagnostic_only": True})
    (tmp_path / "b_env.json").write_text(json.dumps(doc))
    refs["b_reference"]["envelope_sha256"] = doc["artifact_sha256"]
    p["references"] = refs
    with pytest.raises(ValueError):
        t2s.build_scaffold_envelope(p, reference_root=tmp_path)


def test_strict_bound_doc_missing_generation_id_rejected(tmp_path):
    """T2S-11: strict verification requires every bound doc to carry the
    declared identity — a doc missing candidate_generation_id rejects
    even though its digest is honest (non-strict build still passes)."""
    import json
    from nepal.framework_v1.provenance import bind_artifact_envelope
    p = _payload()
    refs = _bound_refs(tmp_path)
    doc = bind_artifact_envelope({
        "doc": "mec_reference",
        "envelope_type": "MULTI_EVENT_CONTRACT_V1",
        "code_revision": _CODE_REVISION,  # candidate_generation_id absent
        "research_diagnostic_only": True})
    (tmp_path / "mec.json").write_text(json.dumps(doc))
    refs["mec_reference"]["envelope_sha256"] = doc["artifact_sha256"]
    p["references"] = refs
    env = t2s.build_scaffold_envelope(p, reference_root=tmp_path)
    ok, problems = t2s.verify_scaffold_envelope(
        env, reference_root=tmp_path)
    assert ok, problems  # non-strict: when-present semantics
    ok, problems = t2s.verify_scaffold_envelope(
        env, reference_root=tmp_path, strict=True)
    assert not ok
    assert any("candidate_generation_id" in pr for pr in problems)


def test_strict_bound_doc_missing_code_revision_rejected(tmp_path):
    """T2S-11: a bound doc missing code_revision rejects under strict."""
    import json
    from nepal.framework_v1.provenance import bind_artifact_envelope
    p = _payload()
    refs = _bound_refs(tmp_path)
    doc = bind_artifact_envelope({
        "doc": "fmx_reference",
        "fmx_status": "FMX_BLOCKED_PENDING_EXPLICIT_FREEZE",
        "candidate_generation_id": _GENERATION_ID,
        # code_revision absent
        "research_diagnostic_only": True})
    (tmp_path / "fmx.json").write_text(json.dumps(doc))
    refs["fmx_reference"]["envelope_sha256"] = doc["artifact_sha256"]
    p["references"] = refs
    env = t2s.build_scaffold_envelope(p, reference_root=tmp_path)
    ok, problems = t2s.verify_scaffold_envelope(
        env, reference_root=tmp_path, strict=True)
    assert not ok
    assert any("code_revision" in pr for pr in problems)


def test_non_strict_bound_doc_missing_identity_tolerated(tmp_path):
    """T2S-11: without strict, a bound doc that simply does not carry
    the identity fields remains legal (they are only compared when
    present)."""
    import json
    from nepal.framework_v1.provenance import bind_artifact_envelope
    p = _payload()
    refs = _bound_refs(tmp_path)
    doc = bind_artifact_envelope({
        "doc": "mec_reference",
        "envelope_type": "MULTI_EVENT_CONTRACT_V1",
        "research_diagnostic_only": True})
    (tmp_path / "mec.json").write_text(json.dumps(doc))
    refs["mec_reference"]["envelope_sha256"] = doc["artifact_sha256"]
    p["references"] = refs
    env = t2s.build_scaffold_envelope(p, reference_root=tmp_path)
    ok, problems = t2s.verify_scaffold_envelope(
        env, reference_root=tmp_path)
    assert ok, problems
