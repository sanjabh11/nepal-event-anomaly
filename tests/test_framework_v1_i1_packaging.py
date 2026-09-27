"""I1 additive successor packaging tests (N02).

The v2 candidate bytes are immutable, but its manifest and audit packet carry
two provenance defects:

* ``manifest.recovery_audit_artifact`` still points at the historical v1
  recovery audit packet;
* the v2 audit packet's ``superseded_by`` points back at v1 — an inverted
  supersession arrow.

I1 closes these with an *additive* successor package: a new canonical manifest
bound to generation ``-i1-`` and to the unchanged v2 artifact root, plus a
corrected audit-binding envelope that records v2 as the current parent and v1
as the historical predecessor.  Nothing in the v2 candidate is edited.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from nepal.framework_v1 import i1_packaging as i1
from nepal.framework_v1.input_manifest import canonical_input_manifest_hash
from nepal.framework_v1.provenance import (sha256_file, verify_artifact_envelope,
                                         write_deterministic_json)

PARENT_GEN = "d1-remediated-provenance-recovery-v2-20260913"
I1_GEN = "d1-remediated-provenance-recovery-v2-i1-20260913"


def _file(root, rel, content):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p


def _parent_manifest(root):
    """Minimal v2-shaped manifest over a small hermetic artifact root."""
    data = _file(root, "catalog/hma_events_all.json", b'{"rows": 112}')
    audit_v2 = _file(root, "provenance/d1_remediation_audit.json",
                     b'{"receipt_type": "FRAMEWORK_V1_D1_REMEDIATION_AUDIT_V2"}')
    audit_v1 = _file(root, "provenance/d1_recovery_audit.json",
                     b'{"audit_version": "D1_PROVENANCE_RECOVERY_AUDIT_V1"}')
    waivers = _file(root, "provenance/waivers.json", b'{"waivers": []}')
    manifest = {
        "schema_version": "d1-input-manifest/v1",
        "candidate_generation_id": PARENT_GEN,
        "artifact_count": 2,
        "inventory_count": 2,
        "status_counts": {"READY": 2},
        "artifacts": [
            {"artifact_id": "hma_events_all", "status": "READY",
             "relative_path": "catalog/hma_events_all.json",
             "sha256": hashlib.sha256(data.read_bytes()).hexdigest()},
            {"artifact_id": "waivers", "status": "READY",
             "relative_path": "provenance/waivers.json",
             "sha256": hashlib.sha256(waivers.read_bytes()).hexdigest()},
        ],
        "package_inventory": [
            {"relative_path": "catalog/hma_events_all.json",
             "bytes": data.stat().st_size,
             "sha256": hashlib.sha256(data.read_bytes()).hexdigest(),
             "artifact_id": "hma_events_all"},
            {"relative_path": "provenance/d1_recovery_audit.json",
             "bytes": audit_v1.stat().st_size,
             "sha256": hashlib.sha256(audit_v1.read_bytes()).hexdigest()},
            {"relative_path": "provenance/waivers.json",
             "bytes": waivers.stat().st_size,
             "sha256": hashlib.sha256(waivers.read_bytes()).hexdigest()},
        ],
        "package_inventory_policy": {
            "excluded": ["manifest.json",
                         "provenance/d1_remediation_audit.json"],
            "reason": "self-referential files are excluded"},
        "recovery_audit_artifact": {
            "relative_path": "provenance/d1_recovery_audit.json",
            "sha256": hashlib.sha256(audit_v1.read_bytes()).hexdigest()},
        "waiver_artifact": {
            "relative_path": "provenance/waivers.json",
            "sha256": hashlib.sha256(waivers.read_bytes()).hexdigest()},
        "promotion_eligible": False,
        "security_authority": "NOT_PROVEN",
    }
    manifest["manifest_sha256"] = canonical_input_manifest_hash(manifest)
    return manifest, audit_v2, audit_v1


def _parent_digests(tmp_path):
    root = tmp_path / "candidate-v2"
    root.mkdir()
    manifest, audit_v2, audit_v1 = _parent_manifest(root)
    manifest_path = root / "manifest.json"
    write_deterministic_json(manifest_path, manifest)
    return {
        "root": root,
        "manifest": manifest,
        "manifest_path": manifest_path,
        "manifest_sha256": manifest["manifest_sha256"],
        "manifest_file_sha256": sha256_file(manifest_path),
        "audit_v2_rel": "provenance/d1_remediation_audit.json",
        "audit_v2_sha256": sha256_file(audit_v2),
        "audit_v1_rel": "provenance/d1_recovery_audit.json",
        "audit_v1_sha256": sha256_file(audit_v1),
    }


class TestSuccessorManifest:
    def test_binds_new_generation_and_parent(self, tmp_path):
        parent = _parent_digests(tmp_path)
        successor = i1.build_successor_manifest(
            parent_manifest=parent["manifest"],
            generation_id=I1_GEN,
            parent_generation_id=PARENT_GEN,
            parent_manifest_sha256=parent["manifest_sha256"],
            parent_manifest_file_sha256=parent["manifest_file_sha256"],
            active_audit_packet={
                "relative_path": parent["audit_v2_rel"],
                "sha256": parent["audit_v2_sha256"]},
            i1_audit_binding={
                "run_root_relative_path":
                    "i1-packaging-20260913/provenance/i1_audit_binding.json",
                "sha256": "ab" * 32},
            code_revision="f" * 40)
        assert successor["candidate_generation_id"] == I1_GEN
        assert successor["successor_of_generation_id"] == PARENT_GEN
        assert (successor["successor_of_manifest_declared_sha256"]
                == parent["manifest_sha256"])
        assert (successor["successor_of_manifest_file_sha256"]
                == parent["manifest_file_sha256"])
        declared = successor["manifest_sha256"]
        assert canonical_input_manifest_hash(successor) == declared
        assert declared != parent["manifest_sha256"]

    def test_preserves_artifacts_counts_and_inventory(self, tmp_path):
        parent = _parent_digests(tmp_path)
        successor = i1.build_successor_manifest(
            parent_manifest=parent["manifest"],
            generation_id=I1_GEN,
            parent_generation_id=PARENT_GEN,
            parent_manifest_sha256=parent["manifest_sha256"],
            parent_manifest_file_sha256=parent["manifest_file_sha256"],
            active_audit_packet={
                "relative_path": parent["audit_v2_rel"],
                "sha256": parent["audit_v2_sha256"]},
            i1_audit_binding={
                "run_root_relative_path": "i1-packaging-20260913/x.json",
                "sha256": "ab" * 32},
            code_revision="f" * 40)
        for field in ("artifacts", "artifact_count", "inventory_count",
                      "status_counts", "package_inventory",
                      "package_inventory_policy", "waiver_artifact",
                      "promotion_eligible", "security_authority"):
            assert successor[field] == parent["manifest"][field]

    def test_audit_reference_is_current_not_historical(self, tmp_path):
        parent = _parent_digests(tmp_path)
        successor = i1.build_successor_manifest(
            parent_manifest=parent["manifest"],
            generation_id=I1_GEN,
            parent_generation_id=PARENT_GEN,
            parent_manifest_sha256=parent["manifest_sha256"],
            parent_manifest_file_sha256=parent["manifest_file_sha256"],
            active_audit_packet={
                "relative_path": parent["audit_v2_rel"],
                "sha256": parent["audit_v2_sha256"]},
            i1_audit_binding={
                "run_root_relative_path": "i1-packaging-20260913/x.json",
                "sha256": "ab" * 32},
            code_revision="f" * 40)
        assert (successor["recovery_audit_artifact"]["relative_path"]
                == parent["audit_v2_rel"])
        assert (successor["recovery_audit_artifact"]["sha256"]
                == parent["audit_v2_sha256"])
        assert successor["recovery_audit_artifact"]["relative_path"] \
            != parent["audit_v1_rel"]

    def test_verify_detects_parent_byte_change(self, tmp_path):
        parent = _parent_digests(tmp_path)
        successor = i1.build_successor_manifest(
            parent_manifest=parent["manifest"],
            generation_id=I1_GEN,
            parent_generation_id=PARENT_GEN,
            parent_manifest_sha256=parent["manifest_sha256"],
            parent_manifest_file_sha256=parent["manifest_file_sha256"],
            active_audit_packet={
                "relative_path": parent["audit_v2_rel"],
                "sha256": parent["audit_v2_sha256"]},
            i1_audit_binding={
                "run_root_relative_path": "i1-packaging-20260913/x.json",
                "sha256": "ab" * 32},
            code_revision="f" * 40)
        ok, problems = i1.verify_successor_manifest(
            successor, expected_generation_id=I1_GEN,
            parent_manifest_sha256=parent["manifest_sha256"],
            parent_manifest_file_sha256=parent["manifest_file_sha256"],
            artifact_root=parent["root"])
        assert ok, problems
        target = parent["root"] / "catalog/hma_events_all.json"
        target.write_bytes(b'{"rows": 113}')
        ok, problems = i1.verify_successor_manifest(
            successor, expected_generation_id=I1_GEN,
            parent_manifest_sha256=parent["manifest_sha256"],
            parent_manifest_file_sha256=parent["manifest_file_sha256"],
            artifact_root=parent["root"])
        assert not ok
        assert any("checksum" in p or "byte" in p or "missing" in p
                   for p in problems)

    def test_verify_rejects_wrong_generation(self, tmp_path):
        parent = _parent_digests(tmp_path)
        successor = i1.build_successor_manifest(
            parent_manifest=parent["manifest"],
            generation_id=I1_GEN,
            parent_generation_id=PARENT_GEN,
            parent_manifest_sha256=parent["manifest_sha256"],
            parent_manifest_file_sha256=parent["manifest_file_sha256"],
            active_audit_packet={
                "relative_path": parent["audit_v2_rel"],
                "sha256": parent["audit_v2_sha256"]},
            i1_audit_binding={
                "run_root_relative_path": "i1-packaging-20260913/x.json",
                "sha256": "ab" * 32},
            code_revision="f" * 40)
        ok, problems = i1.verify_successor_manifest(
            successor, expected_generation_id=PARENT_GEN,
            parent_manifest_sha256=parent["manifest_sha256"],
            parent_manifest_file_sha256=parent["manifest_file_sha256"])
        assert not ok
        assert any("generation" in p for p in problems)

    def test_verify_rejects_absolute_canonical_paths(self, tmp_path):
        parent = _parent_digests(tmp_path)
        successor = i1.build_successor_manifest(
            parent_manifest=parent["manifest"],
            generation_id=I1_GEN,
            parent_generation_id=PARENT_GEN,
            parent_manifest_sha256=parent["manifest_sha256"],
            parent_manifest_file_sha256=parent["manifest_file_sha256"],
            active_audit_packet={
                "relative_path": parent["audit_v2_rel"],
                "sha256": parent["audit_v2_sha256"]},
            i1_audit_binding={
                "run_root_relative_path": "/etc/passwd",
                "sha256": "ab" * 32},
            code_revision="f" * 40)
        ok, problems = i1.verify_successor_manifest(
            successor, expected_generation_id=I1_GEN,
            parent_manifest_sha256=parent["manifest_sha256"],
            parent_manifest_file_sha256=parent["manifest_file_sha256"])
        assert not ok
        assert any("absolute" in p for p in problems)


class TestI1AuditBinding:
    def _build(self, tmp_path):
        parent = _parent_digests(tmp_path)
        successor = i1.build_successor_manifest(
            parent_manifest=parent["manifest"],
            generation_id=I1_GEN,
            parent_generation_id=PARENT_GEN,
            parent_manifest_sha256=parent["manifest_sha256"],
            parent_manifest_file_sha256=parent["manifest_file_sha256"],
            active_audit_packet={
                "relative_path": parent["audit_v2_rel"],
                "sha256": parent["audit_v2_sha256"]},
            i1_audit_binding={
                "run_root_relative_path":
                    "i1-packaging-20260913/provenance/i1_audit_binding.json",
                "sha256": "ab" * 32},
            code_revision="f" * 40)
        binding = i1.build_i1_audit_binding(
            generation_id=I1_GEN,
            manifest_sha256=successor["manifest_sha256"],
            manifest_file_sha256="cd" * 32,
            parent_v2={
                "run_root_relative_path":
                    "d1-remediated-data-provenance-recovery-v2/"
                    "provenance/d1_remediation_audit.json",
                "file_sha256": "90" * 32,
                "candidate_generation_id": PARENT_GEN,
                "manifest_sha256": parent["manifest_sha256"]},
            historical_v1={
                "run_root_relative_path":
                    "d1-remediated-data-provenance-recovery-v2/"
                    "provenance/d1_recovery_audit.json",
                "file_sha256": "88" * 32,
                "candidate_generation_id":
                    "d1-remediated-provenance-recovery-v1-20260912"},
            code_revision="f" * 40)
        return parent, successor, binding

    def test_roles_and_forward_arrows(self, tmp_path):
        _, successor, binding = self._build(tmp_path)
        ok, problems = verify_artifact_envelope(binding)
        assert ok, problems
        assert binding["receipt_type"] == i1.I1_AUDIT_BINDING_TYPE
        assert binding["candidate_generation_id"] == I1_GEN
        assert binding["manifest_sha256"] == successor["manifest_sha256"]
        assert binding["audit_chain"]["active"]["role"] == i1.AUDIT_ROLE_ACTIVE
        assert binding["audit_chain"]["parent"]["role"] == i1.AUDIT_ROLE_PARENT
        assert (binding["audit_chain"]["historical"]["role"]
                == i1.AUDIT_ROLE_HISTORICAL)
        # Forward supersession only: I1 supersedes v2; nothing supersedes I1.
        assert "superseded_by" not in binding
        assert binding["supersedes"]["candidate_generation_id"] == PARENT_GEN
        ok, problems = i1.verify_i1_audit_binding(
            binding, expected_generation_id=I1_GEN,
            expected_manifest_sha256=successor["manifest_sha256"])
        assert ok, problems

    def test_rejects_inverted_arrow(self, tmp_path):
        _, successor, binding = self._build(tmp_path)
        bad = dict(binding)
        bad["superseded_by"] = (
            binding["audit_chain"]["historical"]["run_root_relative_path"])
        bad.pop("artifact_sha256", None)
        ok, problems = i1.verify_i1_audit_binding(
            bad, expected_generation_id=I1_GEN,
            expected_manifest_sha256=successor["manifest_sha256"])
        assert not ok
        assert any("superseded_by" in p or "inverted" in p for p in problems)

    def test_rejects_wrong_generation_or_manifest(self, tmp_path):
        _, successor, binding = self._build(tmp_path)
        ok, problems = i1.verify_i1_audit_binding(
            binding, expected_generation_id=PARENT_GEN,
            expected_manifest_sha256=successor["manifest_sha256"])
        assert not ok
        ok, problems = i1.verify_i1_audit_binding(
            binding, expected_generation_id=I1_GEN,
            expected_manifest_sha256="00" * 32)
        assert not ok

    def test_v1_packet_is_historical_for_i1(self, tmp_path):
        """A v1-shaped packet can never serve as the active I1 packet."""
        v1_packet = {
            "candidate_generation_id":
                "d1-remediated-provenance-recovery-v1-20260912",
            "manifest_sha256": None}
        ok, problems = i1.verify_i1_audit_binding(
            v1_packet, expected_generation_id=I1_GEN,
            expected_manifest_sha256="ab" * 32)
        assert not ok


class TestI1PackageInventory:
    def test_inventory_records_new_files(self, tmp_path):
        pkg = tmp_path / "i1-packaging-20260913"
        a = _file(pkg, "manifest.json", b"{}")
        b = _file(pkg, "provenance/i1_audit_binding.json", b"{}")
        inventory = i1.build_i1_package_inventory(pkg)
        paths = {e["relative_path"] for e in inventory}
        assert paths == {"manifest.json",
                         "provenance/i1_audit_binding.json"}
        for entry in inventory:
            assert entry["sha256"] == sha256_file(pkg / entry["relative_path"])
            assert entry["bytes"] == (pkg / entry["relative_path"]).stat().st_size
