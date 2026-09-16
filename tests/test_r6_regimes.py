"""Round-6 adversarial probes — one behavioral test per closed
residual gate (PROV-01, REG-02, REG-03, REG-04, REG-05a, FCST-01,
PROV-03).  Synthetic fixture scope only."""
import copy as _copy
import hashlib
import re
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

import nepal.science_v0.regimes as regimes_mod
from nepal.research_v0._hashing import (
    sha256_canonical, sha256_file, verify_source_evidence)
from nepal.research_v0.gates import REQUIRED_REGIME_GATE_NAMES
from nepal.science_v0.regimes import (
    RegimeRunConfig, _digest, _iso_date_ok, _null_envelope,
    freeze_regime_artifact, run_regimes)

FEATURES = ["f1", "f2", "f3"]


def _frame(n_per=120, groups=("grp0", "grp1", "grp2", "grp3"),
           day_step=1, seed=0, structured=False):
    """Daily-grid synthetic frame, one unit per group."""
    rng = np.random.default_rng(seed)
    centers = [(0, 0, 0), (5, 5, 0), (0, 5, 5)] if structured \
        else [(0, 0, 0)]
    rows = []
    for gi, g in enumerate(groups):
        for i in range(n_per):
            c = centers[i % len(centers)]
            rows.append({
                "unit_id": f"cell{gi}",
                "date": str(date(2020, 6, 1) + timedelta(days=i * day_step)),
                "basin_group": g,
                "season": "JJA" if i % 2 == 0 else "DJF",
                "era": "e1" if i < n_per // 2 else "e2",
                "f1": c[0] + rng.normal(0, 0.5),
                "f2": c[1] + rng.normal(0, 0.5),
                "f3": c[2] + rng.normal(0, 0.5)})
    return pd.DataFrame(rows)


def _cfg(**kw):
    kw.setdefault("source_manifest", {"fixture": True})
    kw.setdefault("bootstrap_block_len", 12)
    kw.setdefault("effort_waiver_reason",
                  "synthetic frame carries no effort column")
    kw.setdefault("era_waiver_reason", "single-era fixture")
    return RegimeRunConfig(
        train_groups=("grp0", "grp1", "grp2"),
        heldout_groups=("grp3",), **kw)


def _mask(df):
    return (df["basin_group"] != "grp3").to_numpy()


def _redigest(art):
    """Recompute the artifact digest after a mutation so that ONLY
    the named audit surface is exercised."""
    art["regime_artifact_digest"] = _digest(
        {k: v for k, v in art.items()
         if k != "regime_artifact_digest"})


_ART_CACHE = {}


def _art_cached(key, builder):
    if key not in _ART_CACHE:
        _ART_CACHE[key] = builder()
    return _copy.deepcopy(_ART_CACHE[key])


def _art_std():
    df = _frame()
    return _art_cached(
        "std", lambda: run_regimes(df, FEATURES, _mask(df), _cfg()))


def _art_forecast():
    df = _frame()
    cfg = _cfg(mode="FORECAST_REGIME",
               forecast_vintage_digests=("a" * 64, "b" * 64),
               forecast_feature_set=("f1", "f2"))
    return _art_cached(
        "fcst", lambda: run_regimes(df, FEATURES, _mask(df), cfg))


def _art_struct():
    """Run on the clustered fixture so modal_k >= 2 and the null
    envelope actually executes its replicates."""
    df = _frame(structured=True)
    return _art_cached(
        "struct", lambda: run_regimes(df, FEATURES, _mask(df),
                                      _cfg()))


# ---------------------------------------------------------------------
# PROV-01: freeze requires real booleans in required_gates
# ---------------------------------------------------------------------

