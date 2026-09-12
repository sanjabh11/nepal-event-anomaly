"""Focused regression tests for authenticated full-pipeline boundaries."""
from __future__ import annotations

import hashlib
import json
import copy
from pathlib import Path

import pytest

from nepal.framework_v1 import contract as C
from nepal.framework_v1.input_manifest import (
    canonical_input_manifest_hash,
    pretty_input_manifest_hash,
    verify_canonical_input_manifest,
    verify_input_manifest,
)
from nepal.framework_v1.provenance import (
    bind_artifact_envelope,
    bind_gate_artifact,
    gate_input_artifact_sha256,
    sha256_canonical,
    verify_gate_input,
    verify_artifact_envelope,
)
from nepal.framework_v1.orchestrator import (
    bind_pipeline_report,
    load_verified_pipeline_checkpoint,
    pipeline_input_fingerprint,
    verify_pipeline_report,
    write_pipeline_checkpoint,
)
from nepal.framework_v1.briefing import (
    _strict_gate_problems,
    build_briefing_artifact,
    verify_briefing_artifact,
)
from nepal.framework_v1.cli import _ensure_a_catalog_envelope
from nepal.framework_v1.controls import ControlsConfig, create_controls_lock
from nepal.framework_v1.validation import write_validation_artifact
from nepal.framework_v1.preflight import run_preflight


def _manifest(root: Path) -> dict:
    payload = b"full-gate-payload"
    (root / "payload.bin").write_bytes(payload)
    artifact = {
        "artifact_id": "payload",
        "role": "B",
        "kind": "metadata",
        "relative_path": "payload.bin",
        "status": "READY",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
        "crs": "",
        "units": "",
        "license": "test",
        "source_url": "https://example.invalid/payload",
        "query_or_request": "fixture",
        "source_record_id": "fixture-1",
        "acquired_at": "2026-09-12",
        "observation_start": "",
        "observation_end": "",
        "publication_or_validity_date": "",
        "processing": "fixture",
        "language_access_status": "en/accessible",
    }
    manifest = {
        "schema_version": "2.0.0-reconciled",
        "contract_sha256": C.contract_hash(),
        "artifact_count": 1,
        "artifacts": [artifact],
        "required_artifact_ids": ["payload"],
    }
    manifest["manifest_sha256"] = canonical_input_manifest_hash(manifest)
    return manifest


def test_trusted_canonical_manifest_anchor_is_explicit(tmp_path):
    manifest = _manifest(tmp_path)
    trusted = manifest["manifest_sha256"]
    result = verify_input_manifest(
        manifest,
        tmp_path,
        expected_contract_sha256=C.contract_hash(),
        expected_framework_contract_sha256=C.contract_hash(),
        trusted_manifest_sha256=trusted,
        required_artifact_ids=["payload"],
    )
    assert result.ok is True
    assert result.can_run_primary is True
    assert result.checks["trusted_manifest_anchor_bound"] is True


def test_trusted_anchor_rejects_pretty_only_and_wrong_anchor(tmp_path):
    manifest = _manifest(tmp_path)
    canonical = manifest["manifest_sha256"]
    pretty = pretty_input_manifest_hash(manifest)
    pretty_manifest = dict(manifest)
    pretty_manifest["manifest_sha256"] = pretty
    pretty_result = verify_input_manifest(
        pretty_manifest,
        tmp_path,
        expected_contract_sha256=C.contract_hash(),
        expected_framework_contract_sha256=C.contract_hash(),
        trusted_manifest_sha256=canonical,
        required_artifact_ids=["payload"],
    )
    assert pretty_result.checks["canonical_manifest_authorized"] is False
    assert pretty_result.can_run_primary is False

    wrong = verify_input_manifest(
        manifest,
        tmp_path,
        expected_contract_sha256=C.contract_hash(),
        expected_framework_contract_sha256=C.contract_hash(),
        trusted_manifest_sha256="0" * 64,
        required_artifact_ids=["payload"],
    )
    assert wrong.ok is False
    assert wrong.checks["trusted_manifest_anchor_bound"] is False


