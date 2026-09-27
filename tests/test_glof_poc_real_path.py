"""Coordinator real-path tests — CSV bytes → package → receipt.

Exactly one REAL end-to-end receipt run per session (module-cached):
verified bytes → ``load_hmaglofdb_rows`` →
``build_hmaglofdb_event_package`` → the real ``run_regimes`` engine →
``freeze_regime_artifact`` → ``GLOF_POC_RECEIPT_V0``.  Gate-mapping
tests reuse the REAL package but stub the engine exactly as the
contract lane does — gate logic, not engine behavior, is the surface.

Non-goals: no association, no forecast, no warning, no production.
Every terminal status is honest; all promotion flags stay false.
No real HMAGLOFDB bytes are involved (``P5_BLOCKED_NO_AUTHORIZATION``
stands); all fixtures are synthetic.
"""
from __future__ import annotations

import csv
import dataclasses
import hashlib
import tempfile
import re
from pathlib import Path

import pytest

from nepal.research_v0.records import SourceRecordV0, ObservationOpportunityV0
from nepal.research_v0.source_intake import (
    build_source_manifest, load_hmaglofdb_rows)
from nepal.science_v0.events import SourceRow
from nepal.science_v0.glof_poc import (
    build_hmaglofdb_event_package, run_glof_descriptive_poc)

from tests.test_experiment_v0_b4_audit import _mini_regime_frame

_SOURCE_ID = "hmaglofdb"
_SOURCE_VERSION = "1.3.0"
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")

_CSV_HEADER = ("key", "basin", "t0", "t1", "prec", "mech",
               "cg", "parent", "obs")
_COLUMN_MAP = {
    "source_row_key": "key", "basin": "basin",
    "interval_start": "t0", "interval_end": "t1",
    "declared_precision": "prec", "mechanism": "mech",
    "cascade_group_id": "cg", "parent_source_row_key": "parent",
    "observed_on": "obs"}
_BASE_ROWS = [
    ("r1", "koshi", "2020-06-01T00:00:00Z", "2020-06-02T00:00:00Z",
     "day", "lake_outburst", "cg-koshi-1", "", ""),
    ("r2", "koshi", "2020-07-10T00:00:00Z", "2020-07-11T00:00:00Z",
     "day", "lake_outburst", "cg-koshi-1", "r1",
     "2020-07-15T00:00:00Z"),
    ("r3", "bagmati", "2020-06-10T00:00:00Z", "2020-06-11T00:00:00Z",
     "day", "lake_outburst", "", "", ""),
    ("r4", "gandaki", "2020-08-05T00:00:00Z", "2020-08-06T00:00:00Z",
     "day", "lake_outburst", "", "", ""),
    ("r5", "karnali", "2020-09-01T00:00:00Z", "2020-09-02T00:00:00Z",
     "day", "lake_outburst", "", "", ""),
    ("r6", "dudh_koshi", "2020-07-01T00:00:00Z",
     "2020-08-01T00:00:00Z", "month", "lake_outburst", "", "", ""),
    ("r7", "mahakali", "2020-05-01T00:00:00Z",
     "2020-05-31T00:00:00Z", "interval_8_30d", "lake_outburst",
     "", "", ""),
    ("r8", "arun", "2020-06-15T00:00:00Z", "2020-06-18T00:00:00Z",
     "interval_3d", "lake_outburst", "", "", ""),
    ("r9", "seti", "2020-01-01T00:00:00Z", "2021-01-01T00:00:00Z",
     "year", "lake_outburst", "", "", ""),
]
_GROUP_OF_BASIN = {
    "koshi": "grp_east", "dudh_koshi": "grp_east",
    "bagmati": "grp_central", "gandaki": "grp_west",
    "karnali": "grp_north", "arun": "grp_north",
    "mahakali": "grp_farwest", "seti": "grp_farwest"}
_SPLIT_OF_GROUP = {
    "grp_east": "train", "grp_central": "train",
    "grp_west": "validation", "grp_north": "test",
    "grp_farwest": "test"}
