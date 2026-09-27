"""SEASONAL-01 (P5 amendment v3): the seasonal-grain lane.

The seasonal estimand is a different question from daily P5-A2 —
75 basin-year rows under the locked six-feature contract.  These
tests pin the lane's gates: input-role rejection, covariance
vocabulary, the parameter-count guard, seasonal candidate limits,
the null-stratifier binding, the diagnostic-LORO predicate, frame
builder integrity, and — most importantly — that arbitrary
nonphysical structure is refused twice: by declaration AND by the
statistical gates themselves.
"""
import dataclasses
import re
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]
                     / "scripts"))

from nepal.science_v0.regimes import (RegimeRunConfig, run_regimes,
                                    _gmm_free_params,
                                    _component_covariances,
                                    COVARIANCE_TYPES)
from nepal.science_v0.seasonal_frame import (SEASONAL_FEATURES,
                                           build_seasonal_frame,
                                           verify_input_bytes,
                                           _wet_spell_max,
                                           JJA_DAYS)

EVIDENCE = Path("/Users/sanjayb/nepal-event-anomaly-evidence")
DAILY_ROOT = EVIDENCE / "p5-glof-2026-09-19"
DAILY_CSV = (DAILY_ROOT / "era5-multibasin/features/"
             "regime_frame_hma_jja_2001_2025.csv")
DAILY_RECEIPT = (DAILY_ROOT / "retrieval/"
                 "p5_glof_descriptive_receipt_v0.json")
LANE_ROOT = EVIDENCE / "p5-seasonal-v1-2026-09-20"
SEASONAL_CSV = (LANE_ROOT / "features/"
                "seasonal_frame_jja_2001_2025.csv")
ARTIFACT_JSON = LANE_ROOT / "run/seasonal_regime_artifact_v0.json"
RECEIPT_JSON = LANE_ROOT / "run/seasonal_lane_receipt_v0.json"


def _seasonal_frame(n_years=17, n_embargo=2, n_holdout=6):
    """Synthetic seasonal-grain frame: 3 basins x (17 train + 2
    embargo + 6 holdout) basin-year rows on a 365-day index grid."""
    rng = np.random.default_rng(7)
    rows = []
    anchor = pd.Timestamp("2001-07-16")
    years = (list(range(2001, 2001 + n_years))
             + list(range(2018, 2018 + n_embargo))
             + list(range(2020, 2020 + n_holdout)))
    for basin in ("gandaki", "karnali", "koshi"):
        offset = {"gandaki": 0.0, "karnali": -2.0, "koshi": 2.0}[basin]
        for y in years:
            rows.append({
                "unit_id": basin, "basin_group": basin,
                "season": "JJA", "season_year": y,
                "era": "pre_2013" if y < 2013 else "post_2013",
                "elevation_m": {"gandaki": 4972.1,
                                "karnali": 4800.0,
                                "koshi": 4300.0}[basin],
                "date": (anchor + pd.Timedelta(days=365)
                         * (y - 2001)).strftime("%Y-%m-%d"),
                "t2m_mean": offset + rng.normal(0, 0.3),
                "d2m_mean": offset * 0.8 + rng.normal(0, 0.3),
                "pdd_sum": 200 + offset * 40 + rng.normal(0, 15),
                "tp_q95": 12 + offset + rng.normal(0, 1),
                "wet_spell_max_days": 5 + rng.integers(0, 4),
                "sd_delta": -30 + offset * 5 + rng.normal(0, 4)})
    return pd.DataFrame(rows)


def _seasonal_cfg(**over):
    cfg = RegimeRunConfig(
        k_candidates=(1, 2, 3, 4),
        covariance_type="tied",
        frame_grain="seasonal",
        input_role="scientific",
        null_extra_strata_col="basin_group",
        loro_policy="diagnostic",
        cadence="365D",
        bootstrap_block_len=3,
        era_boundaries=("2013-01-01",),
        holdout_axis="temporal",
        train_groups=("gandaki", "karnali", "koshi"),
        heldout_groups=(),
        temporal_train_interval=("2001-06-01", "2017-08-31"),
        temporal_embargo_interval=("2018-06-01", "2019-08-31"),
        temporal_holdout_interval=("2020-06-01", "2025-08-31"),
        effort_waiver_reason="synthetic seasonal fixture",
        source_manifest={"fixture": True})
    return dataclasses.replace(cfg, **over)


