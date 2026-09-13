"""Hermetic tests for framework_v1 lineage hardening (G01/G02/G03/G04/G06/
G08/G10/G11/G18/G19/G26/G27/G30/G38).

These tests never touch the candidate data lane: every fixture is synthesized
under ``tmp_path`` with canonical hashes computed from the fixture bytes.
"""
from __future__ import annotations

import json

import pytest

from nepal.framework_v1 import contract as C
from nepal.framework_v1.adapters import load_verified_b_input_bundle
from nepal.framework_v1.briefing import (build_f_envelope, verify_f_envelope,
                                         write_f_envelope)
from nepal.framework_v1.cli import main
from nepal.framework_v1.lineage import (RECEIPT_SCHEMA_VERSION,
                                        bind_generation_identity,
                                        disk_guard,
                                        discover_current_run_reports,
                                        environment_fingerprint,
                                        environment_fingerprint_sha256,
                                        verify_candidate_lineage,
                                        verify_generation_identity,
                                        verify_phase_a_envelope)
from nepal.framework_v1.provenance import (bind_gate_artifact,
                                           canonical_json,
                                           sha256_canonical, sha256_file)

DC_CONTRACT = "d" * 64
# The framework-contract binding is verified against the runtime contract
# hash, so hermetic fixtures must declare the real runtime value.
FC_CONTRACT = C.contract_hash()
GEN = "GEN-LINEAGE-TEST"
GEN_OLD = "GEN-STALE-2025"


def _artifact(artifact_id, relative, content, *, status="READY",
              role="context", kind="metadata", extra=None, root=None):
    write_payload = content if isinstance(content, bytes) else (
        content.encode("utf-8") if isinstance(content, str) else
        canonical_json(content).encode("utf-8"))
    relative.write_bytes(write_payload)
    relative_path = (str(relative.relative_to(root)) if root is not None
                     else str(relative))
    baseline = {
        "artifact_id": artifact_id,
        "role": role,
        "kind": kind,
        "relative_path": relative_path,
        "status": status,
        "sha256": sha256_file(relative),
        "bytes": len(write_payload),
        "crs": "EPSG:32645",
        "units": "n/a",
        "license": "CC-BY-4.0",
        "source_url": "https://example.org/source",
        "query_or_request": "fixture",
        "source_record_id": f"src-{artifact_id}",
        "acquired_at": "2026-01-01",
        "observation_start": "2001-01-01",
        "observation_end": "2026-01-01",
        "publication_or_validity_date": "2026-01-01",
        "processing": "none",
        "language_access_status": "en",
    }
    if extra:
        baseline.update(extra)
    return baseline