class TestProv01StrictBoolGates:
    def _minimal_artifact(self, gates):
        assignments = [["u1", "2020-01-01", 0],
                       ["u1", "2020-01-02", 1]]
        vals = [[1.0, 2.0], [3.0, 4.0]]
        raw = np.asarray(vals, dtype=np.float64)
        art = {
            "status": "DESCRIPTIVE_REGIME_ONLY",
            "mode": "RETROSPECTIVE_REGIME",
            "data_class": "REANALYSIS",
            "forecast_vintage_digests": [],
            "forecast_feature_set": [],
            "assignments": assignments,
            "config": {"seeds": [1, 2, 3]},
            "input_values": vals,
            "input_schema": {"shape": [2, 2]},
            "preprocessing": {"row_keys_digest": "a" * 64},
            "stability": {"required_gates": gates},
        }
        art["assignment_digest"] = sha256_canonical(assignments)
        art["config_digest"] = sha256_canonical(art["config"])
        art["input_bytes_digest"] = hashlib.sha256(
            raw.tobytes()).hexdigest()
        art["preprocessing_digest"] = sha256_canonical(
            art["preprocessing"])
        art["regime_artifact_digest"] = sha256_canonical(
            {k: v for k, v in art.items()
             if k != "regime_artifact_digest"})
        return art

    def test_string_true_gate_rejected(self):
        gates = {g: True for g in REQUIRED_REGIME_GATE_NAMES}
        gates["loro"] = "true"
        art = self._minimal_artifact(gates)
        with pytest.raises(ValueError, match="bool"):
            freeze_regime_artifact(art)

    def test_truthy_pass_string_cannot_certify(self):
        gates = {g: True for g in REQUIRED_REGIME_GATE_NAMES}
        gates["seed_ari"] = "PASS"
        art = self._minimal_artifact(gates)
        with pytest.raises(ValueError, match="bool"):
            freeze_regime_artifact(art)

    def test_truthy_int_gate_rejected(self):
        gates = {g: True for g in REQUIRED_REGIME_GATE_NAMES}
        gates["effort"] = 1
        art = self._minimal_artifact(gates)
        with pytest.raises(ValueError, match="bool"):
            freeze_regime_artifact(art)

    def test_real_bool_gates_still_freeze(self):
        art = self._minimal_artifact(
            {g: True for g in REQUIRED_REGIME_GATE_NAMES})
        frozen = freeze_regime_artifact(art)
        assert frozen["frozen"] is True


# ---------------------------------------------------------------------
# REG-02: calendar-valid dates (strptime-backed _iso_date_ok)
# ---------------------------------------------------------------------

class TestReg02CalendarDates:
    def test_iso_date_ok_is_calendar_aware(self):
        assert _iso_date_ok("2020-06-01") is True
        assert _iso_date_ok("2020-02-29") is True   # leap day
        assert _iso_date_ok("2020-13-99") is False
        assert _iso_date_ok("2020-02-30") is False
        assert _iso_date_ok("2021-02-29") is False  # non-leap year
        assert _iso_date_ok("2020-1-5") is False    # non-canonical
        assert _iso_date_ok("not-a-date") is False

    def test_era_boundary_noncalendar_mix_rejected(self):
        df = _frame()
        cfg = _cfg(era_boundaries=("2020-06-01", "2020-13-99"))
        art = run_regimes(df, FEATURES, _mask(df), cfg)
        assert art["status"] == "RUN_ERROR"
        assert "era_boundaries" in art["reason"]

    def test_frame_row_noncalendar_date_rejected(self):
        df = _frame()
        df.loc[df.index[0], "date"] = "2020-13-99"
        art = run_regimes(df, FEATURES, _mask(df), _cfg())
        assert art["status"] == "RUN_ERROR"
        assert "date" in art["reason"].lower()


# ---------------------------------------------------------------------
# REG-03: every stability refit is bound to the policy-selected
# fit surface — policy-dropped rows can never re-enter
# ---------------------------------------------------------------------