def _mask(df):
    d = pd.to_datetime(df["date"]).dt.date
    import datetime as _dt
    return ((d >= _dt.date(2001, 6, 1))
            & (d <= _dt.date(2017, 8, 31))).to_numpy()


class TestSeasonalPreflight:
    """Every new lane control must fail closed on undeclared or
    over-powered declarations — before any fit access."""

    def test_full_covariance_rejected_on_seasonal_grain(self):
        df = _seasonal_frame()
        cfg = _seasonal_cfg(covariance_type="full")
        r = run_regimes(df, list(SEASONAL_FEATURES), _mask(df), cfg)
        assert r["status"] == "RUN_ERROR"
        assert "full" in r["reason"] and "seasonal" in r["reason"]

    def test_undeclared_covariance_rejected(self):
        df = _seasonal_frame()
        cfg = _seasonal_cfg(covariance_type="banded")
        r = run_regimes(df, list(SEASONAL_FEATURES), _mask(df), cfg)
        assert r["status"] == "RUN_ERROR"
        assert "covariance_type" in r["reason"]

    def test_k_above_4_rejected_on_seasonal_grain(self):
        df = _seasonal_frame()
        cfg = _seasonal_cfg(k_candidates=(1, 2, 3, 4, 5))
        r = run_regimes(df, list(SEASONAL_FEATURES), _mask(df), cfg)
        assert r["status"] == "RUN_ERROR"
        assert "K<=4" in r["reason"]

    def test_undeclared_grain_rejected(self):
        df = _seasonal_frame()
        cfg = _seasonal_cfg(frame_grain="weekly")
        r = run_regimes(df, list(SEASONAL_FEATURES), _mask(df), cfg)
        assert r["status"] == "RUN_ERROR"
        assert "frame_grain" in r["reason"]

    def test_undeclared_input_role_rejected(self):
        df = _seasonal_frame()
        cfg = _seasonal_cfg(input_role="operational")
        r = run_regimes(df, list(SEASONAL_FEATURES), _mask(df), cfg)
        assert r["status"] == "RUN_ERROR"
        assert "input_role" in r["reason"]

    def test_negative_control_role_refused_before_fit(self):
        """A declared nonphysical contract can never reach the
        model — the rejection fires in preflight."""
        df = _seasonal_frame()
        cfg = _seasonal_cfg(input_role="negative_control")
        r = run_regimes(df, list(SEASONAL_FEATURES), _mask(df), cfg)
        assert r["status"] == "RUN_ERROR"
        assert "negative-control" in r["reason"]
        assert "regime_artifact_digest" not in r or \
            not r.get("regime_artifact_digest")

    def test_parameter_guard_rejects_underpowered_sweep(self):
        """On the seasonal lane a declared K whose free-parameter
        count meets or exceeds the effective fit n fails closed —
        diag covariance at K=4 costs 51 params against 51 rows."""
        df = _seasonal_frame()
        cfg = _seasonal_cfg(covariance_type="diag")
        r = run_regimes(df, list(SEASONAL_FEATURES), _mask(df), cfg)
        assert r["status"] == "RUN_ERROR"
        assert "parameter-count guard" in r["reason"]

    def test_parameter_guard_is_seasonal_scoped(self):
        """The guard does not reach the daily lane — its established
        contract admits the full K<=5/full-covariance sweep."""
        df = _seasonal_frame()
        cfg = _seasonal_cfg(frame_grain="daily",
                            covariance_type="full",
                            k_candidates=(1, 2, 3, 4, 5),
                            loro_policy="required")
        r = run_regimes(df, list(SEASONAL_FEATURES), _mask(df), cfg)
        assert not (r["status"] == "RUN_ERROR" and
                    "parameter-count" in (r.get("reason") or ""))

    def test_free_params_formula(self):
        # d=6 tied: k*6 + (k-1) + 21 ; K=4 -> 48
        assert _gmm_free_params(4, 6, "tied") == 48
        # d=6 diag: k*6 + (k-1) + k*6 ; K=4 -> 51
        assert _gmm_free_params(4, 6, "diag") == 51
        # d=6 full: k*6 + (k-1) + k*21 ; K=2 -> 55
        assert _gmm_free_params(2, 6, "full") == 55
        # K=1 null is always admissible at n=51 (tied: 27, diag: 12)
        assert _gmm_free_params(1, 6, "tied") == 27
        assert _gmm_free_params(1, 6, "diag") == 12

    def test_missing_null_strata_col_rejected(self):
        df = _seasonal_frame()
        cfg = _seasonal_cfg(null_extra_strata_col="nonexistent")
        r = run_regimes(df, list(SEASONAL_FEATURES), _mask(df), cfg)
        assert r["status"] == "RUN_ERROR"
        assert "null_extra_strata_col" in r["reason"]

    def test_diagnostic_loro_illegal_on_daily_grain(self):
        df = _seasonal_frame()
        cfg = _seasonal_cfg(frame_grain="daily",
                            covariance_type="diag",
                            loro_policy="diagnostic")
        r = run_regimes(df, list(SEASONAL_FEATURES), _mask(df), cfg)
        assert r["status"] == "RUN_ERROR"
        assert "loro_policy" in r["reason"]

    def test_diagnostic_loro_illegal_on_geographic_axis(self):
        df = _seasonal_frame()
        cfg = _seasonal_cfg(holdout_axis="geographic",
                            heldout_groups=("koshi",),
                            temporal_train_interval=(),
                            temporal_embargo_interval=(),
                            temporal_holdout_interval=())
        r = run_regimes(df, list(SEASONAL_FEATURES), _mask(df), cfg)
        assert r["status"] == "RUN_ERROR"
        assert "loro_policy" in r["reason"]


