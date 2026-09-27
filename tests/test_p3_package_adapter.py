"""INT-01/02, OP-01 — P3 package -> frozen runner contract adapter.

Fixture tests run always; the real-evidence tests skip when the P5
evidence root is absent.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nepal.research_v0._hashing import (
    sha256_canonical, verify_source_evidence)
from nepal.research_v0.p3_package_adapter import (
    RUNNER_PACKAGE_KEYS, build_lake_basin_linkage,
    build_p3_source_record, build_runner_package, load_p3_package)
from nepal.research_v0.records import RunEvidenceManifestV0
from nepal.research_v0.run_evidence import (
    run_evidence_binding_problems)

EVIDENCE = Path("/Users/sanjayb/nepal-event-anomaly-evidence/"
                "p5-glof-2026-09-19")
PKG = EVIDENCE / "glof-events" / "p3_event_package_v0.json"


def _mini_p3_pkg():
    return {
        "schema": "P3_EVENT_PACKAGE_V0",
        "source_id": "synthetic", "source_version": "0",
        "event_labels": [], "observation_opportunities": [],
        "controls": [],
        "holdout_plan": {
            "record_type": "HoldoutPlanV0",
            "holdout_plan_id": "p",
            "assignment_rule": "basin",
            "assigned_before_filtering": True,
            "train_groups": ["a"], "validation_groups": ["b"],
            "test_groups": ["c", "d"],
            "evaluation_region_names": ["c", "d"],
            "event_assignments": {"e1": "a", "e2": "b",
                                  "e3": "c", "e4": "d"},
            "embargo_seconds": 0.0},
        "linkage_uncertainty_ledger": [], "report": {}}


class TestAdapterShape:
    def test_runner_package_exact_keys(self, tmp_path):
        src = tmp_path / "e.csv"
        src.write_text("x\n", encoding="utf-8")
        from nepal.research_v0.source_intake import (
            build_source_manifest)
        import hashlib
        manifest = build_source_manifest(
            tmp_path, source_id="s", source_version="1",
            source_files=[{"relpath": "e.csv",
                           "sha256": hashlib.sha256(
                               src.read_bytes()).hexdigest()}],
            units=["a"], feature_allowlist=["f1"], lineage="t")
        pkg = build_runner_package(
            _mini_p3_pkg(), source_record=build_p3_source_record(),
            event_manifest=manifest)
        assert sorted(
            k for k in pkg if k != "run_evidence_manifest"
        ) == sorted(RUNNER_PACKAGE_KEYS)
        assert pkg["source_manifest_digest"] == sha256_canonical(
            dict(manifest))
        for dk in ("event_digest", "opportunity_digest",
                   "control_digest", "holdout_digest"):
            assert len(pkg[dk]) == 64

    def test_schema_mismatch_rejects(self):
        with pytest.raises(ValueError, match="schema"):
            build_runner_package(
                {"schema": "WRONG"},
                source_record=build_p3_source_record(),
                event_manifest={"x": 1})

    def test_linkage_from_anchor_record(self):
        linkage = build_lake_basin_linkage({
            "basin_coverage": {
                "Koshi": {"n_lakes": 1,
                          "gl_ids": ["GL1"]},
                "Gandaki": {"n_lakes": 1, "gl_ids": ["GL2"]}}})
        assert linkage["linkage"] == {
            "pdgl:GL1": "koshi", "pdgl:GL2": "gandaki"}
        assert linkage["n_lakes"] == 2


@pytest.mark.skipif(not PKG.exists(), reason="P5 evidence absent")
class TestRealEvidence:
    def test_real_package_to_runner_shape(self):
        from nepal.research_v0.p3_package_adapter import (
            build_all_role_manifests)
        pkg = load_p3_package(PKG)
        manifests = build_all_role_manifests(EVIDENCE)
        wrapper = RunEvidenceManifestV0(
            event_manifest=manifests["event"],
            opportunity_manifest=manifests["opportunity"],
            feature_manifest=manifests["feature"],
            sidecar_manifest=manifests["sidecar"])
        assert wrapper.problems() == []
        assert wrapper.verify_problems() == []
        sids = wrapper.source_ids
        assert len(set(sids.values())) == len(sids)
        runner_pkg = build_runner_package(
            pkg, source_record=build_p3_source_record(),
            event_manifest=manifests["event"],
            run_evidence=wrapper)
        assert sorted(
            k for k in runner_pkg
            if k != "run_evidence_manifest"
        ) == sorted(RUNNER_PACKAGE_KEYS)
        assert len(runner_pkg["event_labels"]) == 45
        assert len(runner_pkg["opportunities"]) == 1175
        assert run_evidence_binding_problems(
            runner_pkg["run_evidence_manifest"],
            manifests["event"]) == []

    def test_linkage_covers_all_47_lakes(self):
        rec = json.loads((EVIDENCE / "retrieval" /
                          "anchor_derivation_record.json").read_text())
        linkage = build_lake_basin_linkage(rec)
        assert linkage["n_lakes"] == 47
        assert linkage["conflicts"] == []
        assert set(linkage["linkage"].values()) == {
            "koshi", "gandaki", "karnali"}
