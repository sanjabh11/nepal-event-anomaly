"""Round-11.3 provenance probes — the audit's live findings:

P01 runner independently re-verifies the config manifest (a forged
    rehashed package + forged config can never reach the engine);
P02 nested unhashable package values are bounded RUN_ERRORs;
P03 package construction requires exact typed records, never
    duck-typed stand-ins;
P04 sidecar reviewer_ids bind a real independent-review minimum.
"""
from __future__ import annotations

import hashlib
import types

import pytest

from nepal.research_v0._hashing import sha256_canonical
from nepal.science_v0.glof_poc import (
    build_hmaglofdb_event_package, run_glof_descriptive_poc)

from tests.test_glof_poc_contract import (
    _BASE_ROWS, _EMBARGO, _EVAL_REGIONS, _GROUP_OF_BASIN,
    _SPLIT_OF_GROUP, _manifest, _opportunity_frame, _package,
    _pkg_manifest, _source_record, _source_rows, _write_csv,
    _write_sidecar)


class _DuckRecord:
    """A duck-typed stand-in: has problems() but is not a typed
    record — inadmissible at package construction (P03)."""
    def __init__(self, problems=()):
        self._problems = list(problems)
        self.source_id = "hmaglofdb"
        self.source_id_value = "hmaglofdb"
    def problems(self):
        return self._problems


def _verified_cfg_pkg(tmp_path):
    path = _write_csv(tmp_path, _BASE_ROWS)
    man = _manifest(tmp_path, path)
    sidecar = _write_sidecar(tmp_path)
    pkg = _package(
        source_manifest=man,
        source_record=_source_record(sidecar=sidecar))
    cfg = types.SimpleNamespace(source_manifest=man)
    return cfg, pkg


class TestR113ForgedManifest:
    """P01: the runner re-verifies the config manifest itself."""

    def test_forged_config_manifest_run_error(self, tmp_path,
                                              monkeypatch):
        called = []
        monkeypatch.setattr(
            "nepal.science_v0.glof_poc.run_regimes",
            lambda *a, **k: called.append(1) or
            {"status": "DESCRIPTIVE_REGIME_ONLY"})
        monkeypatch.setattr(
            "nepal.science_v0.glof_poc.freeze_regime_artifact",
            lambda a: {"regime_artifact_digest": "b" * 64})
        # forge a package whose manifest digest is self-consistent
        # but whose manifest names bytes that do not exist
        man = _pkg_manifest()
        forged = dict(man)
        forged["source_files"] = [{"relpath": "ghost.csv",
                                   "sha256": "c" * 64}]
        forged["source_digests"] = ["c" * 64]
        pkg = _package(source_manifest=man)
        # rehash the package to carry the FORGED digest
        forged_pkg = dict(pkg)
        forged_pkg["source_manifest_digest"] = \
            sha256_canonical(dict(forged))
        cfg = types.SimpleNamespace(source_manifest=forged)
        receipt = run_glof_descriptive_poc(
            None, ["f1"], None, cfg, forged_pkg)
        assert receipt["status"] == "RUN_ERROR"
        assert any("evidence" in p for p in receipt["problems"])
        assert called == [], "engine invoked for a forged manifest"

    def test_fixture_marker_config_run_error(self, tmp_path):
        cfg, pkg = _verified_cfg_pkg(tmp_path)
        bad = dict(_pkg_manifest(), fixture=True)
        cfg.source_manifest = bad
        receipt = run_glof_descriptive_poc(
            None, ["f1"], None, cfg, pkg)
        assert receipt["status"] == "RUN_ERROR"

    def test_extra_key_config_run_error(self, tmp_path):
        cfg, pkg = _verified_cfg_pkg(tmp_path)
        cfg.source_manifest = dict(_pkg_manifest(), extra="x")
        receipt = run_glof_descriptive_poc(
            None, ["f1"], None, cfg, pkg)
        assert receipt["status"] == "RUN_ERROR"


