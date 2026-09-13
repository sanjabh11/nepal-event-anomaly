"""RUN_INDEX_I1 tests (N01/N03).

The I1 index unifies the chain: ONE active report (the metadata-rebound
report) with its raw file digest and envelope self-hash in separate fields,
plus the immutable official-pipeline-v3 compute parent bound by raw digest.
``handoff_file_sha256`` is mandatory — a null handoff digest split the v1
index from the handoff.
"""
from __future__ import annotations

import json

import pytest

from nepal.framework_v1.provenance import (bind_artifact_envelope,
                                         sha256_file,
                                         write_deterministic_json)
from nepal.framework_v1.run_index import (RUN_INDEX_I1_TYPE, RUN_INDEX_TYPE,
                                        build_run_index,
                                        build_run_index_i1,
                                        verify_run_index)

I1_GEN = "d1-remediated-provenance-recovery-v2-i1-20260913"


def _sha(text):
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()


def _make_run_root(tmp_path):
    root = tmp_path / "run-root"
    report = {"envelope_type": "PIPELINE_REPORT",
              "candidate_generation_id": I1_GEN}
    report = bind_artifact_envelope(report)
    report_path = root / "i1-packaging-20260913/pipeline/pipeline_report.json"
    write_deterministic_json(report_path, report)
    parent_path = root / "official-pipeline-v3/pipeline_report.json"
    write_deterministic_json(parent_path, {"old": "compute evidence"})
    manifest_path = root / "i1-packaging-20260913/manifest.json"
    write_deterministic_json(manifest_path, {"candidate_generation_id":
                                             I1_GEN})
    audit_path = (root / "i1-packaging-20260913/provenance/"
                  "i1_audit_binding.json")
    write_deterministic_json(audit_path, {"receipt_type":
                                          "I1_AUDIT_BINDING_V1"})
    handoff_path = root / "i1-packaging-20260913/handoff-i1.json"
    write_deterministic_json(handoff_path, {"handoff": True})
    (root / "d1-remediated-data-provenance-recovery-v2").mkdir(
        parents=True)
    return {
        "root": root,
        "report": report,
        "report_rel": "i1-packaging-20260913/pipeline/pipeline_report.json",
        "parent_rel": "official-pipeline-v3/pipeline_report.json",
        "manifest_rel": "i1-packaging-20260913/manifest.json",
        "audit_rel":
            "i1-packaging-20260913/provenance/i1_audit_binding.json",
        "handoff_rel": "i1-packaging-20260913/handoff-i1.json",
        "artifact_root_rel": "d1-remediated-data-provenance-recovery-v2",
    }


def _build(fx, **overrides):
    kwargs = dict(
        run_root_name="run-root",
        active_generation_id=I1_GEN,
        candidate_manifest_path=fx["manifest_rel"],
        candidate_artifact_root=fx["artifact_root_rel"],
        manifest_sha256=_sha("canonical-manifest"),
        manifest_file_sha256=sha256_file(fx["root"] / fx["manifest_rel"]),
        parent_manifest_sha256=_sha("v2-canonical"),
        parent_manifest_file_sha256=_sha("v2-file"),
        active_report={
            "path": fx["report_rel"],
            "file_sha256": sha256_file(fx["root"] / fx["report_rel"]),
            "artifact_sha256": fx["report"]["artifact_sha256"],
            "run_id": "i1-packaging-20260913"},
        compute_parent={
            "pipeline_root": "official-pipeline-v3",
            "report_path": fx["parent_rel"],
            "report_file_sha256": sha256_file(
                fx["root"] / fx["parent_rel"]),
            "run_id": "official-pipeline-v3-20260913"},
        active_audit_packet=fx["audit_rel"],
        active_audit_packet_file_sha256=sha256_file(
            fx["root"] / fx["audit_rel"]),
        handoff_path=fx["handoff_rel"],
        handoff_file_sha256=sha256_file(fx["root"] / fx["handoff_rel"]),
        superseded_generations=["official-pipeline-v1",
                                "delta-closure-20260913"],
    )
    kwargs.update(overrides)
    return build_run_index_i1(**kwargs)


class TestI1IndexHappyPath:
    def test_builds_and_verifies_on_disk(self, tmp_path):
        fx = _make_run_root(tmp_path)
        index = _build(fx)
        assert index["index_type"] == RUN_INDEX_I1_TYPE
        ok, problems = verify_run_index(index, run_root=fx["root"])
        assert ok, problems

    def test_one_active_report_and_parent_bound(self, tmp_path):
        fx = _make_run_root(tmp_path)
        index = _build(fx)
        assert index["active_report"]["path"] == fx["report_rel"]
        assert (index["compute_parent"]["report_path"]
                == fx["parent_rel"])
        # Raw digest and envelope self-hash are separate domains.
        assert (index["active_report"]["file_sha256"]
                != index["active_report"]["artifact_sha256"])