class TestReg03FitSurfaceIntersection:
    def _frame_with_drops(self):
        """Frame with a categorical effort column and ~20 train rows
        carrying NaN f1 — listwise policy drops them."""
        df = _frame()
        df["effort"] = ["low" if i % 2 == 0 else "high"
                        for i in range(len(df))]
        train_idx = df.index[df["basin_group"] != "grp3"]
        drop_idx = train_idx[::7][:20]
        df.loc[drop_idx, "f1"] = np.nan
        return df, set(drop_idx.tolist())

    def test_effort_bands_exclude_dropped_rows(self):
        df, dropped = self._frame_with_drops()
        cfg = _cfg(effort_col="effort",
                   missingness_policy="listwise")
        art = run_regimes(df, FEATURES, _mask(df), cfg)
        assert art["status"] != "RUN_ERROR"
        applied = art["missingness_applied"]
        assert applied["train_rows_dropped"] == len(dropped)
        bands = art["stability"]["effort_sensitivity"]["bands"]
        complete = df.loc[df["basin_group"] != "grp3"].dropna(
            subset=["f1"])
        for bname, brec in bands.items():
            assert brec["n_rows"] == int(
                (complete["effort"] == bname).sum()), bname

    def test_no_refit_touches_dropped_rows(self, monkeypatch):
        """Spy on _refit_against_reference: no season / missingness /
        effort refit may receive a policy-dropped row."""
        df, dropped = self._frame_with_drops()
        captured = []
        orig = regimes_mod._refit_against_reference

        def spy(sub, *a, **k):
            captured.append(set(sub.index.tolist()))
            return orig(sub, *a, **k)

        monkeypatch.setattr(regimes_mod, "_refit_against_reference",
                            spy)
        cfg = _cfg(effort_col="effort",
                   missingness_policy="listwise")
        art = run_regimes(df, FEATURES, _mask(df), cfg)
        assert art["status"] != "RUN_ERROR"
        assert captured
        for i, idxs in enumerate(captured):
            assert not (idxs & dropped), \
                f"refit {i} touched dropped rows {idxs & dropped}"

    def test_missingness_axis_counts_match_applied(self):
        df, dropped = self._frame_with_drops()
        cfg = _cfg(effort_col="effort",
                   missingness_policy="listwise")
        art = run_regimes(df, FEATURES, _mask(df), cfg)
        assert art["status"] != "RUN_ERROR"
        miss_ax = art["stability"]["missingness_sensitivity"]
        assert miss_ax["complete_rows"] == \
            art["missingness_applied"]["train_rows_fitted"]

    def test_era_axis_uses_fit_surface(self):
        """Declared era labels + dropped rows: the era drift axis must
        compute on the selected surface (previously a boolean-mask
        length mismatch crashed the run)."""
        df, dropped = self._frame_with_drops()
        cfg = _cfg(era_boundaries=("e1", "e2"),
                   missingness_policy="listwise")
        art = run_regimes(df, FEATURES, _mask(df), cfg)
        assert isinstance(art, dict)
        assert art["status"] != "RUN_ERROR"
        assert art["stability"]["era_drift"]["status"] in (
            "PASS", "FAIL")


# ---------------------------------------------------------------------
# REG-04: 'first10' on a numeric effort column fails closed
# ---------------------------------------------------------------------

class TestReg04First10Effort:
    def test_first10_on_numeric_effort_fails_closed(self):
        df = _frame()
        df["obs_hours"] = np.linspace(1.0, 10.0, len(df))
        cfg = _cfg(effort_col="obs_hours", effort_split="first10")
        art = run_regimes(df, FEATURES, _mask(df), cfg)
        assert art["status"] == "RUN_ERROR"
        assert "first10" in art["reason"]

    def test_first10_on_categorical_effort_executes(self):
        df = _frame()
        df["effort_class"] = ["low" if i % 2 == 0 else "high"
                              for i in range(len(df))]
        cfg = _cfg(effort_col="effort_class", effort_split="first10")
        art = run_regimes(df, FEATURES, _mask(df), cfg)
        assert art["status"] != "RUN_ERROR"
        assert art["stability"]["effort_sensitivity"][
            "strata_policy"]["kind"] == "categorical"

    def test_median_on_numeric_effort_executes(self):
        df = _frame()
        df["obs_hours"] = np.linspace(1.0, 10.0, len(df))
        cfg = _cfg(effort_col="obs_hours", effort_split="median")
        art = run_regimes(df, FEATURES, _mask(df), cfg)
        assert art["status"] != "RUN_ERROR"
        assert art["stability"]["effort_sensitivity"][
            "strata_policy"]["kind"] == "numeric_median"