class TestSeasonalNegativeControlStatistics:
    """The declaration-level refusal is necessary but not sufficient:
    a nonphysical frame run under the scientific role must ALSO fail
    the statistical gates — the harness must reject arbitrary
    structure, not merely arbitrary declarations."""

    def test_shuffled_feature_frame_never_descriptive(self):
        df = _seasonal_frame()
        rng = np.random.default_rng(20260920)
        feats = df[list(SEASONAL_FEATURES)].to_numpy()
        df.loc[:, list(SEASONAL_FEATURES)] = feats[
            rng.permutation(len(feats))]
        r = run_regimes(df, list(SEASONAL_FEATURES), _mask(df),
                        _seasonal_cfg())
        assert r["status"] in ("UNSUPERVISED_STRUCTURE_NOT_STABLE",
                               "CANDIDATE_ONLY", "RUN_ERROR"), \
            f"harness produced {r['status']} on a shuffled frame"

    def test_pure_noise_frame_never_descriptive(self):
        df = _seasonal_frame()
        rng = np.random.default_rng(99)
        df[list(SEASONAL_FEATURES)] = df[
            list(SEASONAL_FEATURES)].astype(np.float64)
        df.loc[:, list(SEASONAL_FEATURES)] = rng.normal(
            size=(len(df), len(SEASONAL_FEATURES)))
        r = run_regimes(df, list(SEASONAL_FEATURES), _mask(df),
                        _seasonal_cfg())
        assert r["status"] != "DESCRIPTIVE_REGIME_ONLY", \
            "pure noise produced a descriptive regime — harness failure"