def test_strict_helper_requires_external_anchor(tmp_path):
    manifest = _manifest(tmp_path)
    result = verify_canonical_input_manifest(
        manifest,
        tmp_path,
        trusted_manifest_sha256=None,
        expected_contract_sha256=C.contract_hash(),
        expected_framework_contract_sha256=C.contract_hash(),
        required_artifact_ids=["payload"],
    )
    assert result.ok is False
    assert result.can_run_primary is False
    assert any("trusted" in error.lower() for error in result.errors)


def test_pipeline_style_envelope_binds_status_and_provenance():
    envelope = bind_pipeline_report({
        "A_CATALOG": {"status": "A_BLOCKED"},
        "B_SCREEN": {"status": "B_TO_C_BLOCKED"},
        "E_VALIDATION": {"status": "E_BLOCKED"},
        "F_BRIEFING": {"status": "F_BLOCKED"},
        "status": "B_TO_C_BLOCKED",
        "provenance": {"framework_contract_sha256": C.contract_hash()},
    }, exit_code=3)
    assert verify_pipeline_report(envelope) == (True, [])
    tampered = json.loads(json.dumps(envelope))
    tampered["status"] = "B_TO_C_READY"
    assert verify_artifact_envelope(tampered)[0] is False


def test_pipeline_report_requires_runtime_framework_contract_provenance():
    envelope = bind_pipeline_report({
        "A_CATALOG": {"status": "A_BLOCKED"},
        "B_SCREEN": {"status": "B_TO_C_BLOCKED"},
        "E_VALIDATION": {"status": "E_BLOCKED"},
        "F_BRIEFING": {"status": "F_BLOCKED"},
        "status": "B_TO_C_BLOCKED",
    }, exit_code=3)

    assert envelope["provenance"]["framework_contract_sha256"] == C.contract_hash()
    assert verify_pipeline_report(envelope) == (True, [])

    forged = bind_pipeline_report({
        "A_CATALOG": {"status": "A_BLOCKED"},
        "B_SCREEN": {"status": "B_TO_C_BLOCKED"},
        "E_VALIDATION": {"status": "E_BLOCKED"},
        "F_BRIEFING": {"status": "F_BLOCKED"},
        "status": "B_TO_C_BLOCKED",
        "provenance": {"framework_contract_sha256": "0" * 64},
    }, exit_code=3)

    valid, errors = verify_pipeline_report(forged)

    assert valid is False
    assert any("framework contract" in error.lower() for error in errors)


def test_pipeline_report_rejects_incoherent_downstream_ready_states():
    envelope = bind_pipeline_report({
        "A_CATALOG": {"status": C.PHASE_STATUS_A_BLOCKED},
        "B_SCREEN": {"status": C.PHASE_STATUS_B_TO_C_BLOCKED},
        "E_VALIDATION": {"status": C.PHASE_STATUS_E_READY},
        "F_BRIEFING": {"status": C.PHASE_STATUS_F_READY},
        "status": C.PHASE_STATUS_B_TO_C_BLOCKED,
        "provenance": {"framework_contract_sha256": C.contract_hash()},
    }, exit_code=3)

    valid, errors = verify_pipeline_report(envelope)

    assert valid is False
    assert any("stage" in error.lower() and "dependency" in error.lower()
               for error in errors)


def test_pipeline_report_rejects_malformed_preflight_without_raising():
    envelope = bind_pipeline_report({
        "preflight": ["not-an-object"],
        "A_CATALOG": {"status": C.PHASE_STATUS_A_BLOCKED},
        "B_SCREEN": {"status": C.PHASE_STATUS_B_TO_C_BLOCKED},
        "E_VALIDATION": {"status": C.PHASE_STATUS_E_BLOCKED},
        "F_BRIEFING": {"status": C.PHASE_STATUS_F_BLOCKED},
        "status": C.PHASE_STATUS_B_TO_C_BLOCKED,
        "provenance": {"framework_contract_sha256": C.contract_hash()},
    }, exit_code=3)

    valid, errors = verify_pipeline_report(envelope)

    assert valid is False
    assert any("preflight" in error.lower() for error in errors)