def _build_package(tmp_path, *, generation=GEN, tamper=None,
                   inventory_count=None):
    root = tmp_path / "pkg"
    artifacts_dir = root / "data"
    s2_dir = root / "sentinel2"
    artifacts_dir.mkdir(parents=True)
    s2_dir.mkdir(parents=True)

    alpha_path = artifacts_dir / "alpha.bin"
    alpha = _artifact("alpha", alpha_path, b"alpha-bytes-0001", root=root)

    beta_rel = artifacts_dir / "beta.json"
    receipt = {
        "receipt_schema_version": RECEIPT_SCHEMA_VERSION,
        "created_by": "lineage-test",
        "note": "fixture receipt",
    }
    receipt["receipt_sha256"] = sha256_canonical({
        k: v for k, v in receipt.items() if k != "receipt_sha256"})
    beta = _artifact("beta", beta_rel, {"payload": 1},
                     extra={"receipt": receipt}, root=root)

    asset_path = s2_dir / "asset_manifest.json"
    asset_bytes = canonical_json({"scenes": ["S2A-0001"], "count": 1})
    asset = _artifact("sentinel2_asset_manifest", asset_path,
                      asset_bytes.encode("utf-8"), root=root)

    selected_path = s2_dir / "selected.json"
    selected = _artifact(
        "sentinel2_selected_manifest", selected_path,
        {"status": "READY",
         "asset_manifest_artifact_id": "sentinel2_asset_manifest",
         "asset_manifest_sha256": sha256_file(asset_path),
         "selected_scene_count": 1}, root=root)

    original_alpha_sha = alpha["sha256"]
    if tamper == "alpha_bytes":
        alpha_path.write_bytes(b"alpha-bytes-9000")
        alpha["sha256"] = original_alpha_sha  # declared hash now stale
    if tamper == "alpha_hash":
        alpha["sha256"] = "e" * 64
    if tamper == "alpha_traversal":
        alpha["relative_path"] = "../escape.bin"
    if tamper == "alpha_absolute":
        alpha["relative_path"] = "/Users/someone/run/out/escape.bin"
    if tamper == "receipt_no_schema":
        receipt.pop("receipt_schema_version", None)
        beta["receipt"] = receipt
    if tamper == "receipt_bad_hash":
        receipt["receipt_sha256"] = "a" * 64
        beta["receipt"] = receipt
    if tamper == "s2_stale_asset_hash":
        selected_payload = json.loads(selected_path.read_text("utf-8"))
        selected_payload["asset_manifest_sha256"] = "b" * 64
        selected_path.write_text(canonical_json(selected_payload),
                                 encoding="utf-8")
    if tamper == "generation_mismatch":
        generation = GEN_OLD

    artifacts = [alpha, beta, selected, asset]
    manifest = {
        "schema_version": "1.0",
        "generated_at": "2026-01-01T00:00:00Z",
        "stage_completed": "materialize",
        "stages_pending": [],
        "contract_sha256": DC_CONTRACT,
        "framework_contract_sha256": FC_CONTRACT,
        "candidate_generation_id": generation,
        "artifact_count": len(artifacts),
        "inventory_count": (len(artifacts) if inventory_count is None
                            else inventory_count),
        "statuses": [],
        "required_artifact_ids": [],
        "artifacts": artifacts,
    }
    from nepal.framework_v1.input_manifest import canonical_input_manifest_hash
    manifest["manifest_sha256"] = canonical_input_manifest_hash(manifest)
    (root / "manifest.json").write_text(canonical_json(manifest),
                                        encoding="utf-8")
    return root, root / "manifest.json", manifest["manifest_sha256"], manifest
def _waiver_file(tmp_path, entry_status="READY", resolved=True):
    path = tmp_path / "waiver.json"
    path.write_text(canonical_json({
        "waivers": {"alpha": {"status": entry_status,
                              "resolved": resolved}}}), encoding="utf-8")
    return path


def _audit_packet(tmp_path, generation=GEN, manifest_sha256=None):
    path = tmp_path / "audit_packet.json"
    path.write_text(canonical_json({
        "candidate_generation_id": generation,
        "manifest_sha256": manifest_sha256}), encoding="utf-8")
    return path


def _lineage(root, manifest_path, expected_sha, *, waiver=None,
             audit=None, trusted=None, generation=GEN):
    return verify_candidate_lineage(
        manifest_path, root,
        expected_manifest_sha256=expected_sha,
        expected_data_contract_sha256=DC_CONTRACT,
        expected_framework_contract_sha256=FC_CONTRACT,
        candidate_generation_id=generation,
        trusted_manifest_file_sha256=trusted,
        waiver_path=waiver, audit_packet_path=audit)


class TestGenerationIdentity:
    def test_bind_and_verify(self):
        bound = bind_generation_identity(
            {"x": 1}, candidate_generation_id=GEN,
            manifest_sha256="a" * 64, run_id="run-1")
        ok, errors = verify_generation_identity(
            bound, candidate_generation_id=GEN, manifest_sha256="a" * 64)
        assert ok and not errors

    def test_mixed_generation_is_rejected(self):
        bound = bind_generation_identity(
            {"x": 1}, candidate_generation_id=GEN, manifest_sha256="a" * 64)
        ok, errors = verify_generation_identity(
            bound, candidate_generation_id=GEN_OLD, manifest_sha256="a" * 64)
        assert not ok
        assert any("candidate_generation_id mismatch" in e for e in errors)


