"""CLI exit-code and deterministic-output tests for the coding lane."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys

from nepal.framework_v1 import contract as C
from nepal.framework_v1.cli import main
from nepal.framework_v1.controls import ControlsConfig, create_controls_lock
from nepal.framework_v1.input_manifest import canonical_input_manifest_hash
from nepal.framework_v1.provenance import (bind_artifact_envelope,
                                            bind_gate_artifact)
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
    assert json.loads(out.read_text())["status"] == "BLOCKED"


def test_pipeline_cli_is_wired_and_blocks_wrong_authoritative_root(tmp_path):
    out = tmp_path / "pipeline"
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
    assert code == 3
    result = json.loads(out.read_text())
    assert result["status"] == "BLOCKED"
    assert any("inline" in error.lower() for error in result["errors"])


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