class TestSeasonalConfigFloor:
    """The serialized-config floor (producer_validation) and the
    semantic revalidation must both know the lane's new fields."""

    def test_field_set_exact(self):
        d = dataclasses.asdict(_seasonal_cfg())
        d.pop("forecast_vintages", None)
        from nepal.research_v0.producer_validation import (
            _CONFIG_FIELDS)
        assert set(d) == set(_CONFIG_FIELDS)
        for f in ("covariance_type", "frame_grain", "input_role",
                  "null_extra_strata_col", "loro_policy"):
            assert f in d

    def test_serialized_floor_rejects_negative_control(self):
        from nepal.research_v0.producer_validation import (
            _config_semantic_problems)
        cfg = dataclasses.asdict(
            _seasonal_cfg(input_role="negative_control"))
        cfg.pop("forecast_vintages", None)
        problems = _config_semantic_problems({"config": cfg})
        assert any("negative_control" in p for p in problems)

    def test_serialized_floor_rejects_seasonal_full(self):
        from nepal.research_v0.producer_validation import (
            _config_semantic_problems)
        cfg = dataclasses.asdict(
            _seasonal_cfg(covariance_type="full"))
        cfg.pop("forecast_vintages", None)
        problems = _config_semantic_problems({"config": cfg})
        assert any("covariance" in p for p in problems)

    def test_semantic_revalidation_rejects_negative_control(self):
        from nepal.science_v0.regimes import _validate_config_semantics
        cfg = dataclasses.asdict(
            _seasonal_cfg(input_role="negative_control"))
        cfg.pop("forecast_vintages", None)
        problems = _validate_config_semantics(cfg)
        assert any("negative_control" in p for p in problems)

    def test_semantic_revalidation_rejects_seasonal_k5(self):
        from nepal.science_v0.regimes import _validate_config_semantics
        cfg = dataclasses.asdict(
            _seasonal_cfg(k_candidates=(1, 2, 3, 4, 5)))
        cfg.pop("forecast_vintages", None)
        problems = _validate_config_semantics(cfg)
        assert any("K<=4" in p or "seasonal" in p
                   for p in problems)


class TestSeasonalFrameBuilder:
    """The adapter is part of the evidence chain: unbound input
    bytes, non-constant carriers, and malformed basin-years must all
    fail closed or be ledgered."""

    def test_unbound_input_refused(self, tmp_path):
        csv = tmp_path / "unbound.csv"
        csv.write_text("a,b\n1,2\n")
        with pytest.raises(ValueError, match="sidecar"):
            verify_input_bytes(csv)

    def test_substituted_input_refused(self, tmp_path):
        csv = tmp_path / "fake.csv"
        csv.write_text("a,b\n1,2\n")
        Path(str(csv) + ".sha256").write_text("0" * 64 + "  fake\n")
        with pytest.raises(ValueError, match="digest"):
            verify_input_bytes(csv)

    def test_wet_spell_counter(self):
        tp = np.array([0.5, 1.0, 2.0, 0.2, 3.0, 4.0, 5.0])
        assert _wet_spell_max(tp) == 3

    def test_builder_drops_short_season(self, tmp_path):
        rng = np.random.default_rng(1)
        rows = []
        for basin in ("gandaki", "karnali", "koshi"):
            for y in range(2001, 2003):
                days = pd.date_range(f"{y}-06-01", f"{y}-08-31")
                # honest missingness: one basin-year is truncated by
                # 5 trailing days — a ledger drop, not contamination
                if basin == "koshi" and y == 2002:
                    days = days[:-5]
                for d in days:
                    rows.append({
                        "date": d.strftime("%Y-%m-%d"),
                        "t2m_daily": rng.normal(2, 1),
                        "d2m_daily": rng.normal(0, 1),
                        "pdd_daily": abs(rng.normal(3, 1)),
                        "tp_daily": abs(rng.normal(1, 1)),
                        "sd_daily": 100 + rng.normal(0, 5),
                        "unit_id": basin, "basin_group": basin,
                        "season": "JJA",
                        "era": "pre_2013", "elevation_m": 4500.0})
        df = pd.DataFrame(rows)
        csv = tmp_path / "daily.csv"
        df.to_csv(csv, index=False)
        sha = hashlib.sha256(csv.read_bytes()).hexdigest()
        Path(str(csv) + ".sha256").write_text(f"{sha}  daily.csv\n")
        out = tmp_path / "out"
        build = build_seasonal_frame(csv, out)
        frame = build["frame"]
        assert len(frame) == 5  # 3 basins*2y - 1 short season
        assert len(build["ledger"]["dropped_basin_years"]) == 1
        assert set(frame.columns) >= set(SEASONAL_FEATURES)

    def test_real_seasonal_frame_shape(self):
        if not SEASONAL_CSV.exists():
            pytest.skip("seasonal evidence root absent")
        df = pd.read_csv(SEASONAL_CSV)
        assert len(df) == 75
        assert sorted(df["basin_group"].unique()) == \
            ["gandaki", "karnali", "koshi"]
        assert df["season_year"].nunique() == 25
        assert list(SEASONAL_FEATURES) == \
            [c for c in df.columns if c in SEASONAL_FEATURES]
        # declared split: 51 train / 6 embargo / 18 holdout
        tr = df[df["season_year"] <= 2017]
        em = df[(df["season_year"] >= 2018)
                & (df["season_year"] <= 2019)]
        ho = df[df["season_year"] >= 2020]
        assert (len(tr), len(em), len(ho)) == (51, 6, 18)
        # no feature NaN under the listwise policy
        assert not df[list(SEASONAL_FEATURES)].isna().any().any()
        # sidecar binds the exact bytes
        declared = Path(str(SEASONAL_CSV) + ".sha256") \
            .read_text().split()[0]
        assert hashlib.sha256(SEASONAL_CSV.read_bytes()) \
            .hexdigest() == declared