class TestPhaseAEnvelope:
    def _full_gate(self, manifest_sha256="a" * 64, passed=True):
        gate = {
            "gate_id": C.GateId.A_CATALOG.value,
            "passed": passed,
            "checks": {},
            "n_eligible": 4,
            "catalog_sha256": "b" * 64,
            "holdout_plan_sha256": "c" * 64,
            "mechanism_validation": {
                "catalog_source_validated": True,
                "mechanism_independently_adjudicated": False},
        }
        gate = bind_generation_identity(
            gate, candidate_generation_id=GEN, manifest_sha256=manifest_sha256)
        return bind_gate_artifact(gate)

    def test_minimal_synthetic_gate_is_rejected(self):
        ok, errors = verify_phase_a_envelope(
            {"gate_id": "A_CATALOG", "passed": True})
        assert not ok
        assert any("catalog_sha256" in e for e in errors)
        assert any("self-hash" in e for e in errors)

    def test_full_gate_accepts_and_generation_mismatch_rejects(self):
        gate = self._full_gate()
        ok, errors = verify_phase_a_envelope(
            gate, expected_manifest_sha256="a" * 64,
            candidate_generation_id=GEN, require_generation_identity=True)
        assert ok, errors
        ok, errors = verify_phase_a_envelope(
            gate, expected_manifest_sha256="a" * 64,
            candidate_generation_id=GEN_OLD, require_generation_identity=True)
        assert not ok
        assert any("mismatch" in e for e in errors)
class TestCandidateLineage:
    def test_happy_path_verifies_all_links(self, tmp_path):
        root, manifest_path, expected, _ = _build_package(tmp_path)
        waiver = _waiver_file(tmp_path)
        audit = _audit_packet(tmp_path, manifest_sha256=expected)
        result = _lineage(root, manifest_path, expected, waiver=waiver,
                          audit=audit)
        assert result.ok, result.errors
        assert result.checks["inventory"]["files_verified"] == 4
        assert result.checks["sentinel2_chain"][
            "sentinel2_selected_manifest"]["asset_manifest_verified"] is True
        assert result.checks["receipts"]["beta"]["receipt_sha256_verified"]

    @pytest.mark.parametrize("tamper,needle", [
        ("alpha_bytes", "checksum mismatch"),
        ("alpha_hash", "checksum mismatch"),
        ("alpha_traversal", "traversal"),
        ("alpha_absolute", "absolute production path"),
        ("receipt_no_schema", "receipt_schema_version"),
        ("receipt_bad_hash", "receipt canonical self-hash mismatch"),
        ("generation_mismatch", "candidate_generation_id mismatch"),
    ])
    def test_fail_closed_on_contradictions(self, tmp_path, tamper, needle):
        root, manifest_path, expected, _ = _build_package(
            tmp_path, tamper=tamper,
            generation=(GEN_OLD if tamper == "generation_mismatch" else GEN))
        result = _lineage(root, manifest_path, expected)
        assert not result.ok
        assert any(needle in error for error in result.errors)

    def test_inventory_count_mismatch_is_fatal(self, tmp_path):
        root, manifest_path, expected, _ = _build_package(
            tmp_path, inventory_count=99)
        result = _lineage(root, manifest_path, expected)
        assert not result.ok
        assert any("inventory count mismatch" in e for e in result.errors)

    def test_waiver_manifest_contradiction_is_fatal(self, tmp_path):
        root, manifest_path, expected, _ = _build_package(tmp_path)
        waiver = _waiver_file(tmp_path, entry_status="INCOMPLETE",
                              resolved=True)
        result = _lineage(root, manifest_path, expected, waiver=waiver)
        assert not result.ok
        assert any("declares resolved" in e for e in result.errors)

    def test_audit_packet_generation_mismatch_is_fatal(self, tmp_path):
        root, manifest_path, expected, _ = _build_package(tmp_path)
        audit = _audit_packet(tmp_path, generation=GEN_OLD)
        result = _lineage(root, manifest_path, expected, audit=audit)
        assert not result.ok
        assert any("audit packet" in e for e in result.errors)

    def test_s2_asset_manifest_hash_staleness_is_fatal(self, tmp_path):
        root, manifest_path, expected, _ = _build_package(
            tmp_path, tamper="s2_stale_asset_hash")
        result = _lineage(root, manifest_path, expected)
        assert not result.ok
        assert any("asset-manifest hash is stale" in e for e in result.errors)

    def test_trusted_file_digest_mismatch_is_fatal(self, tmp_path):
        root, manifest_path, expected, _ = _build_package(tmp_path)
        result = _lineage(root, manifest_path, expected, trusted="0" * 64)
        assert not result.ok
        assert any("trusted manifest file digest mismatch" in e
                   for e in result.errors)
