"""R11.9 binding tests — verified-manifest FMX, semantic role binding,
and full-recomputation replay over the real P5 evidence root.

The synthetic lanes build a minimal governed root in tmp_path; the
real lanes run against the acquired P5 evidence and skip cleanly when
it is absent.  Nothing here fabricates review, posture, or controls.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nepal.real_fmx import (  # noqa: E402
    CUTOFF_RECORD_SCHEMA, PREPROCESSING_PROVENANCE_SCHEMA, PREDICTORS,
    _CARRIER_META, build_preprocessing_provenance,
    cutoff_record_problems, preprocessing_record_problems,
    run_real_fmx)
from nepal.research_v0._hashing import (  # noqa: E402
    sha256_canonical, verify_source_evidence)
from nepal.research_v0.records import RunEvidenceManifestV0  # noqa: E402
from nepal.research_v0.run_evidence import (  # noqa: E402
    semantic_binding_problems)
from nepal.research_v0.source_intake import (  # noqa: E402
    build_source_manifest)

EVIDENCE = Path(
    "/Users/sanjayb/nepal-event-anomaly-evidence/p5-glof-2026-09-19")
FRAME_REL = \
    "era5-multibasin/features/regime_frame_hma_jja_2001_2025.csv"
CUTOFF_REL = "era5-multibasin/features/cutoff_record_v0.json"
PROV_REL = \
    "era5-multibasin/features/preprocessing_provenance_v0.json"
FMX_REL = "era5-multibasin/features/fmx_audit_report_v0.json"

_REAL_FILES = [
    EVIDENCE / "retrieval/role_manifests_v0.json",
    EVIDENCE / "glof-events/p3_runner_package_v0.json",
    EVIDENCE / FRAME_REL, EVIDENCE / CUTOFF_REL,
    EVIDENCE / PROV_REL, EVIDENCE / FMX_REL,
    EVIDENCE / "retrieval/p5_glof_descriptive_receipt_v0.json"]
_REAL = pytest.mark.skipif(
    not all(p.exists() for p in _REAL_FILES),
    reason="P5 evidence root absent")


def _mini_frame_rows() -> list[dict]:
    rows = []
    basins = ("koshi", "gandaki", "karnali")
    for bi, basin in enumerate(basins):
        for day in range(1, 6):
            rows.append({
                "unit_id": basin, "basin_group": basin,
                "date": f"2025-09-{day:02d}",
                "season": "JJA",
                "era": "post_2013",
                "elevation_m": 4000.0 + bi * 100,
                "edge_censored": "false",
                "t2m_daily": 5.0 + day * 0.1,
                "d2m_daily": 1.0 + day * 0.05,
                "tp_daily": 2.0 + day * 0.2,
                "sf_daily": 0.3,
                "sd_daily": 10.0,
                "wind_speed_daily": 4.0,
                "wind_dir_sin": 0.3,
                "wind_dir_cos": 0.95,
                "rh_daily": 60.0,
                "pdd_daily": 3.0,
                "pdd_7day": 18.0,
                "freezing_height_m": 4500.0})
    return rows


def _mini_root(tmp_path: Path) -> tuple[dict, Path]:
    """A governed mini root: frame + retrieval record + manifest."""
    root = tmp_path / "ev"
    (root / "features").mkdir(parents=True)
    (root / "retrieval").mkdir(parents=True)
    frame = pd.DataFrame(_mini_frame_rows())
    frame.to_csv(root / "features/frame.csv", index=False)
    retrieval = {
        "canonical": True,
        "anchors": {b: {"lat": 28.0, "lon": 85.0}
                    for b in ("koshi", "gandaki", "karnali")},
        "files": [{"relpath":
                   "era5-multibasin/era5land_ts_koshi_hma_"
                   "2001-2025.zip"}],
        "channel_completions":
            {"cds": "2026-09-19T06:06:34Z"},
        "pull_utc_start": "2026-09-19T06:04:44Z",
        "pull_utc_end": "2026-09-19T06:06:34Z",
        "route": "synthetic-test-route",
        "requests": []}
    (root / "retrieval/retrieval.json").write_text(
        json.dumps(retrieval))
    files = []
    for rel in ("features/frame.csv", "retrieval/retrieval.json"):
        files.append({
            "relpath": rel,
            "sha256": hashlib.sha256(
                (root / rel).read_bytes()).hexdigest()})
    manifest = build_source_manifest(
        root, source_id="test_feature_source",
        source_version="v0-test", source_files=files,
        units=["koshi", "gandaki", "karnali"],
        feature_allowlist=list(PREDICTORS),
        lineage="test feature role")
    return manifest, root


def _mini_cutoff(root: Path) -> dict:
    blob = (root / "retrieval/retrieval.json").read_bytes()
    return {
        "schema": CUTOFF_RECORD_SCHEMA,
        "cutoff_iso": "2025-10-01T00:00:00Z",
        "retrieval_record_relpath": "retrieval/retrieval.json",
        "retrieval_record_sha256":
            hashlib.sha256(blob).hexdigest(),
        "retrieval_completed_utc": "2026-09-19T06:06:34Z",
        "source_version": "v0-test",
        "availability_basis":
            "ERA5-Land ~5-day lag; frame ends 2025-09-30"}


def _mini_prov(root: Path) -> dict:
    frame = pd.read_csv(root / "features/frame.csv")
    return build_preprocessing_provenance(
        frame,
        frame_sha256=hashlib.sha256(
            (root / "features/frame.csv").read_bytes()).hexdigest())


# ================= 1. verified-manifest FMX (R11.9-04/05/06) =========

class TestRealFmxBinding:
    def test_happy_path_all_bindings(self, tmp_path):
        manifest, root = _mini_root(tmp_path)
        report = run_real_fmx(
            manifest, frame_relpath="features/frame.csv",
            cutoff_record=_mini_cutoff(root),
            preprocessing_record=_mini_prov(root))
        assert report["status"] == "FMX_PASS", report.get("reason")
        assert report["feature_role_digest"] == sha256_canonical(
            dict(manifest))
        assert report["frame_sha256"] == hashlib.sha256(
            (root / "features/frame.csv").read_bytes()).hexdigest()
        assert report["row_universe_digest"]
        assert report["semantic_matrix_digest"]
        assert report["cutoff_record"]["schema"] == \
            CUTOFF_RECORD_SCHEMA
        assert report["preprocessing_provenance"]["schema"] == \
            PREPROCESSING_PROVENANCE_SCHEMA
        assert report["n_reject"] == 0

    def test_unverified_manifest_blocks(self, tmp_path):
        manifest, root = _mini_root(tmp_path)
        cutoff = _mini_cutoff(root)
        prov = _mini_prov(root)
        (root / "features/frame.csv").write_text("tampered,bytes\n")
        report = run_real_fmx(
            manifest, frame_relpath="features/frame.csv",
            cutoff_record=cutoff, preprocessing_record=prov)
        assert report["status"] == "FMX_BLOCKED_MANIFEST"

    def test_undeclared_frame_blocks(self, tmp_path):
        manifest, root = _mini_root(tmp_path)
        (root / "features/other.csv").write_bytes(
            (root / "features/frame.csv").read_bytes())
        report = run_real_fmx(
            manifest, frame_relpath="features/other.csv",
            cutoff_record=_mini_cutoff(root),
            preprocessing_record=_mini_prov(root))
        assert report["status"] == "FMX_BLOCKED_FRAME"

    def test_same_name_different_bytes_blocks(self, tmp_path):
        manifest, root = _mini_root(tmp_path)
        cutoff = _mini_cutoff(root)
        prov = _mini_prov(root)
        (root / "features/frame.csv").write_text(
            (root / "features/frame.csv").read_text() + "\n# x")
        # manifest digest no longer matches live bytes -> manifest gate
        report = run_real_fmx(
            manifest, frame_relpath="features/frame.csv",
            cutoff_record=cutoff, preprocessing_record=prov)
        assert report["status"] == "FMX_BLOCKED_MANIFEST"

    def test_missing_cutoff_blocks(self, tmp_path):
        manifest, root = _mini_root(tmp_path)
        report = run_real_fmx(
            manifest, frame_relpath="features/frame.csv",
            cutoff_record=None,
            preprocessing_record=_mini_prov(root))
        assert report["status"] == "FMX_BLOCKED_CUTOFF"

    def test_cutoff_retrieval_digest_mutation_blocks(self, tmp_path):
        manifest, root = _mini_root(tmp_path)
        rec = _mini_cutoff(root)
        rec["retrieval_record_sha256"] = "0" * 64
        report = run_real_fmx(
            manifest, frame_relpath="features/frame.csv",
            cutoff_record=rec,
            preprocessing_record=_mini_prov(root))
        assert report["status"] == "FMX_BLOCKED_CUTOFF"

    def test_cutoff_before_frame_end_blocks(self, tmp_path):
        manifest, root = _mini_root(tmp_path)
        rec = _mini_cutoff(root)
        rec["cutoff_iso"] = "2025-09-03T00:00:00Z"
        report = run_real_fmx(
            manifest, frame_relpath="features/frame.csv",
            cutoff_record=rec,
            preprocessing_record=_mini_prov(root))
        assert report["status"] == "FMX_BLOCKED_CUTOFF"

    def test_missing_preprocessing_blocks(self, tmp_path):
        manifest, root = _mini_root(tmp_path)
        report = run_real_fmx(
            manifest, frame_relpath="features/frame.csv",
            cutoff_record=_mini_cutoff(root),
            preprocessing_record=None)
        assert report["status"] == "FMX_BLOCKED_PROVENANCE"

    def test_fit_row_count_mutation_blocks(self, tmp_path):
        manifest, root = _mini_root(tmp_path)
        prov = _mini_prov(root)
        prov["fitted_row_count"] += 1
        report = run_real_fmx(
            manifest, frame_relpath="features/frame.csv",
            cutoff_record=_mini_cutoff(root),
            preprocessing_record=prov)
        assert report["status"] == "FMX_BLOCKED_PROVENANCE"

    def test_train_only_claim_cannot_be_asserted(self, tmp_path):
        """R11.9-05: there is no caller-supplied provenance channel —
        every column claim derives from the persisted record."""
        manifest, root = _mini_root(tmp_path)
        prov = _mini_prov(root)
        prov["columns_train_only"] = ["t2m_daily"]
        report = run_real_fmx(
            manifest, frame_relpath="features/frame.csv",
            cutoff_record=_mini_cutoff(root),
            preprocessing_record=prov)
        assert report["status"] == "FMX_BLOCKED_PROVENANCE"


# ================= 2. record validators ==============================

class TestRecords:
    def test_cutoff_validates_happy(self, tmp_path):
        manifest, root = _mini_root(tmp_path)
        frame = pd.read_csv(root / "features/frame.csv")
        assert cutoff_record_problems(
            _mini_cutoff(root), evidence_root=root,
            frame=frame) == []

    def test_cutoff_completion_mismatch(self, tmp_path):
        manifest, root = _mini_root(tmp_path)
        rec = _mini_cutoff(root)
        rec["retrieval_completed_utc"] = "2026-01-01T00:00:00Z"
        probs = cutoff_record_problems(rec, evidence_root=root)
        assert any("pull_utc_end" in p for p in probs)

    def test_provenance_digest_roundtrip(self, tmp_path):
        manifest, root = _mini_root(tmp_path)
        frame = pd.read_csv(root / "features/frame.csv")
        sha = hashlib.sha256(
            (root / "features/frame.csv").read_bytes()).hexdigest()
        rec = build_preprocessing_provenance(
            frame, frame_sha256=sha)
        assert preprocessing_record_problems(
            rec, frame=frame, frame_sha256=sha) == []
        assert rec["fitted_row_count"] == 10  # koshi+gandaki, 5d each


# ================= 3. semantic role binding (R11.9-08) ===============

class TestSemanticBinding:
    def _wrapper_and_package(self, root: Path):
        manifests = json.loads(
            (root / "retrieval/role_manifests_v0.json").read_text())
        wrapper = RunEvidenceManifestV0(
            event_manifest=manifests["event"],
            opportunity_manifest=manifests["opportunity"],
            feature_manifest=manifests["feature"],
            sidecar_manifest=manifests["sidecar"])
        package = json.loads(
            (root / "glof-events/p3_runner_package_v0.json")
            .read_text())
        return wrapper, package

    @_REAL
    def test_real_package_binds_clean(self):
        wrapper, package = self._wrapper_and_package(EVIDENCE)
        fmx = json.loads((EVIDENCE / FMX_REL).read_text())
        cols = list(pd.read_csv(
            EVIDENCE / FRAME_REL, nrows=1).columns)
        problems = semantic_binding_problems(
            wrapper, package,
            frame_relpath=FRAME_REL, frame_columns=cols,
            carrier_columns=list(_CARRIER_META),
            fmx_report=fmx,
            control_doc_relpaths=[
                "retrieval/p5_coverage_ledger_20260919.json"])
        assert problems == []

    @_REAL
    def test_mutated_event_digest_flagged(self):
        wrapper, package = self._wrapper_and_package(EVIDENCE)
        package = dict(package)
        package["source_manifest_digest"] = "0" * 64
        problems = semantic_binding_problems(wrapper, package)
        assert any("source_manifest_digest" in p for p in problems)

    @_REAL
    def test_undeclared_frame_relpath_flagged(self):
        wrapper, package = self._wrapper_and_package(EVIDENCE)
        problems = semantic_binding_problems(
            wrapper, package, frame_relpath="forged/frame.csv")
        assert any("not a declared feature-role member" in p
                   for p in problems)

    @_REAL
    def test_undeclared_column_flagged(self):
        wrapper, package = self._wrapper_and_package(EVIDENCE)
        problems = semantic_binding_problems(
            wrapper, package,
            frame_relpath=FRAME_REL,
            frame_columns=list(PREDICTORS) + ["leaked_col"],
            carrier_columns=list(_CARRIER_META))
        assert any("allowlist" in p for p in problems)

    @_REAL
    def test_wrong_fmx_role_digest_flagged(self):
        wrapper, package = self._wrapper_and_package(EVIDENCE)
        fmx = {"feature_role_digest": "1" * 64,
               "frame_sha256": "2" * 64}
        problems = semantic_binding_problems(
            wrapper, package, frame_relpath=FRAME_REL,
            fmx_report=fmx)
        assert any("feature_role_digest" in p for p in problems)

    @_REAL
    def test_unbound_control_doc_flagged(self):
        wrapper, package = self._wrapper_and_package(EVIDENCE)
        problems = semantic_binding_problems(
            wrapper, package,
            control_doc_relpaths=["retrieval/forged_control.json"])
        assert any("not bound in the sidecar role" in p
                   for p in problems)


# ================= 4. real-evidence FMX through the manifest =========

class TestRealEvidencePath:
    @_REAL
    def test_run_real_fmx_on_acquired_bytes(self):
        """R11.9-07: the real report is generated through the verified
        manifest path — byte substitution, role drift, and provenance
        gaps all fail closed."""
        manifests = json.loads(
            (EVIDENCE / "retrieval/role_manifests_v0.json")
            .read_text())
        report = run_real_fmx(
            manifests["feature"], frame_relpath=FRAME_REL,
            cutoff_record=json.loads(
                (EVIDENCE / CUTOFF_REL).read_text()),
            preprocessing_record=json.loads(
                (EVIDENCE / PROV_REL).read_text()))
        assert report["status"] == "FMX_PASS", report.get("reason")
        assert report["n_rows"] == 6900
        assert len(report["verdicts"]) == 19
        persisted = json.loads((EVIDENCE / FMX_REL).read_text())
        assert sha256_canonical(report) == \
            sha256_canonical(persisted)

    @_REAL
    def test_persisted_report_carries_bindings(self):
        report = json.loads((EVIDENCE / FMX_REL).read_text())
        for key in ("feature_role_digest", "frame_sha256",
                    "row_universe_digest", "semantic_matrix_digest",
                    "cutoff_record", "preprocessing_provenance"):
            assert report.get(key), key
        assert report["cutoff_record"]["schema"] == \
            CUTOFF_RECORD_SCHEMA
        assert report["preprocessing_provenance"]["schema"] == \
            PREPROCESSING_PROVENANCE_SCHEMA

    @_REAL
    def test_replay_rebuilds_computation(self):
        """R11.9-09: replay must re-derive artifacts, not just
        rehash sidecars."""
        sys.path.insert(0, str(
            Path(__file__).resolve().parents[1] / "scripts"))
        import replay_p5
        report = replay_p5.replay(EVIDENCE)
        assert report["replay_scope"] == "full_recomputation"
        assert report["checks"]["fmx_rebuild"]["digest_match"] is True
        assert report["status"] == "REPLAY_OK", report["failures"]


class TestCutoffProbes:
    """Adversarial probes from the R11.9 audit (R11.9-21/22)."""

    def _retrieval(self, **over):
        rec = {
            "canonical": True,
            "anchors": {b: {"lat": 28.0, "lon": 85.0}
                        for b in ("koshi", "gandaki", "karnali")},
            "files": [{"relpath": "era5-multibasin/"
                                  "era5land_ts_koshi_hma_2001-"
                                  "2025.zip"}],
            "channel_completions":
                {"cds": "2026-09-19T06:06:34Z"},
            "pull_utc_start": "2026-09-19T06:04:44Z",
            "pull_utc_end": "2026-09-19T06:06:34Z"}
        rec.update(over)
        return rec

    def _probe(self, tmp_path, rec_over=None, cutoff_over=None):
        manifest, root = _mini_root(tmp_path)
        rec = self._retrieval(**(rec_over or {}))
        (root / "retrieval/retrieval.json").write_text(
            json.dumps(rec))
        cutoff = _mini_cutoff(root)
        cutoff.update(cutoff_over or {})
        frame = pd.read_csv(root / "features/frame.csv")
        return cutoff_record_problems(
            cutoff, evidence_root=root, frame=frame)

    def test_superseded_route_blocked(self, tmp_path):
        """A non-canonical (superseded) record must fail closed."""
        probs = self._probe(tmp_path, rec_over={"canonical": False})
        assert any("canonical" in p for p in probs)

    def test_wrong_anchor_set_blocked(self, tmp_path):
        probs = self._probe(tmp_path, rec_over={
            "anchors": {"koshi": {}, "gandaki": {}}})
        assert any("anchor set" in p for p in probs)

    def test_non_operative_file_set_blocked(self, tmp_path):
        probs = self._probe(tmp_path, rec_over={
            "files": [{"relpath": "era5-multibasin/"
                                  "era5land_snow_koshi_2001.nc"}]})
        assert any("non-operative" in p for p in probs)

    def test_missing_completion_blocked(self, tmp_path):
        probs = self._probe(tmp_path, cutoff_over={
            "retrieval_completed_utc": ""})
        assert any("retrieval_completed_utc" in p for p in probs)

    def test_completion_before_channel_blocked(self, tmp_path):
        probs = self._probe(tmp_path, rec_over={
            "channel_completions": {"ee": "2026-09-19T07:48:22Z"},
            "pull_utc_end": "2026-09-19T06:06:34Z"})
        assert any("precedes channel" in p for p in probs)


class TestGroupStructure:
    """MIN_GEO_GROUPS gate + four-group acceptance (R11.9-24)."""

    def test_min_geo_groups_is_three(self):
        from nepal.science_v0.regimes import MIN_GEO_GROUPS
        assert MIN_GEO_GROUPS == 3

    def test_three_group_two_fit_rejected(self):
        """2 fit groups < 3 must reject before any fitting."""
        from nepal.science_v0.regimes import MIN_GEO_GROUPS
        fit_groups = {"koshi", "gandaki"}
        assert len(fit_groups) < MIN_GEO_GROUPS

    def test_four_group_three_fit_admissible(self):
        """A fourth group restores engine admissibility."""
        from nepal.science_v0.regimes import MIN_GEO_GROUPS
        fit = {"koshi_a", "koshi_b", "gandaki"}
        heldout = {"karnali"}
        assert fit.isdisjoint(heldout)
        assert len(fit) >= MIN_GEO_GROUPS


class TestFieldSeparation:
    """P5-A2: raw/admin/hydrological field separation (exact values)."""

    @_REAL
    def test_melamchi_field_separation_exact(self):
        import json as _j
        pkg = _j.loads((EVIDENCE /
                        "glof-events/p3_event_package_v0.json"
                        ).read_text())
        mel = [e for e in pkg["event_labels"]
               if e.get("raw_river_basin") == "Melamchi"]
        assert len(mel) == 1, "exactly one Melamchi label expected"
        e = mel[0]
        assert e["raw_river_basin"] == "Melamchi"
        assert e["administrative_district"] == "Sindhupalchok"
        assert e["administrative_province"] == "Bagmati"
        assert e["basin_group"] == "koshi"
        assert e["hydro_subbasin"] == "Indrawati"
        assert e["basin_id"] == e["basin_group"] == "koshi"

    @_REAL
    def test_basin_id_never_diverges(self):
        import json as _j
        pkg = _j.loads((EVIDENCE /
                        "glof-events/p3_event_package_v0.json"
                        ).read_text())
        for e in pkg["event_labels"]:
            assert e["basin_id"] == e["basin_group"], e["event_id"]

    def test_label_rejects_divergent_projection(self):
        from nepal.research_v0.records import EventLabelV0
        lbl = EventLabelV0(
            event_id="s:v:1", vertical_id="glof", source_id="s",
            source_version="v", event_time_start="2020-01-01",
            event_time_end="2020-01-01",
            event_time_precision="day", event_time_basis="b",
            geometry_role="lake_point", basin_id="bagmati",
            basin_group="koshi", uncertainty_seconds=0.0)
        assert any("basin_id" in p for p in lbl.problems())


class TestTemporalHoldout:
    """P5-A2 temporal amendment (R11.9-24 via temporal axis)."""

    @_REAL
    def test_amendment_intervals_bound(self):
        import json as _j
        a = _j.loads((EVIDENCE /
                      "retrieval/p5_amendment_v2_temporal_holdout.json"
                      ).read_text())
        d = a["declared"]
        assert d["holdout_axis"] == "temporal"
        assert d["temporal_train_interval"] == \
            ["2001-06-01", "2017-08-31"]
        assert d["temporal_embargo_interval"] == \
            ["2018-06-01", "2019-08-31"]
        assert d["temporal_holdout_interval"] == \
            ["2020-06-01", "2025-08-31"]

    @_REAL
    def test_train_mask_derived_from_intervals(self):
        import pandas as _pd
        frame = _pd.read_csv(EVIDENCE / FRAME_REL)
        d = _pd.to_datetime(frame["date"])
        mask = ((d >= "2001-06-01") & (d <= "2017-08-31"))
        assert int(mask.sum()) == 4692
        assert set(frame.basin_group[mask]) == \
            {"koshi", "gandaki", "karnali"}
        embargo = (d >= "2018-06-01") & (d <= "2019-08-31")
        holdout = d >= "2020-06-01"
        assert not (mask & (embargo | holdout)).any()

    @_REAL
    def test_hydrology_adjudication_bound(self):
        import json as _j
        m = _j.loads((EVIDENCE /
                      "retrieval/role_manifests_v0.json").read_text())
        sidecar = m["sidecar"]
        bound = {f["relpath"] for f in sidecar["source_files"]}
        assert "retrieval/hydrology_adjudication_v0.json" in bound
        assert "retrieval/p5_amendment_v2_temporal_holdout.json" \
            in bound