class TestSeasonalArtifactIntegrity:
    """The bound seasonal artifact, when present, must carry the
    lane's declared surface and honest terminal status."""

    def test_artifact_declares_seasonal_contract(self):
        if not ARTIFACT_JSON.exists():
            pytest.skip("seasonal artifact absent — run "
                        "scripts/run_seasonal_p5.py first")
        a = json.loads(ARTIFACT_JSON.read_text())
        cfg = a["config"]
        assert cfg["frame_grain"] == "seasonal"
        assert cfg["covariance_type"] == "tied"
        assert cfg["input_role"] == "scientific"
        assert cfg["null_extra_strata_col"] == "basin_group"
        assert cfg["loro_policy"] == "diagnostic"
        assert cfg["holdout_axis"] == "temporal"
        assert cfg["label_blinding"] is True
        assert cfg["fitted_on"] == "TRAIN_ONLY"
        assert a["feature_cols"] == list(SEASONAL_FEATURES)
        assert a["status"] in ("DESCRIPTIVE_REGIME_ONLY",
                               "CANDIDATE_ONLY",
                               "UNSUPERVISED_STRUCTURE_NOT_STABLE",
                               "UNDERPOWERED_DESCRIPTIVE_ONLY")

    def test_artifact_authority_flags_all_false(self):
        if not ARTIFACT_JSON.exists():
            pytest.skip("seasonal artifact absent")
        a = json.loads(ARTIFACT_JSON.read_text())
        assert a.get("data_class") == "REANALYSIS"
        assert a.get("mode") == "RETROSPECTIVE_REGIME"
        # no forecast/operational vocabulary may attach
        assert not a.get("forecast_vintage_digests")
        assert not a.get("forecast_feature_set")

    def test_holdout_rows_never_in_fit(self):
        if not ARTIFACT_JSON.exists():
            pytest.skip("seasonal artifact absent")
        a = json.loads(ARTIFACT_JSON.read_text())
        fp = a["fit_partition"]
        assert fp["n_train_rows"] == 51
        assert fp["n_rows"] == 75
        # fit cutoff is inside the declared train interval
        assert fp["cutoff_iso"] <= "2017-08-31"

    def test_receipt_binds_all_arms(self):
        if not RECEIPT_JSON.exists():
            pytest.skip("seasonal receipt absent")
        r = json.loads(RECEIPT_JSON.read_text())
        assert set(r["arms"]) == {"A_reference", "B_surface_core",
                                  "NC_negative_control"}
        nc = r["arms"]["NC_negative_control"]
        assert nc["rejected_before_fit"] is True
        assert nc["engine_status"] == "RUN_ERROR"
        assert r["claim_scope"] == \
            "research_only_no_operational_authorization"

    def test_daily_result_untouched(self):
        """Arm A binds the daily receipt by reference — the daily
        frame and receipt bytes must be unchanged."""
        if not DAILY_CSV.exists():
            pytest.skip("daily evidence root absent")
        sidecar = Path(str(DAILY_CSV) + ".sha256")
        declared = sidecar.read_text().split()[0]
        assert hashlib.sha256(DAILY_CSV.read_bytes()).hexdigest() \
            == declared