def test_pipeline_report_rejects_non_object_stage_without_raising():
    envelope = bind_pipeline_report({
        "A_CATALOG": ["not-an-object"],
        "B_SCREEN": {"status": C.PHASE_STATUS_B_TO_C_BLOCKED},
        "E_VALIDATION": {"status": C.PHASE_STATUS_E_BLOCKED},
        "F_BRIEFING": {"status": C.PHASE_STATUS_F_BLOCKED},
        "status": C.PHASE_STATUS_B_TO_C_BLOCKED,
        "provenance": {"framework_contract_sha256": C.contract_hash()},
    }, exit_code=3)

    valid, errors = verify_pipeline_report(envelope)

    assert valid is False
    assert any("stage a_catalog is required" in error.lower()
               for error in errors)


def test_pipeline_report_rejects_unhashable_stage_status_without_raising():
    envelope = bind_pipeline_report({
        "A_CATALOG": {"status": C.PHASE_STATUS_A_BLOCKED},
        "B_SCREEN": {"status": C.PHASE_STATUS_B_TO_C_BLOCKED},
        "E_VALIDATION": {"status": C.PHASE_STATUS_E_BLOCKED},
        "F_BRIEFING": {"status": C.PHASE_STATUS_F_BLOCKED},
        "status": C.PHASE_STATUS_B_TO_C_BLOCKED,
        "provenance": {"framework_contract_sha256": C.contract_hash()},
    }, exit_code=3)
    tampered = copy.deepcopy(envelope)
    tampered["stage_statuses"]["A_CATALOG"] = []

    valid, errors = verify_pipeline_report(tampered)

    assert valid is False
    assert any("stage_statuses" in error.lower() for error in errors)


def test_pipeline_report_rejects_unsupported_exit_code():
    envelope = bind_pipeline_report({
        "A_CATALOG": {"status": C.PHASE_STATUS_A_BLOCKED},
        "B_SCREEN": {"status": C.PHASE_STATUS_B_TO_C_BLOCKED},
        "E_VALIDATION": {"status": C.PHASE_STATUS_E_BLOCKED},
        "F_BRIEFING": {"status": C.PHASE_STATUS_F_BLOCKED},
        "status": C.PHASE_STATUS_B_TO_C_BLOCKED,
        "provenance": {"framework_contract_sha256": C.contract_hash()},
    }, exit_code=99)

    valid, errors = verify_pipeline_report(envelope)

    assert valid is False
    assert any("exit_code" in error.lower() for error in errors)


def test_pipeline_exit_five_is_distinct_from_a_gate_block():
    envelope = bind_pipeline_report({
        "A_CATALOG": {"status": C.PHASE_STATUS_A_BLOCKED},
        "B_SCREEN": {"status": C.PHASE_STATUS_B_TO_C_BLOCKED},
        "E_VALIDATION": {"status": C.PHASE_STATUS_E_BLOCKED},
        "F_BRIEFING": {"status": C.PHASE_STATUS_F_BLOCKED},
        "status": C.PHASE_STATUS_A_BLOCKED,
        "provenance": {"framework_contract_sha256": C.contract_hash()},
    }, exit_code=5)

    assert envelope["pipeline_status"] == "PIPELINE_FAILED"
    assert verify_pipeline_report(envelope) == (True, [])

    tampered = copy.deepcopy(envelope)
    tampered["pipeline_status"] = "PIPELINE_BLOCKED"
    valid, errors = verify_pipeline_report(tampered)
    assert valid is False
    assert any("pipeline_status" in error for error in errors)


def test_pipeline_checkpoint_requires_unchanged_inputs(tmp_path):
    source = tmp_path / "raw.json"
    source.write_text("{}", encoding="utf-8")
    fingerprint = pipeline_input_fingerprint([source], values={"phase": "A"})
    checkpoint = tmp_path / "checkpoint.json"
    write_pipeline_checkpoint(
        checkpoint,
        {"run_state": "INCOMPLETE", "stage": "B"},
        input_fingerprint=fingerprint,
    )
    loaded, errors = load_verified_pipeline_checkpoint(
        checkpoint, input_fingerprint=fingerprint)
    assert errors == []
    assert loaded is not None
    source.write_text("{\"changed\":true}", encoding="utf-8")
    changed = pipeline_input_fingerprint([source], values={"phase": "A"})
    loaded, errors = load_verified_pipeline_checkpoint(
        checkpoint, input_fingerprint=changed)
    assert loaded is None
    assert any("fingerprint" in error for error in errors)


