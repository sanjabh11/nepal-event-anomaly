"""CLI exit-code and deterministic-output tests for the coding lane."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

from nepal.framework_v1 import contract as C
from nepal.framework_v1.cli import main
from nepal.framework_v1.controls import ControlsConfig, create_controls_lock
from nepal.framework_v1.input_manifest import canonical_input_manifest_hash
from nepal.framework_v1.provenance import (bind_artifact_envelope,
                                            bind_gate_artifact,
                                            verify_artifact_envelope)
from nepal.framework_v1.validation import write_validation_artifact


def _manifest(root):
    payload = b"cli-payload"
    (root / "payload.bin").write_bytes(payload)
    artifact = {
        "artifact_id": "cli_payload", "role": "A", "kind": "metadata",
        "relative_path": "payload.bin", "status": "READY",
        "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload),
        "crs": "", "units": "", "license": "test",
        "source_url": "https://example.invalid", "query_or_request": "test",
        "source_record_id": "cli-1", "acquired_at": "2026-09-11",
        "observation_start": "", "observation_end": "",
        "publication_or_validity_date": "", "processing": "none",
        "language_access_status": "en/accessible",
    }
    manifest = {"schema_version": "2.0.0-reconciled",
                "contract_sha256": C.contract_hash(),
                "artifact_count": 1, "artifacts": [artifact]}
    manifest["manifest_sha256"] = canonical_input_manifest_hash(manifest)
    return manifest


def test_manifest_cli_passes_and_writes_deterministic_result(tmp_path, capsys):
    root = tmp_path / "root"
    root.mkdir()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_manifest(root)), encoding="utf-8")
    out = tmp_path / "verification.json"
    code = main(["manifest", "--manifest", str(manifest_path), "--root",
                 str(root), "--expected-contract-sha256", C.contract_hash(),
                 "--expected-framework-contract-sha256", C.contract_hash(),
                 "--require-primary", "--require-artifact", "cli_payload",
                 "--out", str(out)])
    assert code == 0
    assert json.loads(out.read_text())["ok"] is True
    assert "manifest_sha256" in capsys.readouterr().out


def test_strict_screen_cli_fails_closed_without_manifest(tmp_path):
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    out = tmp_path / "screen.json"
    code = main(["screen", "--config", str(config), "--strict", "--out", str(out)])
    assert code == 2
    result = json.loads(out.read_text())
    assert result["status"] == "BLOCKED"
    assert verify_artifact_envelope(result) == (True, [])
    assert result["blocked_reasons"]
    assert result["production_authorized"] is False


def test_strict_screen_does_not_overwrite_existing_output(tmp_path):
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    out = tmp_path / "screen.json"
    out.write_text("sentinel", encoding="utf-8")

    code = main(["screen", "--config", str(config), "--strict",
                 "--out", str(out)])

    assert code == 2
    assert out.read_text(encoding="utf-8") == "sentinel"


def test_strict_screen_rejects_checkpoint_inside_protected_root(tmp_path):
    repo_root = tmp_path / "repo"
    manifest_root = tmp_path / "manifest-root"
    expected_root = tmp_path / "expected-root"
    repo_root.mkdir()
    manifest_root.mkdir()
    expected_root.mkdir()
    out = tmp_path / "screen.json"
    checkpoint = manifest_root / "screen.checkpoint.json"

    code = main([
        "screen", "--strict", "--out", str(out),
        "--checkpoint", str(checkpoint),
        "--repo-root", str(repo_root),
        "--manifest-root", str(manifest_root),
        "--expected-root", str(expected_root),
    ])

    assert code == 2
    assert not out.exists()
    assert not checkpoint.exists()


def test_strict_validate_writes_blocked_diagnostic_when_upstreams_are_missing(
        tmp_path):
    events = tmp_path / "events.json"
    controls = tmp_path / "controls.json"
    lock = tmp_path / "lock.json"
    holdout = tmp_path / "holdout.json"
    out = tmp_path / "validation.json"
    events.write_text(json.dumps([{
        "event_id": "E1", "group": "G1", "score": 1.0,
        "event_date_min": "2020-01-01", "volume_m3": 9.0e6,
        "observation_availability": "AVAILABLE",
        "feature_available_from": {"insar": "2019-01-01"},
    }]), encoding="utf-8")
    controls.write_text(json.dumps([{
        "unit_id": "C1", "group": "G1", "score": 0.1,
        "observation_coverage": "FULL",
    }]), encoding="utf-8")
    lock.write_text(
        json.dumps(create_controls_lock(ControlsConfig()).to_dict()),
        encoding="utf-8")
    holdout_payload = {
        "groups": [{"group_id": "G1"}],
        "n_groups": 1,
        "frozen_before_eligibility_filtering": True,
    }
    holdout_payload["plan_sha256"] = hashlib.sha256(
        json.dumps(holdout_payload, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=True).encode("utf-8")).hexdigest()
    holdout.write_text(json.dumps(holdout_payload), encoding="utf-8")

    code = main([
        "validate", "--strict", "--events", str(events),
        "--controls", str(controls), "--lock", str(lock),
        "--holdout", str(holdout), "--summary", str(out),
    ])

    assert code == 4
    result = json.loads(out.read_text(encoding="utf-8"))
    assert verify_artifact_envelope(result) == (True, [])
    assert result["artifact_kind"] == "E_BLOCKED_DIAGNOSTIC"
    assert result["status"] == C.PHASE_STATUS_E_BLOCKED
    assert result["production_authorized"] is False


def test_strict_screen_cli_malformed_manifest_is_authenticated_block(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{not-json", encoding="utf-8")
    lock_path = tmp_path / "controls_lock.json"
    lock_path.write_text(
        json.dumps(create_controls_lock(ControlsConfig()).to_dict()),
        encoding="utf-8")
    a_gate_path = tmp_path / "a.json"
    a_gate_path.write_text(json.dumps(bind_gate_artifact({
        "gate_id": C.GateId.A_CATALOG.value,
        "passed": True,
        "checks": {},
    })), encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    out = tmp_path / "screen.json"
    code = main([
        "screen", "--config", str(config), "--strict",
        "--manifest", str(manifest_path), "--manifest-root", str(root),
        "--controls-lock", str(lock_path), "--a-gate", str(a_gate_path),
        "--repo-root", str(Path(__file__).resolve().parents[1]),
        "--expected-root", str(Path(__file__).resolve().parents[1]),
        "--expected-contract-sha256", C.contract_hash(),
        "--expected-framework-contract-sha256", C.contract_hash(),
        "--out", str(out),
    ])
    assert code == 2
    result = json.loads(out.read_text())
    assert verify_artifact_envelope(result) == (True, [])
    assert any("manifest" in error.lower() for error in result["errors"])


def test_pipeline_cli_is_wired_and_blocks_wrong_authoritative_root(tmp_path):
    # Pipeline output is required to be outside every input/protected root.
    out = tmp_path.parent / f"{tmp_path.name}-pipeline"
    code = main([
        "pipeline", "--repo-root", str(tmp_path),
        "--expected-root", "/Users/sanjayb/nepal-event-anomaly",
        "--raw", str(tmp_path / "raw.json"),
        "--manifest", str(tmp_path / "manifest.json"),
        "--manifest-root", str(tmp_path), "--out", str(out),
        "--expected-contract-sha256", C.contract_hash(),
        "--expected-framework-contract-sha256", C.contract_hash(),
    ])
    assert code == 2
    report = json.loads((out / "pipeline_report.json").read_text())
    assert report["A_CATALOG"]["status"] == C.PHASE_STATUS_A_BLOCKED
    assert report["B_SCREEN"]["status"] == C.PHASE_STATUS_B_TO_C_BLOCKED
    assert report["F_BRIEFING"]["status"] == C.PHASE_STATUS_F_BLOCKED


def test_pipeline_rejects_output_under_repo_before_failed_preflight_writes(
        tmp_path):
    repo_root = tmp_path / "repo"
    expected_root = tmp_path / "authoritative"
    manifest_root = tmp_path / "manifest-root"
    repo_root.mkdir()
    expected_root.mkdir()
    manifest_root.mkdir()
    raw = tmp_path / "raw.json"
    manifest = tmp_path / "manifest.json"
    raw.write_text("[]", encoding="utf-8")
    manifest.write_text("{}", encoding="utf-8")
    output = repo_root / "pipeline-output"

    code = main([
        "pipeline", "--repo-root", str(repo_root),
        "--expected-root", str(expected_root), "--raw", str(raw),
        "--manifest", str(manifest), "--manifest-root", str(manifest_root),
        "--out", str(output), "--expected-contract-sha256", C.contract_hash(),
        "--expected-framework-contract-sha256", C.contract_hash(),
        "--minimum-free-gib", "0",
    ])

    assert code == 2
    assert not output.exists()


def test_pipeline_rejects_nonempty_output_without_resume(tmp_path):
    repo_root = tmp_path / "repo"
    expected_root = tmp_path / "authoritative"
    manifest_root = tmp_path / "manifest-root"
    repo_root.mkdir()
    expected_root.mkdir()
    manifest_root.mkdir()
    raw = tmp_path / "raw.json"
    manifest = tmp_path / "manifest.json"
    raw.write_text("[]", encoding="utf-8")
    manifest.write_text("{}", encoding="utf-8")
    output = tmp_path / "pipeline-output"
    output.mkdir()
    stale = output / "b_screen.json"
    stale.write_text("stale-output", encoding="utf-8")

    code = main([
        "pipeline", "--repo-root", str(repo_root),
        "--expected-root", str(expected_root), "--raw", str(raw),
        "--manifest", str(manifest), "--manifest-root", str(manifest_root),
        "--out", str(output), "--expected-contract-sha256", C.contract_hash(),
        "--expected-framework-contract-sha256", C.contract_hash(),
        "--minimum-free-gib", "0",
    ])

    assert code == 2
    assert stale.read_text(encoding="utf-8") == "stale-output"
    assert not (output / "pipeline_report.json").exists()


def test_pipeline_maps_b_timeout_to_incomplete_exit_and_status(
        tmp_path, monkeypatch):
    from nepal.framework_v1 import adapters as adapters_module
    from nepal.framework_v1 import catalog as catalog_module
    from nepal.framework_v1 import preflight as preflight_module
    from nepal.framework_v1.adapters import BInputBundle

    repo_root = tmp_path / "repo"
    expected_root = tmp_path / "authoritative"
    manifest_root = tmp_path / "manifest-root"
    repo_root.mkdir()
    expected_root.mkdir()
    manifest_root.mkdir()
    raw = tmp_path / "raw.json"
    manifest = tmp_path / "manifest.json"
    raw.write_text("[]", encoding="utf-8")
    manifest.write_text("{}", encoding="utf-8")
    output = tmp_path / "pipeline-output"
    controls_lock = create_controls_lock(ControlsConfig())

    a_gate_path = tmp_path / "a_envelope.json"
    a_gate = bind_artifact_envelope({
        "profile_id": "FRAMEWORK_V1_FULL",
        "gate": bind_gate_artifact({
            "gate_id": C.GateId.A_CATALOG.value,
            "passed": True,
            "checks": {},
        }),
        "provenance": {"framework_contract_sha256": C.contract_hash()},
    })
    a_gate_path.write_text(json.dumps(a_gate), encoding="utf-8")

    b_timeout = bind_artifact_envelope({
        "status": C.B_TIMEOUT_STATUS,
        "gate_id": C.GateId.B_TO_C.value,
        "gate_passed": False,
        "phase_status": C.PHASE_STATUS_B_TO_C_BLOCKED,
        "gate": bind_gate_artifact({
            "gate_id": C.GateId.B_TO_C.value,
            "passed": False,
            "checks": {"bounded_execution": {"passed": False}},
        }),
        "errors": ["timed out"],
        "provenance": {"framework_contract_sha256": C.contract_hash()},
    })

    monkeypatch.setattr(preflight_module, "run_preflight", lambda *args, **kwargs: {
        "ok": True, "status": "BASELINE_READY", "failures": [],
    })
    monkeypatch.setattr(catalog_module, "build_catalog", lambda *args, **kwargs: {
        "controls_lock": controls_lock,
    })
    monkeypatch.setattr(catalog_module, "materialize_phase_a", lambda *args, **kwargs: {
        "envelope": a_gate_path,
    })
    monkeypatch.setattr(catalog_module, "verify_phase_a_envelope",
                        lambda *args, **kwargs: (True, []))
    monkeypatch.setattr(
        adapters_module, "load_verified_b_input_bundle",
        lambda *args, **kwargs: BInputBundle(
            status=C.PHASE_STATUS_LOAD_READY, verification=None))
    monkeypatch.setattr(adapters_module, "build_b_screen_from_bundle",
                        lambda *args, **kwargs: b_timeout)

    code = main([
        "pipeline", "--repo-root", str(repo_root),
        "--expected-root", str(expected_root), "--raw", str(raw),
        "--manifest", str(manifest), "--manifest-root", str(manifest_root),
        "--out", str(output), "--expected-contract-sha256", C.contract_hash(),
        "--expected-framework-contract-sha256", C.contract_hash(),
        "--minimum-free-gib", "0",
    ])

    report = json.loads((output / "pipeline_report.json").read_text())
    assert code == 4
    assert report["pipeline_status"] == "PIPELINE_INCOMPLETE"
    assert report["B_SCREEN"]["screen_status"] == C.B_TIMEOUT_STATUS


def test_pipeline_does_not_call_b_screen_when_verified_bundle_is_blocked(
        tmp_path, monkeypatch):
    """A blocked bundle must stop before any B ranking invocation."""
    from nepal.framework_v1 import adapters as adapters_module
    from nepal.framework_v1 import catalog as catalog_module
    from nepal.framework_v1 import preflight as preflight_module
    from nepal.framework_v1.controls import create_controls_lock
    from nepal.framework_v1.adapters import BInputBundle

    repo_root = tmp_path / "repo"
    expected_root = tmp_path / "authoritative"
    manifest_root = tmp_path / "manifest-root"
    repo_root.mkdir()
    expected_root.mkdir()
    manifest_root.mkdir()
    raw_path = tmp_path / "raw.json"
    raw_path.write_text("[]", encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    output = tmp_path / "pipeline-output"
    controls_lock = create_controls_lock(ControlsConfig())
    a_gate_path = tmp_path / "a_envelope.json"
    a_gate = bind_artifact_envelope({
        "profile_id": "FRAMEWORK_V1_FULL",
        "gate": bind_gate_artifact({
            "gate_id": C.GateId.A_CATALOG.value,
            "passed": True,
            "checks": {},
        }),
        "provenance": {},
    })
    a_gate_path.write_text(json.dumps(a_gate), encoding="utf-8")
    ranking_called = False

    def fake_build_b_screen(*args, **kwargs):
        nonlocal ranking_called
        ranking_called = True
        raise AssertionError("B ranking called for a blocked bundle")

    monkeypatch.setattr(preflight_module, "run_preflight", lambda *args, **kwargs: {
        "ok": True,
        "status": "BASELINE_READY",
        "failures": [],
    })
    monkeypatch.setattr(catalog_module, "build_catalog", lambda *args, **kwargs: {
        "controls_lock": controls_lock,
    })
    monkeypatch.setattr(catalog_module, "materialize_phase_a", lambda *args, **kwargs: {
        "envelope": a_gate_path,
    })
    monkeypatch.setattr(catalog_module, "verify_phase_a_envelope",
                        lambda *args, **kwargs: (True, []))
    monkeypatch.setattr(
        adapters_module, "load_verified_b_input_bundle",
        lambda *args, **kwargs: BInputBundle(
            status="BLOCKED", verification=None,
            errors=("required B artifact is incomplete",)))
    monkeypatch.setattr(adapters_module, "build_b_screen_from_bundle",
                        fake_build_b_screen)

    code = main([
        "pipeline", "--repo-root", str(repo_root),
        "--expected-root", str(expected_root), "--raw", str(raw_path),
        "--manifest", str(manifest_path), "--manifest-root", str(manifest_root),
        "--out", str(output), "--expected-contract-sha256", C.contract_hash(),
        "--expected-framework-contract-sha256", C.contract_hash(),
        "--minimum-free-gib", "0",
    ])

    report = json.loads((output / "pipeline_report.json").read_text())
    assert code == 2
    assert ranking_called is False
    assert report["B_SCREEN"]["status"] == C.PHASE_STATUS_B_TO_C_BLOCKED
    assert not (output / "b_screen.json").exists()


def test_pipeline_rejects_symlink_output_before_writing(tmp_path):
    repo_root = tmp_path / "repo"
    expected_root = tmp_path / "authoritative"
    manifest_root = tmp_path / "manifest-root"
    repo_root.mkdir()
    expected_root.mkdir()
    manifest_root.mkdir()
    raw = tmp_path / "raw.json"
    manifest = tmp_path / "manifest.json"
    raw.write_text("[]", encoding="utf-8")
    manifest.write_text("{}", encoding="utf-8")
    target = tmp_path / "pipeline-target"
    target.mkdir()
    output = tmp_path / "pipeline-link"
    output.symlink_to(target, target_is_directory=True)

    code = main([
        "pipeline", "--repo-root", str(repo_root),
        "--expected-root", str(expected_root), "--raw", str(raw),
        "--manifest", str(manifest), "--manifest-root", str(manifest_root),
        "--out", str(output), "--expected-contract-sha256", C.contract_hash(),
        "--expected-framework-contract-sha256", C.contract_hash(),
    ])

    assert code == 2
    assert not (target / "pipeline_report.json").exists()


def test_pipeline_rejects_descendant_symlink_on_resume_before_materializing(
        tmp_path, monkeypatch):
    """A resumed run must not write through a symlinked output child."""
    from nepal.framework_v1 import catalog as catalog_module
    from nepal.framework_v1 import orchestrator as orchestrator_module
    from nepal.framework_v1 import preflight as preflight_module
    from nepal.framework_v1 import input_manifest as input_manifest_module

    repo_root = tmp_path / "repo"
    expected_root = tmp_path / "authoritative"
    manifest_root = tmp_path / "manifest-root"
    repo_root.mkdir()
    expected_root.mkdir()
    manifest_root.mkdir()
    raw = tmp_path / "raw.json"
    manifest = tmp_path / "manifest.json"
    raw.write_text("[]", encoding="utf-8")
    manifest.write_text("{}", encoding="utf-8")

    output = tmp_path / "pipeline-output"
    output.mkdir()
    target = tmp_path / "redirected-output"
    target.mkdir()
    (output / "catalog").symlink_to(target, target_is_directory=True)

    fingerprint_paths = [
        raw,
        manifest,
        repo_root / input_manifest_module.AUTHORITATIVE_DATA_CONTRACT_PATH,
        repo_root / input_manifest_module.AUTHORITATIVE_FRAMEWORK_CONTRACT_PATH,
        repo_root / C.PREREGISTRATION_PATH,
    ]
    fingerprint = orchestrator_module.pipeline_input_fingerprint(
        fingerprint_paths,
        values={
            "expected_root": str(expected_root.resolve()),
            "manifest_root": str(manifest_root.resolve()),
            "expected_contract_sha256": C.contract_hash(),
            "expected_framework_contract_sha256": C.contract_hash(),
            "minimum_free_gib": 0.0,
            "timeout_seconds": None,
            "min_pairs": 30,
            "access_date": None,
            "required_features": [],
        },
    )
    orchestrator_module.write_pipeline_checkpoint(
        output / "pipeline_checkpoint.json",
        {"run_state": "INCOMPLETE", "stage": "A_CATALOG"},
        input_fingerprint=fingerprint,
    )

    monkeypatch.setattr(preflight_module, "run_preflight", lambda *args, **kwargs: {
        "ok": True, "status": "BASELINE_READY", "failures": [],
    })
    materialization_called = False

    def fail_if_materialized(*args, **kwargs):
        nonlocal materialization_called
        materialization_called = True
        raise AssertionError("pipeline materialized through a symlinked child")

    monkeypatch.setattr(catalog_module, "build_catalog", fail_if_materialized)

    code = main([
        "pipeline", "--resume", "--repo-root", str(repo_root),
        "--expected-root", str(expected_root), "--raw", str(raw),
        "--manifest", str(manifest), "--manifest-root", str(manifest_root),
        "--out", str(output), "--expected-contract-sha256", C.contract_hash(),
        "--expected-framework-contract-sha256", C.contract_hash(),
        "--minimum-free-gib", "0",
    ])

    assert code == 2
    assert materialization_called is False
    assert not (target / "pipeline_report.json").exists()


def test_strict_screen_cli_rejects_inline_components(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_manifest(root)), encoding="utf-8")
    lock_path = tmp_path / "controls_lock.json"
    lock_path.write_text(
        json.dumps(create_controls_lock(ControlsConfig()).to_dict()),
        encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "terrain": {"slope": {"values": [[1.0]]}},
        "exposure": {},
        "observability_by_unit": {},
    }), encoding="utf-8")
    out = tmp_path / "screen.json"
    code = main([
        "screen", "--config", str(config), "--strict",
        "--manifest", str(manifest_path), "--manifest-root", str(root),
        "--controls-lock", str(lock_path),
        "--expected-contract-sha256", C.contract_hash(),
        "--expected-framework-contract-sha256", C.contract_hash(),
        "--require-artifact", "cli_payload", "--out", str(out),
    ])
    assert code == 2
    result = json.loads(out.read_text())
    assert result["status"] == "BLOCKED"
    assert any("inline" in error.lower() for error in result["errors"])
    assert verify_artifact_envelope(result) == (True, [])


def test_contract_cli_verification_remains_available(capsys):
    assert main(["contract", "--verify",
                 "--repo-root", "/Users/sanjayb/nepal-event-anomaly"]) == 0
    assert "contract_sha256" in capsys.readouterr().out


def test_module_cli_does_not_double_import_itself():
    proc = subprocess.run(
        [sys.executable, "-B", "-m", "nepal.framework_v1.cli", "contract"],
        capture_output=True, text=True, check=False)
    assert proc.returncode == 0
    assert "RuntimeWarning" not in proc.stderr


def test_brief_cli_accepts_explicit_catalog_screen_and_validation_gates(tmp_path):
    summary = tmp_path / "summary.json"
    summary.write_text(json.dumps({"status": "INDETERMINATE",
                                   "input_hashes": {}}), encoding="utf-8")
    catalog_gate = tmp_path / "a.json"
    screen_gate = tmp_path / "b.json"
    validation_gate = tmp_path / "e.json"
    for path, passed in ((catalog_gate, False), (screen_gate, True),
                         (validation_gate, False)):
        path.write_text(json.dumps({"passed": passed}), encoding="utf-8")
    out = tmp_path / "brief.md"
    assert main(["brief", "--summary", str(summary),
                 "--catalog-gate", str(catalog_gate),
                 "--screen-gate", str(screen_gate),
                 "--validation-gate", str(validation_gate),
                 "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "A_CATALOG gate passed: False" in text
    assert "B_TO_C gate passed: True" in text
    assert "E_VALIDATION gate passed: False" in text


def test_strict_brief_cli_rejects_unbound_gate_mappings(tmp_path):
    summary = tmp_path / "summary.json"
    summary.write_text(json.dumps({"status": "INDETERMINATE"}), encoding="utf-8")
    paths = []
    for name in ("a.json", "b.json", "e.json"):
        path = tmp_path / name
        path.write_text(json.dumps({"passed": True}), encoding="utf-8")
        paths.append(path)
    out = tmp_path / "strict-brief.md"
    code = main(["brief", "--strict", "--summary", str(summary),
                 "--catalog-gate", str(paths[0]),
                 "--screen-gate", str(paths[1]),
                 "--validation-gate", str(paths[2]), "--out", str(out)])
    assert code == 4
    assert not out.exists()


def test_strict_brief_cli_rejects_verified_but_blocked_upstream_gates(tmp_path):
    summary = tmp_path / "summary.json"
    summary_payload = {"status": "INDETERMINATE", "validation_errors": []}
    summary.write_text(json.dumps(summary_payload), encoding="utf-8")
    catalog_gate = tmp_path / "a.json"
    catalog_gate.write_text(json.dumps(bind_gate_artifact({
        "gate_id": C.GateId.A_CATALOG.value, "passed": False,
        "checks": {},
    })), encoding="utf-8")
    b_gate = bind_artifact_envelope({
        "gate": bind_gate_artifact({
            "gate_id": C.GateId.B_TO_C.value, "passed": False,
            "checks": {},
        }),
        "provenance": {},
    })
    screen_gate = tmp_path / "b.json"
    screen_gate.write_text(json.dumps(b_gate), encoding="utf-8")
    validation_gate = tmp_path / "e.json"
    write_validation_artifact(
        validation_gate, summary_payload,
        bind_gate_artifact({"gate_id": C.GateId.E_VALIDATION.value,
                            "passed": False, "checks": {}}))
    out = tmp_path / "strict-brief.md"
    code = main(["brief", "--strict", "--summary", str(summary),
                 "--catalog-gate", str(catalog_gate),
                 "--screen-gate", str(screen_gate),
                 "--validation-gate", str(validation_gate), "--out", str(out)])
    assert code == 4
    assert not out.exists()