_EVAL_REGIONS = ("karnali", "mahakali")
_EMBARGO = 30.0 * 86400.0

_PACKAGE_KEYS = {
    "source_record", "event_labels", "opportunities", "controls",
    "holdout_plan", "source_manifest_digest", "event_digest",
    "opportunity_digest", "control_digest", "holdout_digest"}
_RECEIPT_KEYS = {
    "record_type", "status", "claim_scope", "source_manifest_digest",
    "event_digest", "opportunity_digest", "control_digest",
    "holdout_digest", "regime_artifact_digest", "report_digest",
    "promotion_eligible", "production_authorized",
    "warning_path_authorized", "problems"}
_RECEIPT_STATUSES = {
    "RUN_ERROR", "CANDIDATE_ONLY", "UNDERPOWERED_DESCRIPTIVE_ONLY",
    "DESCRIPTIVE_REGIME_ONLY"}

# --------------------------------- module-scoped verified real bytes

_REAL_ROOT = None


def _real_root() -> Path:
    """A byte-bound evidence root created ONCE per session: events.csv
    (declared + hashed) and a real >=2-reviewer sidecar (R11.2-2)."""
    global _REAL_ROOT
    if _REAL_ROOT is None:
        import json
        root = Path(tempfile.mkdtemp(prefix="glof-realpath-"))
        with open(root / "events.csv", "w", newline="",
                  encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(_CSV_HEADER)
            writer.writerows(_BASE_ROWS)
        sidecar = {
            "source_id": _SOURCE_ID, "source_version": _SOURCE_VERSION,
            "license_id": "cc-by-4.0-synthetic",
            "coverage": "Nepal basins (synthetic)",
            "timing_review": "coarse timing preserved verbatim",
            "reviewer_ids": ["rev-1", "rev-2"],
            "review_date": "2026-09-18", "decision": "VERIFIED"}
        (root / "sidecar.json").write_text(
            json.dumps(sidecar), encoding="utf-8")
        _REAL_ROOT = root
    return _REAL_ROOT


def _manifest() -> dict:
    root = _real_root()
    return build_source_manifest(
        root, source_id=_SOURCE_ID, source_version=_SOURCE_VERSION,
        source_files=[{
            "relpath": "events.csv",
            "sha256": hashlib.sha256(
                (root / "events.csv").read_bytes()).hexdigest()}],
        units=list(_GROUP_OF_BASIN),
        feature_allowlist=["f1", "f2"],
        lineage="real-path lane acquired bytes")


def _sidecar_pair() -> dict:
    path = _real_root() / "sidecar.json"
    return {"relpath": "sidecar.json",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _source_record(posture="EVIDENCE_VERIFIED",
                   sidecar=None) -> SourceRecordV0:
    sidecar = sidecar or _sidecar_pair()
    return SourceRecordV0(
        source_id=_SOURCE_ID, provider="test",
        doi_or_url="synthetic://realpath",
        version=_SOURCE_VERSION, as_of_date="2026-09-18",
        license_id="cc-by-4.0-synthetic", redistribution="test",
        geography="Nepal basins", temporal_coverage="1950-2025",
        event_time_class="COARSE_OR_UNRESOLVED",
        spatial_semantics="lake_point",
        observation_method="literature + remote sensing",
        non_event_frame="lake inventory",
        update_cadence="annual", access_status="open",
        posture=posture, license_notes="real-path lane",
        evidence_sidecar_path=sidecar["relpath"],
        evidence_sidecar_sha256=sidecar["sha256"],
        evidence_as_of="2026-09-18",
        evidence_review_state="INDEPENDENTLY_VERIFIED")


def _opp(oid, unit, w0, w1, state="OBSERVED_FULL", cov=1.0,
         frames=None):
    frames = (f"fr-{oid}",) if frames is None else frames
    return ObservationOpportunityV0(
        opportunity_id=oid, unit_id=unit,
        platform="synthetic-platform",
        window_start=w0, window_end=w1, coverage_fraction=cov,
        coverage_quality="synthetic", detection_threshold="synthetic",
        state=state, source_id=_SOURCE_ID, source_as_of="2020-12-31",
        frame_ids=tuple(frames))


def _opportunity_frame():
    return (
        _opp("opp-koshi-clear", "koshi",
             "2020-06-15T00:00:00Z", "2020-06-20T00:00:00Z"),
        _opp("opp-bagmati-overlap", "bagmati",
             "2020-06-01T00:00:00Z", "2020-06-30T00:00:00Z"),
        _opp("opp-gandaki-partial", "gandaki",
             "2020-08-01T00:00:00Z", "2020-08-31T00:00:00Z",
             state="OBSERVED_PARTIAL", cov=0.5),
        _opp("opp-karnali-unobserved", "karnali",
             "2020-09-01T00:00:00Z", "2020-09-30T00:00:00Z",
             state="UNOBSERVED", cov=0.0, frames=()),
        _opp("opp-mahakali-clear", "mahakali",
             "2020-06-01T00:00:00Z", "2020-06-30T00:00:00Z"),
    )


def _rows() -> tuple:
    root = _real_root()
    return load_hmaglofdb_rows(
        root / "events.csv", source_manifest=_manifest(),
        column_map=dict(_COLUMN_MAP))


def _package(**over):
    return build_hmaglofdb_event_package(
        _rows(),
        source_record=over.pop("source_record", _source_record()),
        source_manifest=over.pop("source_manifest", _manifest()),
        opportunity_frame=over.pop("opportunity_frame",
                                   _opportunity_frame()),
        group_of_basin=_GROUP_OF_BASIN,
        split_of_group=_SPLIT_OF_GROUP,
        evaluation_regions=_EVAL_REGIONS,
        embargo_seconds=_EMBARGO, **over)


def _regime_config():
    df, feature_cols, train_mask, cfg = _mini_regime_frame()
    cfg = dataclasses.replace(cfg, source_manifest=_manifest())
    return df, feature_cols, train_mask, cfg


_REAL_RECEIPT = {}


def _real_receipt() -> dict:
    """THE real end-to-end run — executed once, reused read-only."""
    if "receipt" not in _REAL_RECEIPT:
        df, feature_cols, train_mask, cfg = _regime_config()
        _REAL_RECEIPT["receipt"] = run_glof_descriptive_poc(
            df, feature_cols, train_mask, cfg, _package())
    return _REAL_RECEIPT["receipt"]


# ------------------------------------------------------ 1. real end-to-end

class TestRealEndToEnd:
    def test_receipt_contract_holds_on_the_real_path(self):
        receipt = _real_receipt()
        assert set(receipt) == _RECEIPT_KEYS
        assert receipt["record_type"] == "GLOF_POC_RECEIPT_V0"
        assert receipt["status"] in _RECEIPT_STATUSES, \
            receipt["problems"]
        assert receipt["claim_scope"] == \
            "research_only_no_operational_authorization"
        for key in ("source_manifest_digest", "event_digest",
                    "opportunity_digest", "control_digest",
                    "holdout_digest", "regime_artifact_digest",
                    "report_digest"):
            assert _SHA_RE.match(receipt[key]), key
        assert receipt["promotion_eligible"] is False
        assert receipt["production_authorized"] is False
        assert receipt["warning_path_authorized"] is False
        assert isinstance(receipt["problems"], list)

    def test_package_keys_exact_and_digests_deterministic(self):
        pkg = _package()
        assert set(pkg) == _PACKAGE_KEYS
        again = _package()
        for key in ("source_manifest_digest", "event_digest",
                    "opportunity_digest", "control_digest",
                    "holdout_digest"):
            assert _SHA_RE.match(pkg[key]), key
            assert pkg[key] == again[key], key

    def test_timing_precision_is_preserved(self):
        pkg = _package()
        labels = pkg["event_labels"]
        assert isinstance(labels, list) and labels
        for label in labels:
            if not isinstance(label, dict):
                continue
            if label.get("timing_class"):
                assert label["timing_class"] in (
                    "EXACT_TIMESTAMP", "EXACT_DAY",
                    "INTERVAL_LE_7D", "INTERVAL_8_30D",
                    "COARSE_OR_UNRESOLVED")

    def test_no_fabricated_controls(self):
        pkg = _package()
        for control in pkg["controls"]:
            if isinstance(control, dict):
                # The derived state vocabulary is closed: NEGATIVE
                # only under a linked, non-overlapping OBSERVED_FULL
                # opportunity; everything else stays censored.
                state = control.get("state",
                                    control.get("control_type"))
                assert state in ("NEGATIVE",
                                 "CENSORED_OR_AMBIGUOUS"), control
                assert control.get("opportunity_id"), control

    def test_holdout_plan_present(self):
        plan = _package()["holdout_plan"]
        assert isinstance(plan, dict)
        # Either a valid plan or an explicit rejection — never a
        # silent pass, never a weakened gate.
        assert plan.get("rejected") is True or \
            plan.get("holdout_plan_id")

# ---------------------------------- 2. gate mapping over the real package

class TestGateMappingRealPackage:
    @pytest.fixture
    def package(self):
        return _package()

    def _run(self, monkeypatch, artifact_status, frozen=None):
        monkeypatch.setattr(
            "nepal.science_v0.glof_poc.run_regimes",
            lambda *a, **k: {"status": artifact_status})
        monkeypatch.setattr(
            "nepal.science_v0.glof_poc.freeze_regime_artifact",
            frozen or (lambda a: {"regime_artifact_digest": "b" * 64}))
        df, feature_cols, train_mask, cfg = _regime_config()
        return run_glof_descriptive_poc(
            df, feature_cols, train_mask, cfg, _package())

    def test_descriptive_status_maps_descriptive(self, monkeypatch):
        receipt = self._run(monkeypatch, "DESCRIPTIVE_REGIME_ONLY")
        assert receipt["status"] == "DESCRIPTIVE_REGIME_ONLY"

    def test_not_stable_artifact_maps_candidate_only(self, monkeypatch):
        receipt = self._run(
            monkeypatch, "UNSUPERVISED_STRUCTURE_NOT_STABLE")
        assert receipt["status"] == "CANDIDATE_ONLY"

    def test_candidate_artifact_maps_candidate_only(self, monkeypatch):
        receipt = self._run(monkeypatch, "CANDIDATE_ONLY")
        assert receipt["status"] == "CANDIDATE_ONLY"

    def _run_with_posture(self, posture):
        pkg = build_hmaglofdb_event_package(
            _rows(),
            source_record=_source_record(posture=posture),
            source_manifest=_manifest(),
            opportunity_frame=_opportunity_frame(),
            group_of_basin=_GROUP_OF_BASIN,
            split_of_group=_SPLIT_OF_GROUP,
            evaluation_regions=_EVAL_REGIONS,
            embargo_seconds=_EMBARGO)
        df, feature_cols, train_mask, cfg = _regime_config()
        return run_glof_descriptive_poc(
            df, feature_cols, train_mask, cfg, pkg)

    def test_candidate_posture_demotes_to_candidate_only(self):
        receipt = self._run_with_posture("CANDIDATE_ONLY")
        assert receipt["status"] == "CANDIDATE_ONLY"

    def test_rejected_posture_demotes_to_candidate_only(self):
        receipt = self._run_with_posture("REJECTED")
        assert receipt["status"] == "CANDIDATE_ONLY"

    def test_engine_run_error_maps_run_error(self, monkeypatch):
        monkeypatch.setattr(
            "nepal.science_v0.glof_poc.run_regimes",
            lambda *a, **k: {"status": "RUN_ERROR",
                             "reason": "engine refused"})
        df, feature_cols, train_mask, cfg = _regime_config()
        receipt = run_glof_descriptive_poc(
            df, feature_cols, train_mask, cfg, _package())
        assert receipt["status"] == "RUN_ERROR"

    def test_engine_exception_maps_run_error(self, monkeypatch):
        monkeypatch.setattr(
            "nepal.science_v0.glof_poc.run_regimes",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        df, feature_cols, train_mask, cfg = _regime_config()
        receipt = run_glof_descriptive_poc(
            df, feature_cols, train_mask, cfg, _package())
        assert receipt["status"] == "RUN_ERROR"

    def test_malformed_engine_output_maps_run_error(self, monkeypatch):
        monkeypatch.setattr(
            "nepal.science_v0.glof_poc.run_regimes",
            lambda *a, **k: ["not", "a", "mapping"])
        df, feature_cols, train_mask, cfg = _regime_config()
        receipt = run_glof_descriptive_poc(
            df, feature_cols, train_mask, cfg, _package())
        assert receipt["status"] == "RUN_ERROR"

    def test_forged_status_not_in_vocabulary_maps_run_error(
            self, monkeypatch):
        receipt = self._run(monkeypatch, "FORECAST_READY")
        assert receipt["status"] == "RUN_ERROR"

    def test_freeze_rejection_maps_run_error(self, monkeypatch):
        def _boom(_artifact):
            raise ValueError("freeze refused the artifact")

        monkeypatch.setattr(
            "nepal.science_v0.glof_poc.run_regimes",
            lambda *a, **k: {"status": "DESCRIPTIVE_REGIME_ONLY"})
        monkeypatch.setattr(
            "nepal.science_v0.glof_poc.freeze_regime_artifact", _boom)
        df, feature_cols, train_mask, cfg = _regime_config()
        receipt = run_glof_descriptive_poc(
            df, feature_cols, train_mask, cfg, _package())
        assert receipt["status"] == "RUN_ERROR"

    def test_blocked_run_demotes_never_promotes(self, monkeypatch):
        # A fixture manifest can never produce a descriptive receipt:
        # the runner fails closed with RUN_ERROR before the engine is
        # ever invoked — fixture markers are not admissible at the
        # runner boundary (R11.3-P01) and nothing promotes.
        calls = []
        monkeypatch.setattr(
            "nepal.science_v0.glof_poc.run_regimes",
            lambda *a, **k: calls.append(1) or
            {"status": "DESCRIPTIVE_REGIME_ONLY"})
        monkeypatch.setattr(
            "nepal.science_v0.glof_poc.freeze_regime_artifact",
            lambda a: {"regime_artifact_digest": "b" * 64})
        df, feature_cols, train_mask, cfg = _mini_regime_frame()
        receipt = run_glof_descriptive_poc(
            df, feature_cols, train_mask, cfg, _package())
        assert receipt["status"] == "RUN_ERROR"
        assert calls == [], "engine invoked for a fixture manifest"
        assert receipt["promotion_eligible"] is False
        assert receipt["warning_path_authorized"] is False
        assert receipt["production_authorized"] is False
        assert receipt["claim_scope"] == \
            "research_only_no_operational_authorization"

    def test_sidecar_mismatch_demotes_to_candidate_only(
            self, monkeypatch):
        # A record binding a FAKE sidecar digest: the runner's
        # sidecar gate must demote even under a descriptive artifact.
        monkeypatch.setattr(
            "nepal.science_v0.glof_poc.run_regimes",
            lambda *a, **k: {"status": "DESCRIPTIVE_REGIME_ONLY"})
        monkeypatch.setattr(
            "nepal.science_v0.glof_poc.freeze_regime_artifact",
            lambda a: {"regime_artifact_digest": "b" * 64})
        df, feature_cols, train_mask, cfg = _regime_config()
        pkg = build_hmaglofdb_event_package(
            _rows(),
            source_record=_source_record(sidecar={
                "relpath": "sidecar.json", "sha256": "c" * 64}),
            source_manifest=_manifest(),
            opportunity_frame=_opportunity_frame(),
            group_of_basin=_GROUP_OF_BASIN,
            split_of_group=_SPLIT_OF_GROUP,
            evaluation_regions=_EVAL_REGIONS,
            embargo_seconds=_EMBARGO)
        receipt = run_glof_descriptive_poc(
            df, feature_cols, train_mask, cfg, pkg)
        assert receipt["status"] == "CANDIDATE_ONLY"