def test_pipeline_checkpoint_rejects_terminal_state_as_non_resumable(tmp_path):
    source = tmp_path / "raw.json"
    source.write_text("{}", encoding="utf-8")
    fingerprint = pipeline_input_fingerprint([source], values={"phase": "A"})
    checkpoint = tmp_path / "checkpoint.json"
    write_pipeline_checkpoint(
        checkpoint,
        {"run_state": "TERMINAL", "stage": "pipeline_report"},
        input_fingerprint=fingerprint,
    )

    loaded, errors = load_verified_pipeline_checkpoint(
        checkpoint, input_fingerprint=fingerprint)

    assert loaded is None
    assert any("resumable" in error.lower() for error in errors)


def test_pipeline_wraps_direct_a_gate_with_catalog_artifact_provenance(tmp_path):
    catalog_output = tmp_path / "catalog.json"
    catalog_output.write_text("catalog", encoding="utf-8")
    direct_gate = bind_gate_artifact({
        "gate_id": C.GateId.A_CATALOG.value,
        "passed": True,
        "checks": {},
    })
    envelope = _ensure_a_catalog_envelope(
        direct_gate,
        {"catalog": catalog_output},
        create_controls_lock(ControlsConfig()),
    )
    valid, inner, outer, errors = verify_gate_input(
        envelope, expected_gate_id=C.GateId.A_CATALOG.value)
    assert valid is True, errors
    assert inner is not None and inner["passed"] is True
    assert outer is not None
    assert outer["status"] == C.PHASE_STATUS_A_READY
    assert outer["provenance"]["framework_contract_sha256"] == C.contract_hash()
    assert outer["provenance"]["catalog_artifact_sha256"]["catalog"] == \
        hashlib.sha256(b"catalog").hexdigest()


def test_briefing_artifact_binds_text_and_upstream_evidence(tmp_path):
    summary = {"status": "INDETERMINATE", "input_hashes": {}}
    a_gate = bind_artifact_envelope({
        "profile_id": "FRAMEWORK_V1_FULL",
        "artifact_kind": "A_CATALOG",
        "gate": bind_gate_artifact({
            "gate_id": C.GateId.A_CATALOG.value,
            "passed": True,
            "checks": {},
        }),
        "provenance": {"framework_contract_sha256": C.contract_hash()},
    })
    b_gate = bind_artifact_envelope({
        "gate": bind_gate_artifact({
            "gate_id": C.GateId.B_TO_C.value,
            "passed": True,
            "checks": {},
        }),
        "provenance": {"framework_contract_sha256": C.contract_hash()},
    })
    e_gate = write_validation_artifact(
        tmp_path / "e.json",
        summary,
        bind_gate_artifact({
            "gate_id": C.GateId.E_VALIDATION.value,
            "passed": True,
            "checks": {},
        }),
    )
    artifact = build_briefing_artifact(
        "research-only briefing\n",
        summary=summary,
        catalog_gate=a_gate,
        screen_gate=b_gate,
        validation_gate=e_gate,
        contract_hash=C.contract_hash(),
    )
    assert verify_briefing_artifact(artifact)[0] is True
    tampered = dict(artifact)
    tampered["briefing"] = "forged\n"
    assert verify_briefing_artifact(tampered)[0] is False
    forged_b = dict(artifact["b_gate"])
    forged_b["gate"] = dict(forged_b["gate"])
    forged_b["gate"]["passed"] = False
    forged_b = bind_artifact_envelope(forged_b)
    rebound = dict(artifact)
    rebound["b_gate"] = forged_b
    rebound["provenance"] = dict(rebound["provenance"])
    rebound["provenance"]["b_artifact_sha256"] = forged_b["artifact_sha256"]
    rebound = bind_artifact_envelope(rebound)
    assert verify_briefing_artifact(rebound)[0] is False

    with pytest.raises(ValueError, match="strict"):
        build_briefing_artifact(
            "research-only briefing\n",
            summary=summary,
            catalog_gate=a_gate,
            screen_gate=b_gate,
            validation_gate=e_gate,
            contract_hash=C.contract_hash(),
            strict=True,
        )


