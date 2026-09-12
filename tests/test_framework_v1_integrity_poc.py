"""Focused tests for the narrow INTEGRITY_POC_V1 profile."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from nepal.framework_v1 import contract as C
from nepal.framework_v1.cli import main
from nepal.framework_v1.input_manifest import (
    canonical_input_manifest_hash,
    pretty_input_manifest_hash,
)
from nepal.framework_v1.integrity_poc import (
    IntegrityPocConfig,
    STATUS_BLOCKED,
    STATUS_INCOMPLETE,
    STATUS_PASS,
    _build_scope_manifest,
    benchmark_sha256,
    run_integrity_poc,
    verify_integrity_scope,
)
from nepal.framework_v1.provenance import (
    sha256_file,
    verify_artifact_envelope,
    write_deterministic_json,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_CONTRACT_SHA256 = sha256_file(REPO_ROOT / "nepal" / "feature_contract.py")
FRAMEWORK_CONTRACT_SHA256 = C.contract_hash()


def _artifact(root: Path, artifact_id: str = "real_payload",
              relative_path: str = "payload.bin") -> dict:
    payload = b"integrity-poc-test-payload\n"
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return {
        "artifact_id": artifact_id,
        "role": "B",
        "kind": "metadata",
        "relative_path": relative_path,
        "status": "READY",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
        "crs": "",
        "units": "bytes",
        "license": "test",
        "source_url": "https://example.invalid/test",
        "query_or_request": "test",
        "source_record_id": "test-record",
        "acquired_at": "2026-09-12",
        "observation_start": "",
        "observation_end": "",
        "publication_or_validity_date": "",
        "processing": "integrity PoC test fixture",
        "language_access_status": "en/accessible",
    }


def _scope(root: Path, artifact_id: str = "real_payload") -> dict:
    return _build_scope_manifest(
        _artifact(root, artifact_id), DATA_CONTRACT_SHA256,
        FRAMEWORK_CONTRACT_SHA256,
    )


def test_canonical_scope_loader_requires_both_contracts_and_anchor(tmp_path):
    manifest = _scope(tmp_path)
    result = verify_integrity_scope(
        manifest, tmp_path,
        trusted_manifest_sha256=manifest["manifest_sha256"],
        expected_data_contract_sha256=DATA_CONTRACT_SHA256,
        expected_framework_contract_sha256=FRAMEWORK_CONTRACT_SHA256,
        required_artifact_id="real_payload",
    )
    assert result["status"] == "PASS"
    assert result["ok"] is True
    assert result["checks"]["canonical_manifest_match"] is True
    assert result["checks"]["trusted_anchor_match"] is True
    assert result["checks"]["adapter_can_run_primary"] is True


def test_pretty_hash_is_rejected_even_when_legacy_adapter_accepts_compatibility(tmp_path):
    manifest = _scope(tmp_path)
    manifest["manifest_sha256"] = pretty_input_manifest_hash(manifest)
    result = verify_integrity_scope(
        manifest, tmp_path,
        trusted_manifest_sha256=manifest["manifest_sha256"],
        expected_data_contract_sha256=DATA_CONTRACT_SHA256,
        expected_framework_contract_sha256=FRAMEWORK_CONTRACT_SHA256,
        required_artifact_id="real_payload",
    )
    assert result["status"] == STATUS_BLOCKED
    assert any("canonical JSON" in error for error in result["errors"])


@pytest.mark.parametrize("field", [
    "expected_data_contract_sha256", "expected_framework_contract_sha256",
])
def test_missing_contract_hash_is_rejected(tmp_path, field):
    manifest = _scope(tmp_path)
    kwargs = {
        "trusted_manifest_sha256": manifest["manifest_sha256"],
        "expected_data_contract_sha256": DATA_CONTRACT_SHA256,
        "expected_framework_contract_sha256": FRAMEWORK_CONTRACT_SHA256,
        "required_artifact_id": "real_payload",
    }
    kwargs[field] = None
    result = verify_integrity_scope(manifest, tmp_path, **kwargs)
    assert result["status"] == STATUS_BLOCKED
    assert any("explicit lowercase SHA-256" in error for error in result["errors"])


def test_wrong_external_anchor_rejects_manifest(tmp_path):
    manifest = _scope(tmp_path)
    result = verify_integrity_scope(
        manifest, tmp_path,
        trusted_manifest_sha256="a" * 64,
        expected_data_contract_sha256=DATA_CONTRACT_SHA256,
        expected_framework_contract_sha256=FRAMEWORK_CONTRACT_SHA256,
        required_artifact_id="real_payload",
    )
    assert result["status"] == STATUS_BLOCKED
    assert any("trusted external anchor" in error for error in result["errors"])


def test_path_and_duplicate_tampering_is_rejected(tmp_path):
    manifest = _scope(tmp_path)
    tampered = copy.deepcopy(manifest)
    tampered["artifacts"][0]["relative_path"] = "../outside.bin"
    tampered["manifest_sha256"] = canonical_input_manifest_hash(tampered)
    result = verify_integrity_scope(
        tampered, tmp_path,
        trusted_manifest_sha256=tampered["manifest_sha256"],
        expected_data_contract_sha256=DATA_CONTRACT_SHA256,
        expected_framework_contract_sha256=FRAMEWORK_CONTRACT_SHA256,
        required_artifact_id="real_payload",
    )
    assert result["status"] == STATUS_BLOCKED
    assert any("path" in error.lower() for error in result["errors"])

    duplicate = copy.deepcopy(manifest)
    duplicate["artifacts"].append(copy.deepcopy(duplicate["artifacts"][0]))
    duplicate["artifact_count"] = 2
    duplicate["manifest_sha256"] = canonical_input_manifest_hash(duplicate)
    result = verify_integrity_scope(
        duplicate, tmp_path,
        trusted_manifest_sha256=duplicate["manifest_sha256"],
        expected_data_contract_sha256=DATA_CONTRACT_SHA256,
        expected_framework_contract_sha256=FRAMEWORK_CONTRACT_SHA256,
        required_artifact_id="real_payload",
    )
    assert result["status"] == STATUS_BLOCKED
    assert any("duplicate" in error.lower() for error in result["errors"])


def test_streaming_benchmark_reports_metrics_and_timeout(tmp_path):
    path = tmp_path / "payload.bin"
    path.write_bytes(b"x" * (64 * 1024))
    result = benchmark_sha256(
        path, timeout_seconds=30, warning_seconds=30,
        chunk_bytes=1024, max_peak_rss_mib=512,
    )
    assert result["status"] == "PASS"
    assert result["algorithm"] == "sha256"
    assert result["bytes_read"] == path.stat().st_size
    assert result["hash_calls"] == 1
    assert result["unique_paths_hashed"] == 1
    assert result["peak_rss_bytes"] > 0
    assert result["digest"] == hashlib.sha256(path.read_bytes()).hexdigest()

    timed_out = benchmark_sha256(
        path, timeout_seconds=0, warning_seconds=30,
        chunk_bytes=1024, max_peak_rss_mib=512,
    )
    assert timed_out["status"] == STATUS_INCOMPLETE
    assert timed_out["timeout"] is True


def test_run_poc_is_authenticated_and_does_not_call_ranking(tmp_path, monkeypatch):
    real_root = tmp_path / "real"
    source_manifest = _scope(real_root)
    source_path = tmp_path / "source_manifest.json"
    write_deterministic_json(source_path, source_manifest)
    output = tmp_path / "run" / "integrity_poc_report.json"

    import nepal.framework_v1.screen as screen

    def forbidden(*args, **kwargs):
        raise AssertionError("B ranking must not run in the integrity PoC")

    monkeypatch.setattr(screen, "rank_box", forbidden)
    config = IntegrityPocConfig(
        repo_root=REPO_ROOT,
        source_manifest=source_path,
        real_root=real_root,
        real_artifact_id="real_payload",
        output_path=output,
        expected_data_contract_sha256=DATA_CONTRACT_SHA256,
        expected_framework_contract_sha256=FRAMEWORK_CONTRACT_SHA256,
        generate_demo_anchor=True,
    )
    result = run_integrity_poc(config)
    assert result["poc_status"] == STATUS_PASS
    assert result["promotion_eligible"] is False
    assert result["tamper_benchmark"]["scenario_count"] == 17
    assert result["tamper_benchmark"]["failed_scenarios"] == []
    assert set(result["tamper_benchmark"]["scenarios"]) == {
        f"T{index:02d}" for index in range(1, 18)
    }
    assert result["tamper_benchmark"]["scenarios"]["T17"]["classification"] == (
        "ANCHOR_COMPROMISED"
    )
    assert result["tamper_benchmark"]["scenarios"]["T17"]["observed_verified"] is True
    assert output.exists()
    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert verify_artifact_envelope(persisted) == (True, [])
    assert persisted["poc_status"] == STATUS_PASS
    assert persisted["loader_smoke"]["real_artifact"]["ok"] is True
    assert persisted["loader_smoke"]["fixture"]["ok"] is True
    assert persisted["resource_guard"]["rss_measurements_present"] is True
    assert persisted["baseline"]["root_match"] is True
    assert persisted["hash_benchmark"]["real_artifact"]["hash_calls"] == 1
    assert persisted["hash_benchmark"]["real_artifact"]["unique_paths_hashed"] == 1
    assert persisted["hash_accounting"]["duplicate_hash_passes_in_measured_baseline"] == 0
    assert persisted["hash_accounting"]["loader_hash_cache_hits"] == 2
    assert persisted["loader_smoke"]["real_artifact"]["elapsed_seconds"] >= 0
    assert persisted["tamper_benchmark"]["elapsed_seconds"] >= 0
    assert persisted["report_writing"]["atomic"] is True
    assert persisted["report_writing"]["output_bytes"] > 0
    assert not list(output.parent.glob(f".{output.name}.*"))


def test_strict_scope_replay_requires_external_anchor_and_replays(tmp_path):
    real_root = tmp_path / "real"
    source_path = tmp_path / "source_manifest.json"
    write_deterministic_json(source_path, _scope(real_root))
    first_output = tmp_path / "first" / "report.json"
    first = run_integrity_poc(IntegrityPocConfig(
        repo_root=REPO_ROOT,
        source_manifest=source_path,
        real_root=real_root,
        real_artifact_id="real_payload",
        output_path=first_output,
        expected_data_contract_sha256=DATA_CONTRACT_SHA256,
        expected_framework_contract_sha256=FRAMEWORK_CONTRACT_SHA256,
        generate_demo_anchor=True,
    ))
    assert first["poc_status"] == STATUS_PASS
    scope_path = first_output.parent / "scope_manifest.json"
    scope_manifest = json.loads(scope_path.read_text(encoding="utf-8"))
    anchor = scope_manifest["manifest_sha256"]

    replay_output = tmp_path / "replay" / "report.json"
    replay = run_integrity_poc(IntegrityPocConfig(
        repo_root=REPO_ROOT,
        source_manifest=None,
        scope_manifest=scope_path,
        scope_root=real_root,
        real_root=real_root,
        real_artifact_id="real_payload",
        output_path=replay_output,
        expected_data_contract_sha256=DATA_CONTRACT_SHA256,
        expected_framework_contract_sha256=FRAMEWORK_CONTRACT_SHA256,
        trusted_manifest_sha256=anchor,
    ))
    assert replay["poc_status"] == STATUS_PASS
    assert replay["contract_binding"]["anchor_mode"] == "external"
    assert verify_artifact_envelope(json.loads(
        replay_output.read_text(encoding="utf-8"))) == (True, [])

    blocked_output = tmp_path / "blocked-replay" / "report.json"
    blocked = run_integrity_poc(IntegrityPocConfig(
        repo_root=REPO_ROOT,
        source_manifest=None,
        scope_manifest=scope_path,
        scope_root=real_root,
        real_root=real_root,
        real_artifact_id="real_payload",
        output_path=blocked_output,
        expected_data_contract_sha256=DATA_CONTRACT_SHA256,
        expected_framework_contract_sha256=FRAMEWORK_CONTRACT_SHA256,
    ))
    assert blocked["poc_status"] == STATUS_BLOCKED
    assert blocked["exit_code"] == 2


def test_run_poc_rejects_output_inside_live_root_without_writing(tmp_path):
    real_root = tmp_path / "real"
    source_path = tmp_path / "source_manifest.json"
    manifest = _scope(real_root)
    write_deterministic_json(source_path, manifest)
    output = real_root / "unsafe-report.json"
    config = IntegrityPocConfig(
        repo_root=REPO_ROOT,
        source_manifest=source_path,
        real_root=real_root,
        real_artifact_id="real_payload",
        output_path=output,
        expected_data_contract_sha256=DATA_CONTRACT_SHA256,
        expected_framework_contract_sha256=FRAMEWORK_CONTRACT_SHA256,
        generate_demo_anchor=True,
    )
    result = run_integrity_poc(config)
    assert result["poc_status"] == STATUS_BLOCKED
    assert output.exists() is False


def test_cli_integrity_poc_smoke_returns_zero(tmp_path, capsys):
    real_root = tmp_path / "real"
    source_manifest = _scope(real_root)
    source_path = tmp_path / "source_manifest.json"
    write_deterministic_json(source_path, source_manifest)
    output = tmp_path / "run" / "report.json"
    code = main([
        "integrity-poc",
        "--repo-root", str(REPO_ROOT),
        "--source-manifest", str(source_path),
        "--real-root", str(real_root),
        "--real-artifact-id", "real_payload",
        "--out", str(output),
        "--expected-data-contract-sha256", DATA_CONTRACT_SHA256,
        "--expected-framework-contract-sha256", FRAMEWORK_CONTRACT_SHA256,
        "--generate-demo-anchor",
    ])
    assert code == 0
    assert json.loads(output.read_text(encoding="utf-8"))["poc_status"] == STATUS_PASS
    assert "INTEGRITY_POC_PASS" in capsys.readouterr().out
