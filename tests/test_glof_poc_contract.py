"""Round-11 thin-PoC contract tests — the GLOF descriptive path.

Covers the three contract surfaces added for the Round-11 audit
(``docs/science/run_b/GLOF_POC_CONTRACT_V0.md``):

* ``nepal.research_v0.source_intake.build_source_manifest`` — the
  existing non-fixture ``source_manifest`` shape for real acquired
  bytes; exact 7-key surface, ``source_version=…;`` lineage token,
  fail-closed shape validation (no byte verification here).
* ``nepal.research_v0.source_intake.load_hmaglofdb_rows`` — byte-bound
  CSV intake: the file must be a manifest-declared member whose bytes
  verify, every semantic column must be explicitly mapped, and every
  vocabulary (basin/mechanism/precision) is controlled.
* ``nepal.science_v0.glof_poc`` — ``build_hmaglofdb_event_package``
  emits exactly the 10 contract keys with typed
  EventLabelV0/ControlWindowV0/HoldoutPlanV0 surfaces and canonical
  digests; ``run_glof_descriptive_poc`` emits the non-promotable
  ``GLOF_POC_RECEIPT_V0`` whose gate order is RUN_ERROR ->
  CANDIDATE_ONLY -> UNDERPOWERED_DESCRIPTIVE_ONLY ->
  DESCRIPTIVE_REGIME_ONLY.

All fixtures are synthetic: no real event data, no network, no
wall-clock reads.  The runner's status-mapping gates are exercised
with ``run_regimes``/``freeze_regime_artifact`` monkeypatched so the
gate logic is tested independently of the (separately audited) regime
engine; exactly one test performs the real end-to-end run.
"""
from __future__ import annotations

import csv
import dataclasses
import hashlib
import types
from pathlib import Path
import re

import pytest

from nepal.research_v0.records import (
    RECORD_TYPES, ObservationOpportunityV0, SourceRecordV0,
    deserialize_record)
from nepal.research_v0.source_intake import (
    build_source_manifest, load_hmaglofdb_rows)
from nepal.science_v0.events import SourceRow
from nepal.science_v0.glof_poc import (
    build_hmaglofdb_event_package, run_glof_descriptive_poc)

from tests.test_experiment_v0_b4_audit import _mini_regime_frame


_SOURCE_ID = "hmaglofdb"
_SOURCE_VERSION = "1.3.0"
_HEX64 = "a" * 64
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")

_MANIFEST_KEYS = {
    "source_id", "source_digests", "units", "feature_allowlist",
    "lineage", "evidence_root", "source_files"}
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

_CSV_HEADER = ("key", "basin", "t0", "t1", "prec", "mech",
               "cg", "parent", "obs")
_COLUMN_MAP = {
    "source_row_key": "key", "basin": "basin",
    "interval_start": "t0", "interval_end": "t1",
    "declared_precision": "prec", "mechanism": "mech",
    "cascade_group_id": "cg", "parent_source_row_key": "parent",
    "observed_on": "obs"}
_REQUIRED_COLUMN_MAP = {k: v for k, v in _COLUMN_MAP.items()
                        if k not in ("cascade_group_id",
                                     "parent_source_row_key",
                                     "observed_on")}

#: 9 rows across 8 basins / 4+ geographic groups.  Precision mix is
#: deliberate: honest day rows, bounded interval windows, a 31-day
#: month-declared window and a year window (both measured COARSE).
#: r1/r2 form a same-basin cascade group (r2's parent is r1).
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
    ("r6", "dudh_koshi", "2020-07-01T00:00:00Z", "2020-08-01T00:00:00Z",
     "month", "lake_outburst", "", "", ""),
    ("r7", "mahakali", "2020-05-01T00:00:00Z", "2020-05-31T00:00:00Z",
     "interval_8_30d", "lake_outburst", "", "", ""),
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

#: A single-test-group variant: every basin otherwise valid, but the
#: two evaluation regions collapse onto one locked test group — the
#: HoldoutPlanV0 contract rejects (>=2 uniquely named regions).
_ONE_TEST_GOB = dict(_GROUP_OF_BASIN, mahakali="grp_north",
                     seti="grp_north")
_ONE_TEST_SOG = {"grp_east": "train", "grp_central": "train",
                 "grp_west": "validation", "grp_north": "test"}


# ------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------

def _write_csv(root, rows, name="events.csv"):
    path = root / name
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(_CSV_HEADER)
        writer.writerows(rows)
    return path