# ---------------------------------------------------------------------
# REG-05a: per-replicate generated-input digest inside family_digest
# ---------------------------------------------------------------------

class TestReg05aNullInputDigest:
    def test_replicates_bind_generated_input(self):
        def gen(i, seed):
            r = np.random.default_rng(seed)
            return r.normal(0, 1, size=(60, 3))

        rec = _null_envelope(0.9, gen, 2, [1, 2, 3],
                             n_replicates=5, alpha=0.05)
        reps = rec["replicates"]
        assert len(reps) == 5
        for rep in reps:
            assert "input_digest" in rep
            X_n = gen(rep["i"], rep["gen_seed"])
            expected = hashlib.sha256(
                np.ascontiguousarray(
                    X_n, dtype=np.float64).tobytes()).hexdigest()
            assert rep["input_digest"] == expected

    def test_artifact_replicates_carry_64hex_input_digest(self):
        art = _art_struct()
        assert art["nulls"]["observed"] is not None
        for fam in ("shuffled", "season_matched"):
            reps = art["nulls"][fam].get("replicates") or []
            assert reps, fam
            for rep in reps:
                assert "input_digest" in rep
                if rep["ok"]:
                    assert re.fullmatch(r"[0-9a-f]{64}",
                                        rep["input_digest"])

    def test_family_digest_binds_replicate_inputs(self):
        """The recorded family_digest must recompute over material
        that includes per-replicate input_digest — mutating one
        changes the digest."""
        art = _art_struct()
        nul = art["nulls"]["shuffled"]
        material = {"family": "shuffled",
                    "seed_cycle": art["seeds"],
                    "n_replicates": nul["n_replicates"],
                    "statistic": nul["statistic"],
                    "p_value": nul["p_value"],
                    "null_stat_min": nul.get("null_stat_min"),
                    "null_stat_max": nul.get("null_stat_max"),
                    "null_k_distribution": nul.get(
                        "null_k_distribution", {}),
                    "replicates": nul.get("replicates", [])}
        assert _digest(material) == nul["family_digest"]
        mutated = _copy.deepcopy(material)
        mutated["replicates"][0]["input_digest"] = "0" * 64
        assert _digest(mutated) != nul["family_digest"]


# ---------------------------------------------------------------------
# FCST-01: executable forecast-regime contract (producer side)
# ---------------------------------------------------------------------