class TestDiscoveryAndEnvelope:
    def _write_report(self, root, name, payload):
        path = root / name / "pipeline_report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(canonical_json(payload), encoding="utf-8")
        return path

    def test_only_current_generation_reports_are_accepted(self, tmp_path):
        runs = tmp_path / "runs"
        self._write_report(runs, "current", {
            "candidate_generation_id": GEN,
            "manifest_sha256": "a" * 64})
        self._write_report(runs, "stale", {
            "candidate_generation_id": GEN_OLD,
            "manifest_sha256": "b" * 64})
        self._write_report(runs, "null-shaped", {})
        malformed = runs / "garbage" / "pipeline_report.json"
        malformed.parent.mkdir(parents=True, exist_ok=True)
        malformed.write_text("not json{{", encoding="utf-8")
        result = discover_current_run_reports(
            runs, candidate_generation_id=GEN, manifest_sha256="a" * 64)
        assert result["ok"]
        assert result["accepted_count"] == 1
        assert result["rejected_count"] == 3
        assert any("stale generation" in entry["reason"]
                   for entry in result["rejected"])

    def test_strict_f_envelope_round_trip_and_tamper(self, tmp_path):
        from nepal.framework_v1.briefing import LIABILITY, NOT_EVACUATION
        summary = {"status": "INDETERMINATE", "input_hashes": {}}
        envelope = build_f_envelope(
            summary, contract_hash="a" * 64,
            candidate_generation_id=GEN, manifest_sha256="b" * 64,
            briefing_text="\n".join([NOT_EVACUATION, LIABILITY]))
        path = tmp_path / "f_envelope.json"
        write_f_envelope(path, envelope)
        ok, errors = verify_f_envelope(path)
        assert ok, errors
        tampered = json.loads(path.read_text("utf-8"))
        tampered["candidate_generation_id"] = GEN_OLD
        path.write_text(canonical_json(tampered), encoding="utf-8")
        ok, _ = verify_f_envelope(path)
        assert not ok

    def test_disk_guard_available_and_passes(self, tmp_path):
        result = disk_guard(tmp_path, minimum_free_bytes=1024)
        assert result["passed"] is True
        with pytest.raises(C.ContractViolation):
            disk_guard(tmp_path, minimum_free_bytes=10 ** 18)

    def test_environment_fingerprint_is_shaped(self):
        fp = environment_fingerprint()
        assert fp["python_version"]
        assert environment_fingerprint_sha256() == environment_fingerprint_sha256()


class TestStrictBundleLoader:
    def test_in_memory_manifest_rejected_with_trusted_digest(self, tmp_path):
        manifest = {"artifacts": [], "statuses": []}
        bundle = load_verified_b_input_bundle(
            tmp_path, manifest,
            expected_contract_sha256=DC_CONTRACT,
            expected_framework_contract_sha256=FC_CONTRACT,
            trusted_manifest_file_sha256="e" * 64,
            candidate_generation_id=GEN)
        assert not bundle.ok
        assert any("disk" in error for error in bundle.errors)


class TestLineageCLI:
    def test_cli_lineage_exit_2_on_contradiction(self, tmp_path):
        root, manifest_path, expected, _ = _build_package(
            tmp_path, tamper="alpha_hash")
        code = main(["lineage", "--manifest", str(manifest_path),
                     "--root", str(root),
                     "--expected-manifest-sha256", expected,
                     "--expected-data-contract-sha256", DC_CONTRACT,
                     "--expected-framework-contract-sha256", FC_CONTRACT,
                     "--candidate-generation-id", GEN])
        assert code == 2

    def test_cli_find_reports_exit_codes(self, tmp_path):
        runs = tmp_path / "runs"
        (runs / "current").mkdir(parents=True)
        (runs / "current" / "pipeline_report.json").write_text(
            canonical_json({"candidate_generation_id": GEN,
                            "manifest_sha256": "a" * 64}),
            encoding="utf-8")
        code = main(["find-reports", "--runs-root", str(runs),
                     "--candidate-generation-id", GEN])
        assert code == 0
        code = main(["find-reports", "--runs-root", str(runs),
                     "--candidate-generation-id", GEN_OLD])
        assert code == 2