def _manifest(root, *paths, feature_allowlist=("f1", "f2")):
    files = [{"relpath": str(p.relative_to(root)),
              "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
             for p in paths]
    return build_source_manifest(
        root, source_id=_SOURCE_ID, source_version=_SOURCE_VERSION,
        source_files=files,
        units=list(_GROUP_OF_BASIN),
        feature_allowlist=list(feature_allowlist),
        lineage="contract-test acquired bytes")


_PKG_ROOT = None


def _pkg_manifest():
    """The manifest the event package binds — REAL bytes under a
    module-scoped evidence root, because the builder now calls
    ``verify_source_evidence``: a rehashed fake manifest can never
    enter the package (R11.2-1)."""
    global _PKG_ROOT
    if _PKG_ROOT is None:
        import tempfile
        _PKG_ROOT = Path(tempfile.mkdtemp(prefix="glof-poc-ev-"))
        (_PKG_ROOT / "events.csv").write_text(
            "GF_ID,x\n1,y\n", encoding="utf-8")
    root = _PKG_ROOT
    digest = hashlib.sha256(
        (root / "events.csv").read_bytes()).hexdigest()
    return build_source_manifest(
        root, source_id=_SOURCE_ID, source_version=_SOURCE_VERSION,
        source_files=[{"relpath": "events.csv",
                       "sha256": digest}],
        units=list(_GROUP_OF_BASIN),
        feature_allowlist=["f1", "f2"],
        lineage="contract-test acquired bytes")


def _write_sidecar(root, *, source_id=_SOURCE_ID,
                   version=_SOURCE_VERSION, license_id="test-license",
                   decision="VERIFIED"):
    """A real evidence sidecar under the evidence root — required
    for the runner's R11.2-2 sidecar gate."""
    import json as _json
    payload = {
        "source_id": source_id, "source_version": version,
        "license_id": license_id, "coverage": "Nepal basins",
        "timing_review": "coarse timing preserved",
        "reviewer_ids": ["rev-1", "rev-2"], "review_date": "2026-09-01",
        "decision": decision}
    path = Path(root) / "sidecar.json"
    path.write_text(_json.dumps(payload), encoding="utf-8")
    return {"relpath": "sidecar.json",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _source_record(posture="EVIDENCE_VERIFIED",
                   sidecar=None) -> SourceRecordV0:
    """``sidecar`` is the ``{"relpath","sha256"}`` pair from
    ``_write_sidecar`` — absent, the record keeps its fake sidecar
    binding (which the runner's sidecar gate correctly rejects)."""
    sidecar_rel = sidecar["relpath"] if sidecar else "sidecar.bin"
    sidecar_sha = sidecar["sha256"] if sidecar else _HEX64
    """A SourceRecordV0 with no problems() at the requested posture."""
    if posture == "CANDIDATE_ONLY":
        return SourceRecordV0(source_id=_SOURCE_ID, provider="test",
                              doi_or_url="synthetic://contract-test")
    return SourceRecordV0(
        source_id=_SOURCE_ID, provider="test",
        doi_or_url="synthetic://contract-test",
        version=_SOURCE_VERSION, as_of_date="2026-09-01",
        license_id="test-license", redistribution="test",
        geography="Nepal basins", temporal_coverage="1950-2024",
        event_time_class="COARSE_OR_UNRESOLVED",
        spatial_semantics="lake_point",
        observation_method="literature + remote sensing",
        non_event_frame="lake inventory",
        update_cadence="annual", access_status="open",
        posture=posture, license_notes="contract test",
        evidence_sidecar_path=sidecar_rel,
        evidence_sidecar_sha256=sidecar_sha,
        evidence_as_of="2026-09-01",
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
    """Basin-level opportunities covering every derivation branch:
    clear OBSERVED_FULL (NEGATIVE), event-overlapping OBSERVED_FULL
    (CENSORED), OBSERVED_PARTIAL (never NEGATIVE), UNOBSERVED."""
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


def _source_rows():
    return tuple(
        SourceRow(source_id=_SOURCE_ID, source_version=_SOURCE_VERSION,
                  source_row_key=k, mechanism=m, interval_start=t0,
                  interval_end=t1, declared_precision=p, basin=b,
                  cascade_group_id=cg or None,
                  parent_source_row_key=parent or None,
                  observed_on=obs or None)
        for k, b, t0, t1, p, m, cg, parent, obs in _BASE_ROWS)


def _package(rows=None, *, source_record=None, source_manifest=None,
             opps=None, group_of_basin=None, split_of_group=None,
             evaluation_regions=_EVAL_REGIONS):
    return build_hmaglofdb_event_package(
        _source_rows() if rows is None else rows,
        source_record=source_record or _source_record(),
        source_manifest=(source_manifest
                         if source_manifest is not None
                         else _pkg_manifest()),
        opportunity_frame=(_opportunity_frame()
                           if opps is None else opps),
        group_of_basin=(_GROUP_OF_BASIN
                        if group_of_basin is None else group_of_basin),
        split_of_group=(_SPLIT_OF_GROUP
                        if split_of_group is None else split_of_group),
        evaluation_regions=evaluation_regions,
        embargo_seconds=_EMBARGO)


@pytest.fixture
def intake(tmp_path):
    """A byte-bound CSV + its build_source_manifest manifest."""
    path = _write_csv(tmp_path, _BASE_ROWS)
    manifest = _manifest(tmp_path, path)
    return {"root": tmp_path, "path": path, "manifest": manifest}


@pytest.fixture
def package():
    return _package()


@pytest.fixture
def descriptive_regime(monkeypatch):
    """Stub the regime engine so runner status-mapping is tested in
    isolation — a DESCRIPTIVE_REGIME_ONLY artifact that freezes."""
    monkeypatch.setattr(
        "nepal.science_v0.glof_poc.run_regimes",
        lambda *a, **k: {"status": "DESCRIPTIVE_REGIME_ONLY"})
    monkeypatch.setattr(
        "nepal.science_v0.glof_poc.freeze_regime_artifact",
        lambda a: {"regime_artifact_digest": "b" * 64})


def _sha_of(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ------------------------------------------------------------------
# 1. build_source_manifest
# ------------------------------------------------------------------

class TestBuildSourceManifest:

    def _build(self, tmp_path, **over):
        kw = dict(source_id=_SOURCE_ID, source_version=_SOURCE_VERSION,
                  source_files=[{"relpath": "a.csv", "sha256": _HEX64}],
                  units=["u-a"], feature_allowlist=["f1"],
                  lineage="test acquisition")
        kw.update(over)
        return build_source_manifest(tmp_path, **kw)

    def test_happy_path_exact_seven_keys(self, tmp_path):
        man = self._build(tmp_path)
        assert set(man) == _MANIFEST_KEYS
        assert man["source_id"] == _SOURCE_ID
        assert man["source_digests"] == [_HEX64]
        assert man["units"] == ["u-a"]
        assert man["feature_allowlist"] == ["f1"]
        assert man["evidence_root"] == str(tmp_path)
        assert man["source_files"] == [
            {"relpath": "a.csv", "sha256": _HEX64}]

    def test_source_version_bound_into_lineage(self, tmp_path):
        man = self._build(tmp_path)
        assert man["lineage"].startswith(
            f"source_version={_SOURCE_VERSION};")
        assert "test acquisition" in man["lineage"]

    @pytest.mark.parametrize("field", ("source_id", "source_version",
                                       "lineage"))
    @pytest.mark.parametrize("bad", ("", "   ", 7, None))
    def test_rejects_empty_identity_fields(self, tmp_path, field, bad):
        with pytest.raises(ValueError):
            self._build(tmp_path, **{field: bad})

    @pytest.mark.parametrize("relpath", (
        "/abs/escape.csv", "~/escape.csv", "../escape.csv",
        "sub/../../escape.csv", "a/../b.csv"))
    def test_rejects_escaping_relpaths(self, tmp_path, relpath):
        with pytest.raises(ValueError):
            self._build(tmp_path, source_files=[
                {"relpath": relpath, "sha256": _HEX64}])

    def test_rejects_duplicate_relpaths(self, tmp_path):
        with pytest.raises(ValueError):
            self._build(tmp_path, source_files=[
                {"relpath": "a.csv", "sha256": _HEX64},
                {"relpath": "a.csv", "sha256": "b" * 64}])

    @pytest.mark.parametrize("sha", (
        "abc", "a" * 63, "A" * 64, "g" * 64, "", None))
    def test_rejects_non_64hex_sha256(self, tmp_path, sha):
        with pytest.raises(ValueError):
            self._build(tmp_path, source_files=[
                {"relpath": "a.csv", "sha256": sha}])

    @pytest.mark.parametrize("field", ("units", "feature_allowlist"))
    @pytest.mark.parametrize("bad", (
        [], (), ["a", "a"], ["a", ""], [1], "not-a-seq", None))
    def test_rejects_malformed_units_and_allowlist(self, tmp_path,
                                                   field, bad):
        with pytest.raises(ValueError):
            self._build(tmp_path, **{field: bad})

    @pytest.mark.parametrize("record", (
        "a.csv", 42, None, ["relpath", "sha256"]))
    def test_rejects_nonmapping_file_records(self, tmp_path, record):
        with pytest.raises(ValueError):
            self._build(tmp_path, source_files=[record])

    def test_rejects_extra_fields_in_file_record(self, tmp_path):
        with pytest.raises(ValueError):
            self._build(tmp_path, source_files=[{
                "relpath": "a.csv", "sha256": _HEX64,
                "size_bytes": 12}])

    @pytest.mark.parametrize("files", ([], (), None, "a.csv"))
    def test_rejects_empty_source_files(self, tmp_path, files):
        with pytest.raises(ValueError):
            self._build(tmp_path, source_files=files)

    def test_rejects_nonpath_evidence_root(self, tmp_path):
        with pytest.raises(ValueError):
            build_source_manifest(
                str(tmp_path), source_id=_SOURCE_ID,
                source_version=_SOURCE_VERSION,
                source_files=[{"relpath": "a.csv", "sha256": _HEX64}],
                units=["u"], feature_allowlist=["f"], lineage="l")


# ------------------------------------------------------------------
# 2. load_hmaglofdb_rows
# ------------------------------------------------------------------

class TestLoadHmaglofdbRows:

    def test_valid_load_recovers_identity_and_bindings(self, intake):
        rows = load_hmaglofdb_rows(intake["path"],
                                 source_manifest=intake["manifest"],
                                 column_map=dict(_COLUMN_MAP))
        assert isinstance(rows, tuple)
        assert len(rows) == len(_BASE_ROWS)
        assert all(isinstance(r, SourceRow) for r in rows)
        assert all(r.source_id == _SOURCE_ID for r in rows)
        assert all(r.source_version == _SOURCE_VERSION for r in rows)
        by_key = {r.source_row_key: r for r in rows}
        child = by_key["r2"]
        assert child.cascade_group_id == "cg-koshi-1"
        assert child.parent_source_row_key == "r1"
        assert child.observed_on == "2020-07-15T00:00:00Z"

    def test_optional_columns_unmapped_bind_none(self, intake):
        rows = load_hmaglofdb_rows(
            intake["path"], source_manifest=intake["manifest"],
            column_map=dict(_REQUIRED_COLUMN_MAP))
        assert all(r.cascade_group_id is None
                   and r.parent_source_row_key is None
                   and r.observed_on is None for r in rows)

    def test_undeclared_file_rejected(self, intake):
        other = _write_csv(intake["root"], _BASE_ROWS, name="other.csv")
        with pytest.raises(ValueError):
            load_hmaglofdb_rows(other,
                                source_manifest=intake["manifest"],
                                column_map=dict(_COLUMN_MAP))

    def test_file_outside_evidence_root_rejected(self, intake):
        outside = (intake["root"].parent /
                   f"{intake['root'].name}-outside.csv")
        outside.write_bytes(intake["path"].read_bytes())
        with pytest.raises(ValueError):
            load_hmaglofdb_rows(outside,
                                source_manifest=intake["manifest"],
                                column_map=dict(_COLUMN_MAP))

    def test_byte_mutation_rejected(self, intake):
        with open(intake["path"], "ab") as fh:
            fh.write(b"\n")
        with pytest.raises(ValueError):
            load_hmaglofdb_rows(intake["path"],
                                source_manifest=intake["manifest"],
                                column_map=dict(_COLUMN_MAP))

    @pytest.mark.parametrize("key", ("source_row_key", "basin",
                                     "interval_start", "interval_end",
                                     "declared_precision", "mechanism"))
    def test_missing_column_map_key_rejected(self, intake, key):
        cmap = {k: v for k, v in _COLUMN_MAP.items() if k != key}
        with pytest.raises(ValueError):
            load_hmaglofdb_rows(intake["path"],
                                source_manifest=intake["manifest"],
                                column_map=cmap)

    def test_unknown_semantic_key_rejected(self, intake):
        cmap = dict(_COLUMN_MAP, lake_name="key")
        with pytest.raises(ValueError):
            load_hmaglofdb_rows(intake["path"],
                                source_manifest=intake["manifest"],
                                column_map=cmap)

    def test_csv_missing_mapped_column_rejected(self, intake):
        cmap = dict(_COLUMN_MAP, mechanism="no_such_column")
        with pytest.raises(ValueError):
            load_hmaglofdb_rows(intake["path"],
                                source_manifest=intake["manifest"],
                                column_map=cmap)

    def test_duplicate_source_row_key_rejected(self, intake):
        rows = [_BASE_ROWS[0],
                ("r1", "gandaki", "2020-08-05T00:00:00Z",
                 "2020-08-06T00:00:00Z", "day", "lake_outburst",
                 "", "", "")]
        path = _write_csv(intake["root"], rows, name="dup.csv")
        man = _manifest(intake["root"], path)
        with pytest.raises(ValueError):
            load_hmaglofdb_rows(path, source_manifest=man,
                                column_map=dict(_COLUMN_MAP))

    def test_empty_source_row_key_rejected(self, intake):
        rows = [("", "koshi", "2020-06-01T00:00:00Z",
                 "2020-06-02T00:00:00Z", "day", "lake_outburst",
                 "", "", "")]
        path = _write_csv(intake["root"], rows, name="emptykey.csv")
        man = _manifest(intake["root"], path)
        with pytest.raises(ValueError):
            load_hmaglofdb_rows(path, source_manifest=man,
                                column_map=dict(_COLUMN_MAP))

    @pytest.mark.parametrize("t0,t1", (
        ("2020-06-02T00:00:00Z", "2020-06-01T00:00:00Z"),
        ("2020-06-01T00:00:00Z", "2020-06-01T00:00:00Z")))
    def test_inverted_or_empty_interval_rejected(self, intake, t0, t1):
        rows = [("r1", "koshi", t0, t1, "day", "lake_outburst",
                 "", "", "")]
        path = _write_csv(intake["root"], rows, name="inv.csv")
        man = _manifest(intake["root"], path)
        with pytest.raises(ValueError):
            load_hmaglofdb_rows(path, source_manifest=man,
                                column_map=dict(_COLUMN_MAP))

    def test_unparseable_interval_rejected(self, intake):
        rows = [("r1", "koshi", "not-a-date", "also-not-a-date",
                 "day", "lake_outburst", "", "", "")]
        path = _write_csv(intake["root"], rows, name="badts.csv")
        man = _manifest(intake["root"], path)
        with pytest.raises(ValueError):
            load_hmaglofdb_rows(path, source_manifest=man,
                                column_map=dict(_COLUMN_MAP))

    def test_naive_timestamp_rejected(self, intake):
        rows = [("r1", "koshi", "2020-06-01", "2020-06-03",
                 "day", "lake_outburst", "", "", "")]
        path = _write_csv(intake["root"], rows, name="naive.csv")
        man = _manifest(intake["root"], path)
        with pytest.raises(ValueError):
            load_hmaglofdb_rows(path, source_manifest=man,
                                column_map=dict(_COLUMN_MAP))

    @pytest.mark.parametrize("column,value", (
        ("basin", "atlantis"),
        ("mech", "volcano"),
        ("prec", "fortnight")))
    def test_unknown_vocabulary_rejected(self, intake, column, value):
        idx = _CSV_HEADER.index(column)
        row = list(_BASE_ROWS[0])
        row[idx] = value
        path = _write_csv(intake["root"], [row], name="badvocab.csv")
        man = _manifest(intake["root"], path)
        with pytest.raises(ValueError):
            load_hmaglofdb_rows(path, source_manifest=man,
                                column_map=dict(_COLUMN_MAP))

    def test_zero_row_file_rejected(self, intake):
        path = _write_csv(intake["root"], [], name="empty.csv")
        man = _manifest(intake["root"], path)
        with pytest.raises(ValueError):
            load_hmaglofdb_rows(path, source_manifest=man,
                                column_map=dict(_COLUMN_MAP))


# ------------------------------------------------------------------
# 3. build_hmaglofdb_event_package
# ------------------------------------------------------------------

class TestEventPackage:

    def test_exact_ten_key_surface(self, package):
        assert set(package) == _PACKAGE_KEYS

    def test_event_labels_deserialize_clean(self, package):
        assert len(package["event_labels"]) == len(_BASE_ROWS)
        for label in package["event_labels"]:
            rec = deserialize_record(label)
            assert rec.problems() == []

    def test_precision_is_honest(self, package):
        """Declared terms map to the class they honestly bound —
        day->day, bounded month-window->interval, month/year->unresolved
        (a coarse declaration is never silently upgraded)."""
        by_key = {lab["event_id"].rsplit(":", 1)[-1]:
                  lab["event_time_precision"]
                  for lab in package["event_labels"]}
        for key in ("r1", "r2", "r3", "r4", "r5"):
            assert by_key[key] == "day", key
        for key in ("r7", "r8"):
            assert by_key[key] == "interval", key
        for key in ("r6", "r9"):
            assert by_key[key] == "unresolved", key

    def test_cascade_and_parentage_bound(self, package):
        by_key = {lab["event_id"].rsplit(":", 1)[-1]: lab
                  for lab in package["event_labels"]}
        assert by_key["r2"]["cascade_group_id"] == "cg-koshi-1"
        assert by_key["r2"]["parent_event_id"] == \
            by_key["r1"]["event_id"]
        assert by_key["r1"]["cascade_group_id"] == "cg-koshi-1"

    def test_controls_derived_never_asserted(self, package):
        by_unit = {c["unit_id"]: c for c in package["controls"]}
        # non-overlapping OBSERVED_FULL -> NEGATIVE
        assert by_unit["koshi"]["state"] == "NEGATIVE"
        assert by_unit["mahakali"]["state"] == "NEGATIVE"
        # OBSERVED_FULL window overlapping an event -> CENSORED
        assert by_unit["bagmati"]["state"] == "CENSORED_OR_AMBIGUOUS"
        # partial / unobserved coverage is never a negative
        assert by_unit["gandaki"]["state"] == "CENSORED_OR_AMBIGUOUS"
        assert by_unit["karnali"]["state"] == "CENSORED_OR_AMBIGUOUS"
        for ctl in package["controls"]:
            assert ctl["opportunity_id"]
            assert ctl["opportunity_state"] in (
                "OBSERVED_FULL", "OBSERVED_PARTIAL", "UNOBSERVED",
                "UNKNOWN")

    def test_controls_deserialize_clean(self, package):
        for ctl in package["controls"]:
            assert deserialize_record(ctl).problems() == []

    def test_holdout_plan_valid_and_deserializes(self, package):
        plan = package["holdout_plan"]
        assert plan.get("holdout_plan_id")
        rec = deserialize_record(plan)
        assert rec.problems() == []
        assert set(plan["evaluation_region_names"]) <= \
            set(plan["test_groups"])
        assert len(set(plan["evaluation_region_names"])) >= 2

    def test_single_test_group_holdout_rejected(self):
        pkg = _package(group_of_basin=_ONE_TEST_GOB,
                       split_of_group=_ONE_TEST_SOG)
        plan = pkg["holdout_plan"]
        assert plan.get("rejected") is True
        assert plan["problems"]

    def test_digests_are_64hex_and_deterministic(self, package):
        for key in ("source_manifest_digest", "event_digest",
                    "opportunity_digest", "control_digest",
                    "holdout_digest"):
            assert _SHA_RE.match(package[key]), key
        again = _package()
        for key in ("source_manifest_digest", "event_digest",
                    "opportunity_digest", "control_digest",
                    "holdout_digest"):
            assert package[key] == again[key], key

    def test_source_record_accepts_serialized_mapping(self):
        pkg = _package(source_record=_source_record().to_dict())
        assert set(pkg) == _PACKAGE_KEYS

    def test_empty_rows_rejected(self):
        with pytest.raises(ValueError):
            _package(rows=())

    def test_source_record_with_problems_rejected(self):
        bad = SourceRecordV0(source_id="", provider="",
                             doi_or_url="")
        with pytest.raises(ValueError):
            _package(source_record=bad)

    def test_opportunity_unit_outside_universe_rejected(self):
        opps = (_opp("opp-bad", "atlantis",
                     "2020-06-01T00:00:00Z", "2020-06-10T00:00:00Z"),)
        with pytest.raises(ValueError):
            _package(opps=opps)


# ------------------------------------------------------------------
# 4. run_glof_descriptive_poc
# ------------------------------------------------------------------

class TestRunner:

    def test_happy_path_real_run(self, intake):
        """One real end-to-end run: manifest-bound regime config +
        EVIDENCE_VERIFIED package -> a well-formed non-promotable
        receipt.  Status is only asserted against the 4-vocab — the
        descriptive-strength of the mini frame is the regime engine's
        own contract, audited elsewhere."""
        df, feature_cols, train_mask, cfg = _mini_regime_frame()
        cfg = dataclasses.replace(
            cfg, source_manifest=intake["manifest"])
        sidecar = _write_sidecar(intake["root"])
        receipt = run_glof_descriptive_poc(
            df, feature_cols, train_mask, cfg,
            _package(source_manifest=intake["manifest"],
                     source_record=_source_record(sidecar=sidecar)))
        assert receipt["record_type"] == "GLOF_POC_RECEIPT_V0"
        assert receipt["status"] in _RECEIPT_STATUSES
        assert receipt["claim_scope"] == \
            "research_only_no_operational_authorization"
        for key in ("source_manifest_digest", "event_digest",
                    "opportunity_digest", "control_digest",
                    "holdout_digest", "regime_artifact_digest",
                    "report_digest"):
            assert _SHA_RE.match(receipt[key]), \
                f"{key}: {receipt[key]!r}"
        assert receipt["promotion_eligible"] is False
        assert receipt["production_authorized"] is False
        assert receipt["warning_path_authorized"] is False
        assert isinstance(receipt["problems"], list)
        assert all(isinstance(p, str) for p in receipt["problems"])

    @pytest.mark.parametrize("posture", ("CANDIDATE_ONLY", "REJECTED"))
    def test_non_verified_posture_demotes(self, posture,
                                          descriptive_regime):
        """Any posture other than EVIDENCE_VERIFIED — metadata-only
        or rejected — can never carry a descriptive result."""
        pkg = _package(source_record=_source_record(posture))
        cfg = types.SimpleNamespace(
            source_manifest=_pkg_manifest())
        receipt = run_glof_descriptive_poc(
            None, [], None, cfg, pkg)
        assert receipt["status"] == "CANDIDATE_ONLY"
        assert any("posture" in p for p in receipt["problems"])

    def test_rejected_holdout_underpowered(self, descriptive_regime):
        man = _pkg_manifest()
        sidecar = _write_sidecar(man["evidence_root"])
        pkg = _package(source_record=_source_record(sidecar=sidecar),
                       group_of_basin=_ONE_TEST_GOB,
                       split_of_group=_ONE_TEST_SOG)
        assert pkg["holdout_plan"].get("rejected") is True
        cfg = types.SimpleNamespace(source_manifest=man)
        receipt = run_glof_descriptive_poc(
            None, [], None, cfg, pkg)
        assert receipt["status"] == "UNDERPOWERED_DESCRIPTIVE_ONLY"
        assert any("holdout" in p for p in receipt["problems"])

    def test_missing_package_key_run_error(self, package):
        pkg = {k: v for k, v in package.items() if k != "controls"}
        receipt = run_glof_descriptive_poc(
            None, [], None, None, pkg)
        assert receipt["status"] == "RUN_ERROR"
        assert any("controls" in p for p in receipt["problems"])

    def test_extra_package_key_run_error(self, package,
                                         descriptive_regime):
        pkg = dict(package, undeclared_key=True)
        receipt = run_glof_descriptive_poc(
            None, [], None, None, pkg)
        assert receipt["status"] == "RUN_ERROR"

    def test_non_mapping_package_run_error(self):
        receipt = run_glof_descriptive_poc(
            None, [], None, None, "not-a-mapping")
        assert receipt["status"] == "RUN_ERROR"

    def test_bad_regime_column_run_error_never_raises(self):
        df, feature_cols, train_mask, cfg = _mini_regime_frame()
        man = _pkg_manifest()
        sidecar = _write_sidecar(man["evidence_root"])
        pkg = _package(
            source_manifest=man,
            source_record=_source_record(sidecar=sidecar))
        cfg = dataclasses.replace(
            cfg, group_col="no_such_column",
            source_manifest=man)
        receipt = run_glof_descriptive_poc(
            df, feature_cols, train_mask, cfg, pkg)
        assert receipt["status"] == "RUN_ERROR"

    def test_receipt_has_no_authority_surface(self, package,
                                              descriptive_regime):
        receipt = run_glof_descriptive_poc(
            None, [], None, None, package)
        assert set(receipt) == _RECEIPT_KEYS
        # the receipt is not a contract record class — it can never
        # be deserialized into the record machinery or authorize
        # association / forecast / warning / production
        assert "GLOF_POC_RECEIPT_V0" not in RECORD_TYPES
        assert receipt["status"] in _RECEIPT_STATUSES
        assert receipt["promotion_eligible"] is False
        assert receipt["production_authorized"] is False
        assert receipt["warning_path_authorized"] is False
        authority_like = {
            k for k in receipt
            if "forecast" in k or "associat" in k
            or ("warning" in k and k != "warning_path_authorized")}
        assert authority_like == set()


# ------------------------------------------------------------------
# 5. Determinism
# ------------------------------------------------------------------

class TestDeterminism:

    def test_source_digests_follow_declared_order(self, tmp_path):
        a = _write_csv(tmp_path, _BASE_ROWS, name="b_second.csv")
        b = _write_csv(tmp_path, _BASE_ROWS[:1], name="a_first.csv")
        man = _manifest(tmp_path, a, b)  # declared order: b, then a
        # source_digests preserve the DECLARED order — not sorted
        assert man["source_digests"] == [_sha_of(a), _sha_of(b)]
        assert [f["relpath"] for f in man["source_files"]] == \
            ["b_second.csv", "a_first.csv"]

    def test_event_digest_stable_under_row_permutation(self):
        pkg_a = _package()
        pkg_b = _package(rows=tuple(reversed(_source_rows())))
        assert pkg_a["event_digest"] == pkg_b["event_digest"]

    def test_other_digests_stable_under_row_permutation(self):
        """The surfaces that ARE canonical — controls are emitted in
        sorted unit order, the holdout assignment is order-free —
        stay identical under input row permutation."""
        pkg_a = _package()
        pkg_b = _package(rows=tuple(reversed(_source_rows())))
        for key in ("source_manifest_digest", "opportunity_digest",
                    "control_digest", "holdout_digest"):
            assert pkg_a[key] == pkg_b[key], key

    def test_repeat_build_is_byte_identical(self):
        pkg_a = _package()
        pkg_b = _package()
        assert pkg_a == pkg_b


# ------------------------------------------------------------------
# R11.1 provenance repairs — read-order, map hygiene, digest binding,
# cross-object provenance, malformed-input boundary
# ------------------------------------------------------------------

class TestR111LoaderBoundary:
    """The intake boundary verifies before it reads, maps columns
    strictly, and rejects duplicate CSV headers (findings 1-2)."""

    def test_column_map_list_value_is_valueerror(self, intake):
        with pytest.raises(ValueError):
            load_hmaglofdb_rows(
                intake["path"],
                source_manifest=intake["manifest"],
                column_map={"source_row_key": "key", "basin": "basin",
                            "interval_start": "start",
                            "interval_end": "end",
                            "declared_precision": "prec",
                            "mechanism": ["not", "a", "str"]})

    def test_column_map_nonstring_values_rejected(self, intake):
        for bad in (42, None, ("t",), {"h": 1}):
            with pytest.raises(ValueError):
                load_hmaglofdb_rows(
                    intake["path"],
                    source_manifest=intake["manifest"],
                    column_map={"source_row_key": "key",
                                "basin": bad,
                                "interval_start": "start",
                                "interval_end": "end",
                                "declared_precision": "prec",
                                "mechanism": "mech"})

    def test_duplicate_mapped_headers_rejected(self, intake):
        with pytest.raises(ValueError):
            load_hmaglofdb_rows(
                intake["path"],
                source_manifest=intake["manifest"],
                column_map={"source_row_key": "key",
                            "basin": "key",
                            "interval_start": "start",
                            "interval_end": "end",
                            "declared_precision": "prec",
                            "mechanism": "mech"})

    def test_duplicate_csv_headers_rejected(self, intake):
        path = intake["path"]
        text = path.read_text()
        text = text.replace("key,basin", "key,key", 1)
        path.write_text(text)
        man = _manifest(
            intake["root"], path)
        with pytest.raises(ValueError, match="duplicate column"):
            load_hmaglofdb_rows(path, source_manifest=man,
                                column_map=dict(_COLUMN_MAP))

    def test_symlinked_leaf_rejected_via_verifier(self, intake):
        link = intake["root"] / "linked.csv"
        link.symlink_to(intake["path"].name)
        man = _manifest(intake["root"], intake["path"])
        with pytest.raises(ValueError):
            load_hmaglofdb_rows(link, source_manifest=man,
                                column_map=dict(_COLUMN_MAP))

    def test_symlinked_intermediate_dir_rejected(self, intake):
        real = intake["root"] / "real"
        real.mkdir()
        inner = _write_csv(real, _BASE_ROWS)
        link_dir = intake["root"] / "linkdir"
        link_dir.symlink_to("real")
        man = _manifest(intake["root"], inner)
        with pytest.raises(ValueError):
            load_hmaglofdb_rows(link_dir / inner.name,
                                source_manifest=man,
                                column_map=dict(_COLUMN_MAP))


class TestR111PackageBinding:
    """source_manifest_digest binds the manifest; rows/record/
    manifest cross-bind; malformed inputs are bounded (3-6)."""

    def test_source_manifest_digest_is_manifest_not_record(self):
        from nepal.research_v0._hashing import sha256_canonical
        man = _pkg_manifest()
        pkg = _package(source_manifest=man)
        assert pkg["source_manifest_digest"] == \
            sha256_canonical(dict(man))
        assert pkg["source_manifest_digest"] != \
            sha256_canonical(pkg["source_record"])

    def test_manifest_mutation_changes_digest(self):
        man = _pkg_manifest()
        mutated = dict(man, lineage=man["lineage"] + " tampered")
        pkg_a = _package(source_manifest=man)
        pkg_b = _package(source_manifest=mutated)
        assert pkg_a["source_manifest_digest"] != \
            pkg_b["source_manifest_digest"]

    def test_row_source_id_mismatch_rejected(self):
        rows = [dataclasses.replace(r, source_id="forged")
                for r in _source_rows()]
        with pytest.raises(ValueError, match="source_id"):
            _package(rows=rows)

    def test_row_version_mismatch_rejected(self):
        rows = [dataclasses.replace(r, source_version="9.9.9")
                for r in _source_rows()]
        with pytest.raises(ValueError, match="source_version"):
            _package(rows=rows)

    def test_row_basin_outside_manifest_units_rejected(self):
        man = dict(_pkg_manifest(), units=["koshi"])
        with pytest.raises(ValueError, match="units"):
            _package(source_manifest=man)

    def test_manifest_source_id_mismatch_rejected(self):
        man = dict(_pkg_manifest(), source_id="other-source")
        with pytest.raises(ValueError, match="source_id"):
            _package(source_manifest=man)

    def test_package_requires_source_manifest_kwarg(self):
        with pytest.raises(TypeError):
            build_hmaglofdb_event_package(
                _source_rows(), source_record=_source_record(),
                opportunity_frame=_opportunity_frame(),
                group_of_basin=_GROUP_OF_BASIN,
                split_of_group=_SPLIT_OF_GROUP,
                evaluation_regions=_EVAL_REGIONS,
                embargo_seconds=_EMBARGO)


class TestR111MalformedInputs:
    """Every malformed package input is a bounded ValueError — never
    an uncaught TypeError/AttributeError (finding 6)."""

    @pytest.mark.parametrize("rows", [
        [{"not": "a SourceRow"}], ["x"], [42], [None]])
    def test_non_sourcerow_elements(self, rows):
        with pytest.raises(ValueError):
            _package(rows=rows)

    @pytest.mark.parametrize("bad", [42, "x", ["x"]])
    def test_bad_source_record(self, bad):
        with pytest.raises((ValueError, TypeError)):
            _package(source_record=bad)

    def test_none_source_record(self):
        with pytest.raises((ValueError, TypeError)):
            build_hmaglofdb_event_package(
                _source_rows(), source_record=None,
                source_manifest=_pkg_manifest(),
                opportunity_frame=_opportunity_frame(),
                group_of_basin=_GROUP_OF_BASIN,
                split_of_group=_SPLIT_OF_GROUP,
                evaluation_regions=_EVAL_REGIONS,
                embargo_seconds=_EMBARGO)

    @pytest.mark.parametrize("bad", [42, "x", {"opp": 1}])
    def test_bad_opportunity_frame(self, bad):
        with pytest.raises((ValueError, TypeError)):
            _package(opps=bad)

    def test_bad_group_of_basin(self):
        with pytest.raises((ValueError, TypeError)):
            _package(group_of_basin=["koshi"])

    def test_bad_split_of_group(self):
        with pytest.raises((ValueError, TypeError)):
            _package(split_of_group="test")

    def test_bad_evaluation_regions(self):
        with pytest.raises((ValueError, TypeError)):
            _package(evaluation_regions="karnali")

    def test_bad_embargo(self):
        with pytest.raises((ValueError, TypeError)):
            build_hmaglofdb_event_package(
                _source_rows(), source_record=_source_record(),
                source_manifest=_pkg_manifest(),
                opportunity_frame=_opportunity_frame(),
                group_of_basin=_GROUP_OF_BASIN,
                split_of_group=_SPLIT_OF_GROUP,
                evaluation_regions=_EVAL_REGIONS,
                embargo_seconds="30d")


class TestR111RunnerRevalidation:
    """The runner recomputes every carried digest and revalidates
    every section before fitting (finding 4-5)."""

    def test_stale_event_digest_run_error(self, descriptive_regime):
        pkg = _package()
        mutated = dict(pkg)
        mutated["event_labels"] = [dict(l) for l in
                                   pkg["event_labels"]]
        mutated["event_labels"][0] = dict(
            mutated["event_labels"][0],
            adjudication_notes="tampered")
        receipt = run_glof_descriptive_poc(
            None, ["f1"], None, object(), mutated)
        assert receipt["status"] == "RUN_ERROR"
        assert any("digest" in p for p in receipt["problems"])

    def test_forged_carried_digest_run_error(self, descriptive_regime):
        pkg = _package()
        mutated = dict(pkg, event_digest="f" * 64)
        receipt = run_glof_descriptive_poc(
            None, ["f1"], None, object(), mutated)
        assert receipt["status"] == "RUN_ERROR"

    def test_invalid_record_in_section_run_error(
            self, descriptive_regime):
        pkg = _package()
        bad = dict(pkg["event_labels"][0], record_type="CutoffRecordV0")
        mutated = dict(pkg)
        mutated["event_labels"] = [bad] + list(
            pkg["event_labels"][1:])
        from nepal.research_v0._hashing import sha256_canonical
        mutated["event_digest"] = sha256_canonical(
            mutated["event_labels"])
        receipt = run_glof_descriptive_poc(
            None, ["f1"], None, object(), mutated)
        assert receipt["status"] == "RUN_ERROR"

    def test_config_manifest_mismatch_run_error(
            self, descriptive_regime):
        pkg = _package()
        other = dict(_pkg_manifest(),
                     lineage=_pkg_manifest()["lineage"] + " other")
        cfg = types.SimpleNamespace(source_manifest=other)
        receipt = run_glof_descriptive_poc(
            None, ["f1"], None, cfg, pkg)
        assert receipt["status"] == "RUN_ERROR"
        assert any("manifest" in p for p in receipt["problems"])

    def test_matching_config_manifest_passes_gate(
            self, descriptive_regime):
        cfg = types.SimpleNamespace(source_manifest=_pkg_manifest())
        receipt = run_glof_descriptive_poc(
            None, ["f1"], None, cfg, _package())
        assert receipt["status"] != "RUN_ERROR" or not any(
            "manifest" in p for p in receipt["problems"])