class TestSeasonalV1Semantics:
    """SEASONAL-02 — the audit-driven semantic corrections: gate
    observations distinguish PASS from NOT_APPLICABLE/SKIPPED, the
    diagnostic LORO can never read as geographic validation, the
    Arm A reference chain is verified, and every authority field is
    explicitly false."""

    def test_gate_observations_cover_gate_universe(self):
        if not ARTIFACT_JSON.exists():
            pytest.skip("seasonal artifact absent")
        a = json.loads(ARTIFACT_JSON.read_text())
        stab = a["stability"]
        gates = stab["required_gates"]
        obs = stab["gate_observations"]
        assert set(obs) == set(gates)
        vocab = {"PASS", "FAIL", "SKIPPED", "NOT_APPLICABLE",
                 "NONCONVERGED"}
        for g, o in obs.items():
            assert o["observed_status"] in vocab
            assert isinstance(o["binding"], bool)

    def test_loro_never_reads_as_geographic_validation(self):
        if not ARTIFACT_JSON.exists():
            pytest.skip("seasonal artifact absent")
        a = json.loads(ARTIFACT_JSON.read_text())
        assert a["config"]["loro_policy"] == "diagnostic"
        obs = a["stability"]["gate_observations"]["loro"]
        assert obs["binding"] is False
        assert obs["observed_status"] == "SKIPPED"
        # the binding bool and the observation disagree by design —
        # a consumer reading required_gates alone would see True and
        # wrongly infer an executed LORO pass
        assert a["stability"]["required_gates"]["loro"] is True
        folds = a["stability"]["leave_one_region_out"]["folds"]
        assert all(f["status"] == "SKIPPED" for f in folds.values())

    def test_not_applicable_axes_are_not_passes(self):
        if not ARTIFACT_JSON.exists():
            pytest.skip("seasonal artifact absent")
        obs = json.loads(ARTIFACT_JSON.read_text()) \
            ["stability"]["gate_observations"]
        # the single-season axis and the waived effort axis must
        # record NOT_APPLICABLE — not a silent executed pass
        assert obs["season_refits"]["observed_status"] == \
            "NOT_APPLICABLE"
        assert obs["effort"]["observed_status"] == "NOT_APPLICABLE"

    def test_receipt_authority_all_false_explicit(self):
        if not RECEIPT_JSON.exists():
            pytest.skip("seasonal receipt absent")
        r = json.loads(RECEIPT_JSON.read_text())
        auth = r["authority"]
        for k in ("promotion_eligible", "production_authorized",
                  "warning_path_authorized", "operational_claim"):
            assert auth[k] is False, f"{k} is not explicitly false"

    def test_receipt_negative_status_carries_reason_and_gates(self):
        if not RECEIPT_JSON.exists():
            pytest.skip("seasonal receipt absent")
        r = json.loads(RECEIPT_JSON.read_text())
        b = r["arms"]["B_surface_core"]
        if b["status"] in ("UNSUPERVISED_STRUCTURE_NOT_STABLE",
                           "CANDIDATE_ONLY"):
            assert b["failed_gates"], "negative status without a " \
                "failed-gate summary"
            assert b["terminal_reason"], "negative status without " \
                "a terminal reason"
            # receipt gates agree with the artifact's bound map
            if ARTIFACT_JSON.exists():
                ag = json.loads(ARTIFACT_JSON.read_text()) \
                    ["stability"]["required_gates"]
                assert sorted(b["failed_gates"]) == sorted(
                    g for g, v in ag.items() if v is not True)

    def test_arm_a_verification_present_and_passed(self):
        if not RECEIPT_JSON.exists():
            pytest.skip("seasonal receipt absent")
        v = json.loads(RECEIPT_JSON.read_text()) \
            ["arms"]["A_reference"]["verification"]
        assert v["verified"] is True
        assert v["receipt_binds_artifact"] is True
        assert v["artifact_envelope_digest_match"] is True
        assert v["artifact_freeze_digest_ok"] is True
        assert v["authority_flags_all_false"] is True
        assert v["status_terminal"] is True