class TestI1IndexRejections:
    def test_null_handoff_digest_rejected(self, tmp_path):
        fx = _make_run_root(tmp_path)
        index = _build(fx)
        index["handoff_file_sha256"] = None
        index.pop("artifact_sha256")
        index = bind_artifact_envelope(index)
        ok, problems = verify_run_index(index, run_root=fx["root"])
        assert not ok
        assert any("handoff" in p for p in problems)

    def test_missing_handoff_digest_rejected(self, tmp_path):
        fx = _make_run_root(tmp_path)
        index = _build(fx)
        del index["handoff_file_sha256"]
        index.pop("artifact_sha256")
        index = bind_artifact_envelope(index)
        ok, problems = verify_run_index(index, run_root=fx["root"])
        assert not ok

    def test_raw_self_hash_confusion_rejected(self, tmp_path):
        fx = _make_run_root(tmp_path)
        index = _build(fx)
        index["active_report"]["artifact_sha256"] = \
            index["active_report"]["file_sha256"]
        index.pop("artifact_sha256")
        index = bind_artifact_envelope(index)
        ok, problems = verify_run_index(index, run_root=fx["root"])
        assert not ok
        assert any("hash domain" in p or "distinct" in p for p in problems)

    def test_split_active_report_rejected(self, tmp_path):
        """Active report and compute parent cannot be the same report."""
        fx = _make_run_root(tmp_path)
        index = _build(fx)
        index["active_report"]["path"] = fx["parent_rel"]
        index["active_report"]["file_sha256"] = sha256_file(
            fx["root"] / fx["parent_rel"])
        index.pop("artifact_sha256")
        index = bind_artifact_envelope(index)
        ok, problems = verify_run_index(index, run_root=fx["root"])
        assert not ok
        assert any("distinct" in p for p in problems)

    def test_absolute_canonical_path_rejected(self, tmp_path):
        fx = _make_run_root(tmp_path)
        index = _build(fx, candidate_manifest_path="/abs/manifest.json")
        ok, problems = verify_run_index(index, run_root=fx["root"])
        assert not ok
        assert any("absolute" in p or "safe relative" in p
                   for p in problems)

    def test_traversal_rejected(self, tmp_path):
        fx = _make_run_root(tmp_path)
        index = _build(fx, handoff_path="../outside/handoff.json")
        ok, problems = verify_run_index(index, run_root=fx["root"])
        assert not ok

    def test_tampered_active_report_detected_on_disk(self, tmp_path):
        fx = _make_run_root(tmp_path)
        index = _build(fx)
        target = fx["root"] / fx["report_rel"]
        target.write_bytes(b'{"tampered": true}')
        ok, problems = verify_run_index(index, run_root=fx["root"])
        assert not ok
        assert any("active report" in p for p in problems)

    def test_tampered_handoff_detected_on_disk(self, tmp_path):
        fx = _make_run_root(tmp_path)
        index = _build(fx)
        (fx["root"] / fx["handoff_rel"]).write_bytes(b"{}")
        ok, problems = verify_run_index(index, run_root=fx["root"])
        assert not ok
        assert any("handoff" in p for p in problems)

    def test_envelope_hash_mismatch_detected(self, tmp_path):
        fx = _make_run_root(tmp_path)
        index = _build(fx)
        index["active_report"]["artifact_sha256"] = "00" * 32
        index.pop("artifact_sha256")
        index = bind_artifact_envelope(index)
        ok, problems = verify_run_index(index, run_root=fx["root"])
        assert not ok

    def test_missing_generation_rejected(self, tmp_path):
        fx = _make_run_root(tmp_path)
        index = _build(fx)
        del index["active_generation_id"]
        index.pop("artifact_sha256")
        index = bind_artifact_envelope(index)
        ok, problems = verify_run_index(index)
        assert not ok


class TestBackwardCompatibility:
    def test_v1_index_still_verifies(self, tmp_path):
        root = tmp_path / "run-root"
        manifest = root / "cand/manifest.json"
        write_deterministic_json(manifest, {"a": 1})
        report = root / "pipe/pipeline_report.json"
        write_deterministic_json(report, {"b": 2})
        audit = root / "cand/audit.json"
        write_deterministic_json(audit, {"c": 3})
        index = build_run_index(
            run_root_name="run-root",
            active_generation_id="gen-x",
            candidate_root="cand",
            manifest_sha256=_sha("m"),
            manifest_file_sha256=sha256_file(manifest),
            pipeline_run_id="pipe-1",
            pipeline_root="pipe",
            pipeline_report_sha256=sha256_file(report),
            active_audit_packet="cand/audit.json",
            active_audit_packet_sha256=sha256_file(audit),
            handoff_path="cand/handoff.json")
        assert index["index_type"] == RUN_INDEX_TYPE
        ok, problems = verify_run_index(index, run_root=root)
        assert ok, problems

    def test_v1_index_still_allows_null_handoff(self, tmp_path):
        index = build_run_index(
            run_root_name="r", active_generation_id="g",
            candidate_root="c", manifest_sha256=_sha("a"),
            manifest_file_sha256=_sha("b"), pipeline_run_id="p",
            pipeline_root="p", pipeline_report_sha256=_sha("c"),
            active_audit_packet="a", active_audit_packet_sha256=_sha("d"),
            handoff_path="h", handoff_sha256=None)
        ok, problems = verify_run_index(index)
        assert ok, problems