def test_strict_f_rejects_e_provenance_detached_from_embedded_upstreams(tmp_path):
    summary = {
        "status": "INDETERMINATE",
        "validation_errors": [],
        "input_hashes": {
            "catalog": "a" * 64,
            "controls": "b" * 64,
            "feature_config": "c" * 64,
            "holdout_plan": "d" * 64,
            "input_manifest": "e" * 64,
            "controls_lock": "f" * 64,
        },
    }
    a_gate = bind_artifact_envelope({
        "profile_id": "FRAMEWORK_V1_FULL",
        "artifact_kind": "A_CATALOG",
        "gate": bind_gate_artifact({
            "gate_id": C.GateId.A_CATALOG.value,
            "passed": True,
            "checks": {},
        }),
        "provenance": {"framework_contract_sha256": C.contract_hash()},
    })
    b_gate = bind_artifact_envelope({
        "status": C.PHASE_STATUS_SCREEN_RANKED,
        "phase_status": C.PHASE_STATUS_B_TO_C_READY,
        "gate_passed": True,
        "gate": bind_gate_artifact({
            "gate_id": C.GateId.B_TO_C.value,
            "passed": True,
            "checks": {},
        }),
        "provenance": {
            "framework_contract_sha256": C.contract_hash(),
            "a_gate_artifact_sha256": gate_input_artifact_sha256(a_gate),
            "controls_lock_sha256": "f" * 64,
            "input_manifest_sha256": "e" * 64,
            "input_manifest_hash_encoding": "canonical_json",
            "input_manifest_contract_bound": True,
            "input_manifest_canonical_authorized": True,
        },
    })
    e_gate = write_validation_artifact(
        tmp_path / "e.json",
        summary,
        bind_gate_artifact({
            "gate_id": C.GateId.E_VALIDATION.value,
            "passed": True,
            "checks": {},
        }),
        provenance={
            "framework_contract_sha256": C.contract_hash(),
            "a_gate_artifact_sha256": gate_input_artifact_sha256(a_gate),
            "b_artifact_sha256": gate_input_artifact_sha256(b_gate),
            "controls_lock_sha256": "f" * 64,
            "input_manifest_sha256": "e" * 64,
            "holdout_plan_sha256": "d" * 64,
            "summary_sha256": sha256_canonical(summary),
            "event_ids": ["E1"],
            "control_unit_ids": ["C1"],
            "claim_scope": "research_only_no_operational_authorization",
        },
        strict_contract=True,
    )
    artifact = build_briefing_artifact(
        "research-only briefing\n",
        summary=summary,
        catalog_gate=a_gate,
        screen_gate=b_gate,
        validation_gate=e_gate,
        contract_hash=C.contract_hash(),
        strict=True,
    )
    assert verify_briefing_artifact(artifact) == (True, [])

    forged_e = copy.deepcopy(e_gate)
    forged_e["provenance"] = dict(forged_e["provenance"])
    forged_e["provenance"]["a_gate_artifact_sha256"] = "1" * 64
    forged_e = bind_artifact_envelope(forged_e)
    with pytest.raises(ValueError, match="provenance"):
        build_briefing_artifact(
            "research-only briefing\n",
            summary=summary,
            catalog_gate=a_gate,
            screen_gate=b_gate,
            validation_gate=forged_e,
            contract_hash=C.contract_hash(),
            strict=True,
        )


def test_strict_f_requires_outer_authenticated_a_envelope():
    direct_a = bind_gate_artifact({
        "gate_id": C.GateId.A_CATALOG.value,
        "passed": True,
        "checks": {},
    })
    problems = _strict_gate_problems({}, direct_a, None, None)
    assert any("outer" in problem.lower() and "a_catalog" in problem.lower()
               for problem in problems)