class TestFcst01ForecastMode:
    def test_forecast_mode_emits_contract_fields(self):
        art = _art_forecast()
        assert art["status"] != "RUN_ERROR"
        assert art["mode"] == "FORECAST_REGIME"
        assert art["data_class"] == "ARCHIVED_OPERATIONAL"
        assert art["forecast_vintage_digests"] == \
            ["a" * 64, "b" * 64]
        assert art["forecast_feature_set"] == ["f1", "f2"]

    def test_forecast_artifact_freezes(self):
        art = _art_forecast()
        frozen = freeze_regime_artifact(art)
        assert frozen["frozen"] is True
        assert frozen["mode"] == "FORECAST_REGIME"

    def test_retrospective_default_unchanged(self):
        art = _art_std()
        assert art["mode"] == "RETROSPECTIVE_REGIME"
        assert art["data_class"] == "REANALYSIS"
        assert art["forecast_vintage_digests"] == []
        assert art["forecast_feature_set"] == []

    def test_invalid_mode_rejected(self):
        df = _frame()
        cfg = _cfg(mode="BOGUS_MODE")
        art = run_regimes(df, FEATURES, _mask(df), cfg)
        assert art["status"] == "RUN_ERROR"
        assert "mode" in art["reason"]

    def test_retrospective_with_forecast_fields_rejected(self):
        df = _frame()
        cfg = _cfg(forecast_vintage_digests=("a" * 64,))
        art = run_regimes(df, FEATURES, _mask(df), cfg)
        assert art["status"] == "RUN_ERROR"
        assert "forecast" in art["reason"]

    def test_retrospective_with_feature_set_rejected(self):
        df = _frame()
        cfg = _cfg(forecast_feature_set=("f1",))
        art = run_regimes(df, FEATURES, _mask(df), cfg)
        assert art["status"] == "RUN_ERROR"
        assert "forecast" in art["reason"]

    def test_forecast_mode_requires_vintage_digests(self):
        df = _frame()
        cfg = _cfg(mode="FORECAST_REGIME",
                   forecast_feature_set=("f1",))
        art = run_regimes(df, FEATURES, _mask(df), cfg)
        assert art["status"] == "RUN_ERROR"
        assert "forecast_vintage_digests" in art["reason"]

    def test_forecast_mode_requires_feature_set(self):
        df = _frame()
        cfg = _cfg(mode="FORECAST_REGIME",
                   forecast_vintage_digests=("a" * 64,))
        art = run_regimes(df, FEATURES, _mask(df), cfg)
        assert art["status"] == "RUN_ERROR"
        assert "forecast_feature_set" in art["reason"]

    def test_forecast_vintage_digests_must_be_sha256(self):
        df = _frame()
        cfg = _cfg(mode="FORECAST_REGIME",
                   forecast_vintage_digests=("not-hex",),
                   forecast_feature_set=("f1",))
        art = run_regimes(df, FEATURES, _mask(df), cfg)
        assert art["status"] == "RUN_ERROR"
        assert "forecast_vintage_digests" in art["reason"]

    def test_forecast_feature_set_subset_of_features(self):
        df = _frame()
        cfg = _cfg(mode="FORECAST_REGIME",
                   forecast_vintage_digests=("a" * 64,),
                   forecast_feature_set=("f1", "undeclared"))
        art = run_regimes(df, FEATURES, _mask(df), cfg)
        assert art["status"] == "RUN_ERROR"
        assert "outside" in art["reason"]

    def test_freeze_rejects_forecast_mode_wrong_data_class(self):
        art = _art_forecast()
        art["data_class"] = "REANALYSIS"
        _redigest(art)
        with pytest.raises(ValueError,
                           match="ARCHIVED_OPERATIONAL|data_class"):
            freeze_regime_artifact(art)

    def test_freeze_rejects_forecast_mode_missing_fields(self):
        art = _art_forecast()
        art["forecast_vintage_digests"] = []
        _redigest(art)
        with pytest.raises(ValueError, match="forecast"):
            freeze_regime_artifact(art)

    def test_freeze_rejects_retro_carrying_forecast_fields(self):
        art = _art_std()
        art["forecast_vintage_digests"] = ["a" * 64]
        _redigest(art)
        with pytest.raises(ValueError, match="forecast"):
            freeze_regime_artifact(art)

    def test_freeze_rejects_unknown_mode(self):
        art = _art_std()
        art["mode"] = "BOGUS_MODE"
        _redigest(art)
        with pytest.raises(ValueError, match="RegimeMode|mode"):
            freeze_regime_artifact(art)


# ---------------------------------------------------------------------
# PROV-03: byte-bound source-evidence verification
# ---------------------------------------------------------------------