DAILY_ARTIFACT_V1 = EVIDENCE / "p5-glof-2026-09-19" / "retrieval" / \
    "p5_glof_regime_artifact_v1.json"
DAILY_RECEIPT_V1 = EVIDENCE / "p5-glof-2026-09-19" / "retrieval" / \
    "p5_glof_descriptive_receipt_v1.json"


def _fake_daily_root(tmp_path, receipt=None, artifact=None):
    """Materialize a synthetic daily root holding the two governed
    Arm A files under retrieval/."""
    ret = tmp_path / "retrieval"
    ret.mkdir(parents=True)
    if receipt is not None:
        (ret / "p5_glof_descriptive_receipt_v1.json").write_text(
            json.dumps(receipt))
    if artifact is not None:
        (ret / "p5_glof_regime_artifact_v1.json").write_text(
            json.dumps(artifact))
    return tmp_path


class TestDailyReferenceVerification:
    """The Arm A reference chain must reject stale, mutated, or
    receipt-only daily evidence — a bound digest without artifact
    bytes is not verification (R-05)."""

    def test_verify_daily_reference_accepts_real_chain(self):
        if not DAILY_RECEIPT_V1.exists() or \
                not DAILY_ARTIFACT_V1.exists():
            pytest.skip("daily v1 artifact/receipt absent")
        import run_seasonal_p5 as drv
        v = drv.verify_daily_reference(
            DAILY_RECEIPT_V1.parents[1])
        assert v["verified"] is True
        assert v["problems"] == []
        assert v["artifact_envelope_digest_match"] is True
        assert v["artifact_freeze_digest_ok"] is True
        assert v["receipt_binds_artifact"] is True

    def test_missing_artifact_fails_closed(self, tmp_path):
        """A receipt whose artifact bytes are absent is NOT a
        verified reference — the digest alone cannot stand in."""
        import run_seasonal_p5 as drv
        root = _fake_daily_root(tmp_path, receipt={
            "regime_artifact_digest": "a" * 64,
            "status": "CANDIDATE_ONLY",
            "promotion_eligible": False,
            "production_authorized": False,
            "warning_path_authorized": False,
            "claim_scope":
                "research_only_no_operational_authorization"})
        v = drv.verify_daily_reference(root)
        assert v["verified"] is False
        assert v["artifact_exists"] is False
        assert any("artifact" in p and "bytes" in p
                   for p in v["problems"])

    def test_receipt_digest_disagreement_fails_closed(
            self, tmp_path):
        """Receipt binds a digest the artifact does not carry."""
        import run_seasonal_p5 as drv
        root = _fake_daily_root(
            tmp_path,
            receipt={
                "regime_artifact_digest": "b" * 64,
                "status": "CANDIDATE_ONLY",
                "promotion_eligible": False,
                "production_authorized": False,
                "warning_path_authorized": False,
                "claim_scope":
                    "research_only_no_operational_authorization"},
            artifact={"regime_artifact_digest": "a" * 64,
                      "status": "UNSUPERVISED_STRUCTURE_NOT_STABLE",
                      "frozen": True, "freeze_digest": "c" * 64})
        v = drv.verify_daily_reference(root)
        assert v["verified"] is False
        assert v["receipt_binds_artifact"] is False

    def test_verify_daily_reference_rejects_true_authority(
            self, tmp_path):
        import run_seasonal_p5 as drv
        root = _fake_daily_root(tmp_path, receipt={
            "regime_artifact_digest": "a" * 64,
            "status": "CANDIDATE_ONLY",
            "promotion_eligible": True,
            "production_authorized": False,
            "warning_path_authorized": False,
            "claim_scope":
                "research_only_no_operational_authorization"})
        v = drv.verify_daily_reference(root)
        assert v["verified"] is False
        assert any("authority" in p for p in v["problems"])

    def test_verify_daily_reference_rejects_missing(self, tmp_path):
        import run_seasonal_p5 as drv
        v = drv.verify_daily_reference(tmp_path / "absent_root")
        assert v["verified"] is False
        assert v["problems"]