class TestR113UnhashableInputs:
    """P02: nested unhashable values are bounded, never raised."""

    def _mutated(self, tmp_path, section, value):
        cfg, pkg = _verified_cfg_pkg(tmp_path)
        mutated = dict(pkg)
        mutated[section] = [dict(r) for r in pkg[section]]
        mutated[section][0] = dict(mutated[section][0],
                                   bad_field=value)
        mutated[section + "_digest"] = None
        return cfg, mutated

    @pytest.mark.parametrize("value", [
        {"a", "b"}, (i for i in range(3)), float("nan"),
        float("inf"), b"bytes"])
    def test_unhashable_nested_value_run_error(self, tmp_path,
                                               value):
        cfg, mutated = self._mutated(
            tmp_path, "event_labels", value)
        receipt = run_glof_descriptive_poc(
            None, ["f1"], None, cfg, mutated)
        assert receipt["status"] == "RUN_ERROR"
        assert receipt["record_type"] == "GLOF_POC_RECEIPT_V0"

    def test_unhashable_in_opportunities_run_error(self, tmp_path):
        cfg, mutated = self._mutated(
            tmp_path, "opportunities", {"x", "y"})
        receipt = run_glof_descriptive_poc(
            None, ["f1"], None, cfg, mutated)
        assert receipt["status"] == "RUN_ERROR"

    def test_unhashable_in_controls_run_error(self, tmp_path):
        cfg, mutated = self._mutated(
            tmp_path, "controls", (i for i in range(2)))
        receipt = run_glof_descriptive_poc(
            None, ["f1"], None, cfg, mutated)
        assert receipt["status"] == "RUN_ERROR"

    def test_unhashable_config_manifest_value(self, tmp_path):
        cfg, pkg = _verified_cfg_pkg(tmp_path)
        cfg.source_manifest = dict(
            _pkg_manifest(), units={"u1", "u2"})
        receipt = run_glof_descriptive_poc(
            None, ["f1"], None, cfg, pkg)
        assert receipt["status"] == "RUN_ERROR"


class TestR113ExactTypedRecords:
    """P03: duck-typed records never reach package emission."""

    def test_duck_source_record_rejected(self):
        with pytest.raises(ValueError, match="SourceRecordV0"):
            _package(source_record=_DuckRecord())

    def test_duck_opportunity_rejected(self):
        opps = list(_opportunity_frame())
        opps[0] = _DuckRecord()
        with pytest.raises((ValueError, TypeError)):
            _package(opps=tuple(opps))

    def test_wrong_record_type_mapping_rejected(self):
        sr = _source_record().to_dict()
        sr["record_type"] = "CutoffRecordV0"
        with pytest.raises((ValueError, TypeError)):
            _package(source_record=sr)

    def test_typed_canonical_package_passes(self):
        pkg = _package()
        assert pkg["source_manifest_digest"]
        assert pkg["event_digest"]


class TestR113ReviewerBinding:
    """P04: reviewer_ids must bind a real independent review."""

    def _run(self, tmp_path, reviewers):
        path = _write_csv(tmp_path, _BASE_ROWS)
        man = _manifest(tmp_path, path)
        import json as _json
        payload = {
            "source_id": "hmaglofdb", "source_version": "1.3.0",
            "license_id": "test-license", "coverage": "Nepal",
            "timing_review": "ok", "reviewer_ids": reviewers,
            "review_date": "2026-09-01", "decision": "VERIFIED"}
        (tmp_path / "sidecar.json").write_text(
            _json.dumps(payload), encoding="utf-8")
        sha = hashlib.sha256(
            (tmp_path / "sidecar.json").read_bytes()).hexdigest()
        rec = _source_record(sidecar={
            "relpath": "sidecar.json", "sha256": sha})
        pkg = _package(source_manifest=man, source_record=rec)
        cfg = types.SimpleNamespace(source_manifest=man)
        return run_glof_descriptive_poc(
            None, ["f1"], None, cfg, pkg)

    @pytest.mark.parametrize("reviewers", [
        "rev-1", 42, [["rev-1"]], ["rev-1"],
        ["rev-1", "rev-1"], ["rev-1", ""], []])
    def test_bad_reviewer_ids_candidate_only(self, tmp_path,
                                             reviewers):
        receipt = self._run(tmp_path, reviewers)
        assert receipt["status"] == "CANDIDATE_ONLY"
        assert any("reviewer" in p or "sidecar" in p
                   for p in receipt["problems"])

    def test_two_named_reviewers_pass_gate(self, tmp_path):
        receipt = self._run(tmp_path, ["rev-1", "rev-2"])
        assert not any("reviewer" in p for p in receipt["problems"])