class TestProv03SourceEvidence:
    def _evidence(self, tmp_path):
        root = tmp_path / "evidence"
        (root / "sub").mkdir(parents=True)
        f1 = root / "features.csv"
        f1.write_bytes(b"col1,col2\n1,2\n")
        f2 = root / "sub" / "units.csv"
        f2.write_bytes(b"unit\n1\n")
        d1, d2 = sha256_file(f1), sha256_file(f2)
        manifest = {
            "source_id": "real_src",
            "source_digests": [d1, d2],
            "units": {"f1": "mm", "f2": "mm", "f3": "mm"},
            "feature_allowlist": FEATURES,
            "lineage": {"fetch": "test"},
            "evidence_root": str(root),
            "source_files": [
                {"relpath": "features.csv", "sha256": d1},
                {"relpath": "sub/units.csv", "sha256": d2}]}
        return root, manifest

    def test_fixture_manifest_bypasses_verification(self):
        assert verify_source_evidence({"fixture": True}) == []

    def test_valid_manifest_verifies_clean(self, tmp_path):
        _, manifest = self._evidence(tmp_path)
        assert verify_source_evidence(manifest) == []

    def test_missing_evidence_root_flagged(self, tmp_path):
        _, manifest = self._evidence(tmp_path)
        del manifest["evidence_root"]
        problems = verify_source_evidence(manifest)
        assert any("evidence_root" in p for p in problems)

    def test_evidence_root_not_a_directory_flagged(self, tmp_path):
        _, manifest = self._evidence(tmp_path)
        manifest["evidence_root"] = str(tmp_path / "no_such_dir")
        problems = verify_source_evidence(manifest)
        assert any("not a directory" in p for p in problems)

    def test_missing_source_files_flagged(self, tmp_path):
        _, manifest = self._evidence(tmp_path)
        del manifest["source_files"]
        problems = verify_source_evidence(manifest)
        assert any("source_files" in p for p in problems)

    def test_tampered_file_bytes_flagged(self, tmp_path):
        root, manifest = self._evidence(tmp_path)
        (root / "features.csv").write_bytes(b"corrupted bytes")
        problems = verify_source_evidence(manifest)
        assert any("mismatch" in p for p in problems)

    def test_missing_file_flagged(self, tmp_path):
        _, manifest = self._evidence(tmp_path)
        manifest["source_files"][0]["relpath"] = "absent.csv"
        problems = verify_source_evidence(manifest)
        assert any("absent.csv" in p for p in problems)

    def test_dotdot_escape_flagged(self, tmp_path):
        root, manifest = self._evidence(tmp_path)
        outside = tmp_path / "outside.csv"
        outside.write_bytes(b"escapee")
        manifest["source_files"][0] = {
            "relpath": "../outside.csv",
            "sha256": sha256_file(outside)}
        problems = verify_source_evidence(manifest)
        assert any("outside evidence_root" in p for p in problems)

    def test_absolute_relpath_flagged(self, tmp_path):
        root, manifest = self._evidence(tmp_path)
        f1 = root / "features.csv"
        manifest["source_files"][0] = {
            "relpath": str(f1), "sha256": sha256_file(f1)}
        problems = verify_source_evidence(manifest)
        assert any("absolute" in p for p in problems)

    def test_source_digests_multiset_must_match(self, tmp_path):
        _, manifest = self._evidence(tmp_path)
        manifest["source_digests"] = ["0" * 64, "1" * 64]
        problems = verify_source_evidence(manifest)
        assert any("source_digests" in p for p in problems)

    def test_producer_runs_with_bound_evidence(self, tmp_path):
        _, manifest = self._evidence(tmp_path)
        df = _frame()
        cfg = _cfg(source_manifest=manifest)
        art = run_regimes(df, FEATURES, _mask(df), cfg)
        assert art["status"] != "RUN_ERROR", art.get("reason")

    def test_producer_rejects_tampered_evidence(self, tmp_path):
        root, manifest = self._evidence(tmp_path)
        (root / "features.csv").write_bytes(b"tampered")
        df = _frame()
        cfg = _cfg(source_manifest=manifest)
        art = run_regimes(df, FEATURES, _mask(df), cfg)
        assert art["status"] == "RUN_ERROR"
        assert "evidence" in art["reason"]

    def test_producer_rejects_manifest_without_source_files(
            self, tmp_path):
        _, manifest = self._evidence(tmp_path)
        del manifest["source_files"]
        df = _frame()
        cfg = _cfg(source_manifest=manifest)
        art = run_regimes(df, FEATURES, _mask(df), cfg)
        assert art["status"] == "RUN_ERROR"
        assert "evidence" in art["reason"] or \
            "source_files" in art["reason"]