class TestGateObservationFloor:
    """The producer floor rejects malformed gate_observations — a
    tri-state or misnamed observation cannot launder semantics."""

    def _payload(self, obs):
        from nepal.research_v0.producer_validation import _gate_problems
        stab = {"required_gates":
                {g: True for g in
                 ("seed_policy", "modal_k_unanimous", "seed_ari",
                  "seed_coverage", "loro", "temporal_bootstrap",
                  "season_refits", "elevation", "missingness",
                  "effort", "era_drift", "shuffled_null",
                  "season_matched_null")},
                "gate_observations": obs}
        return _gate_problems({"stability": stab})

    def test_wellformed_observations_accepted(self):
        obs = {g: {"observed_status": "PASS", "binding": True,
                   "reason": None}
               for g in ("seed_policy", "modal_k_unanimous",
                         "seed_ari", "seed_coverage", "loro",
                         "temporal_bootstrap", "season_refits",
                         "elevation", "missingness", "effort",
                         "era_drift", "shuffled_null",
                         "season_matched_null")}
        assert not any("gate_observations" in p
                       for p in self._payload(obs))

    def test_undeclared_gate_in_observations_rejected(self):
        obs = {"bogus_gate": {"observed_status": "PASS",
                              "binding": True}}
        assert any("undeclared gate" in p
                   for p in self._payload(obs))

    def test_malformed_observation_rejected(self):
        obs = {"loro": {"observed_status": "TRISTATE",
                        "binding": "yes"}}
        assert any("gate_observations[loro]" in p
                   for p in self._payload(obs))


class TestClaimVocabularyCeiling:
    """The lane's evidence must never carry operational, warning,
    forecast, or geographic-transfer claim language — the claim-scan
    floor is enforced at test level, not only at release."""

    _FORBIDDEN = re.compile(
        r"\b(forecast(?!_vintage|_feature_set)|early[-_ ]?warning|"
        r"evacuat|bulletin|siren|public[-_ ]?safety|"
        r"operationali[sz]e|production[-_ ]?deploy)\b",
        re.IGNORECASE)

    def _walk_strings(self, obj):
        if isinstance(obj, str):
            yield obj
        elif isinstance(obj, dict):
            for v in obj.values():
                yield from self._walk_strings(v)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                yield from self._walk_strings(v)

    def test_receipt_carries_no_forbidden_claim_language(self):
        if not RECEIPT_JSON.exists():
            pytest.skip("seasonal receipt absent")
        r = json.loads(RECEIPT_JSON.read_text())
        hits = [s for s in self._walk_strings(r)
                if self._FORBIDDEN.search(s)]
        assert hits == [], f"forbidden claim language: {hits[:3]}"

    def test_artifact_carries_no_forbidden_claim_language(self):
        if not ARTIFACT_JSON.exists():
            pytest.skip("seasonal artifact absent")
        a = json.loads(ARTIFACT_JSON.read_text())
        hits = [s for s in self._walk_strings(a)
                if self._FORBIDDEN.search(s)]
        assert hits == [], f"forbidden claim language: {hits[:3]}"