def test_outer_a_envelope_is_verified_by_strict_f_boundary(tmp_path):
    summary = {"status": "INDETERMINATE", "input_hashes": {}}
    inner_a = bind_gate_artifact({
        "gate_id": C.GateId.A_CATALOG.value,
        "passed": True,
        "checks": {},
    })
    outer_a = bind_artifact_envelope({
        "profile_id": "FRAMEWORK_V1_FULL",
        "artifact_kind": "A_CATALOG",
        "gate": inner_a,
        "provenance": {
            "source": "test",
            "framework_contract_sha256": C.contract_hash(),
        },
    })
    valid, _, _, errors = verify_gate_input(
        outer_a, expected_gate_id=C.GateId.A_CATALOG.value)
    assert valid is True, errors

    b_gate = bind_artifact_envelope({
        "gate": bind_gate_artifact({
            "gate_id": C.GateId.B_TO_C.value,
            "passed": True,
            "checks": {},
        }),
        "provenance": {"framework_contract_sha256": C.contract_hash()},
    })
    e_gate = write_validation_artifact(
        tmp_path / "e.json",
        summary,
        bind_gate_artifact({
            "gate_id": C.GateId.E_VALIDATION.value,
            "passed": True,
            "checks": {},
        }),
    )
    artifact = build_briefing_artifact(
        "research-only briefing\n",
        summary=summary,
        catalog_gate=outer_a,
        screen_gate=b_gate,
        validation_gate=e_gate,
        contract_hash=C.contract_hash(),
    )
    assert verify_briefing_artifact(artifact) == (True, [])


def test_validation_artifact_keeps_explicit_provenance_inside_outer_hash(tmp_path):
    summary = {"status": "INDETERMINATE", "input_hashes": {}}
    gate = bind_gate_artifact({
        "gate_id": C.GateId.E_VALIDATION.value,
        "passed": False,
        "checks": {},
    })
    artifact = write_validation_artifact(
        tmp_path / "validation.json",
        summary,
        gate,
        provenance={"event_ids": ["E1"], "controls_lock_sha256": "a" * 64},
    )
    assert artifact["provenance"]["event_ids"] == ["E1"]
    assert verify_artifact_envelope(artifact) == (True, [])
    tampered = dict(artifact)
    tampered["provenance"] = {"event_ids": ["E2"]}
    assert verify_artifact_envelope(tampered)[0] is False


def test_preflight_records_worktree_process_and_input_inventory_keys():
    root = Path(__file__).resolve().parents[1]
    result = run_preflight(root, handoff_root=root / "data" /
                           "framework_inputs_v1_reconciled")
    checks = result["checks"]
    assert "git_diff_stat" in checks
    assert "worktrees" in checks
    assert "active_processes" in checks
    assert "handoff_inventory" in checks


def test_f_rejects_rebound_contract_drift_in_its_provenance(tmp_path):
    summary = {"status": "INDETERMINATE", "input_hashes": {}}
    a_gate = bind_artifact_envelope({
        "profile_id": "FRAMEWORK_V1_FULL",
        "artifact_kind": "A_CATALOG",
        "gate": bind_gate_artifact({
            "gate_id": C.GateId.A_CATALOG.value,
            "passed": True,
            "checks": {},
        }),
        "provenance": {"framework_contract_sha256": C.contract_hash()},
    })
    b_gate = bind_artifact_envelope({
        "gate": bind_gate_artifact({
            "gate_id": C.GateId.B_TO_C.value,
            "passed": True,
            "checks": {},
        }),
        "provenance": {"framework_contract_sha256": C.contract_hash()},
    })
    e_gate = write_validation_artifact(
        tmp_path / "e.json",
        summary,
        bind_gate_artifact({
            "gate_id": C.GateId.E_VALIDATION.value,
            "passed": True,
            "checks": {},
        }),
        provenance={"framework_contract_sha256": C.contract_hash()},
    )
    artifact = build_briefing_artifact(
        "research-only briefing\n",
        summary=summary,
        catalog_gate=a_gate,
        screen_gate=b_gate,
        validation_gate=e_gate,
        contract_hash=C.contract_hash(),
    )
    assert verify_briefing_artifact(artifact) == (True, [])

    forged = copy.deepcopy(artifact)
    forged["provenance"]["framework_contract_sha256"] = "0" * 64
    forged = bind_artifact_envelope(forged)
    valid, errors = verify_briefing_artifact(forged)
    assert valid is False
    assert any("framework contract" in error.lower() for error in errors)
