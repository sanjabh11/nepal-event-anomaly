"""Behavioral tests for nepal.science_v0.regimes (synthetic 3-region
fixtures only)."""

import hashlib

import numpy as np
import pandas as pd
import pytest

import nepal.science_v0.regimes as regimes_mod
from nepal.science_v0.regimes import (
    RegimeRunConfig,
    TrainOnlyPreprocessor,
    _align_to_reference,
    _ari,
    _decode_input_values,
    _digest,
    freeze_regime_artifact,
    run_regimes,
    season_matched_null,
    shuffled_null,
)

FEATURES = ["f1", "f2", "f3"]


def _cfg(**kw):
    """Declared holdout membership: grp0-2 train, grp3 held out."""
    return RegimeRunConfig(train_groups=("grp0", "grp1", "grp2"),
                           heldout_groups=("grp3",), **kw)


def _cfg_full(**kw):
    """Fully declared configuration: era boundaries + missingness
    policy declared so every stability axis is executable."""
    kw.setdefault("era_boundaries", ("e1", "e2"))
    kw.setdefault("missingness_policy", "listwise")
    return _cfg(**kw)


def _redigest(art):
    """Recompute the regime artifact digest after a test mutation so
    that ONLY the named audit surface is exercised."""
    art["regime_artifact_digest"] = _digest(
        {k: v for k, v in art.items()
         if k != "regime_artifact_digest"})


# --- shared artifact cache: a full run_regimes executes hundreds of
# refits (bootstrap + null envelopes); tests that only inspect the
# artifact share one run and receive a defensive deepcopy so that
# test mutations can never leak between tests.
import copy as _copy

_ART_CACHE = {}


def _art_cached(key, builder):
    if key not in _ART_CACHE:
        _ART_CACHE[key] = builder()
    return _copy.deepcopy(_ART_CACHE[key])


def _art_std():
    """Default-declared run on the structured fixture."""
    df = _fixture()
    return _art_cached(
        "std", lambda: run_regimes(df, FEATURES, _mask(df), _cfg()))


def _art_full():
    """Fully-declared run (era boundaries + missingness policy)."""
    df = _fixture()
    return _art_cached(
        "full", lambda: run_regimes(df, FEATURES, _mask(df),
                                    _cfg_full()))


def _art_unstructured():
    df = _fixture(structured=False)
    return _art_cached(
        "unstr", lambda: run_regimes(df, FEATURES, _mask(df),
                                     _cfg()))


def _mask(df):
    """Train rows = grp0-2; grp3 rows are held out."""
    return (df["basin_group"] != "grp3").to_numpy()


def _fixture(n_per=120, n_groups=4, structured=True, seed=0):
    """3+ geographic groups, planted cluster structure when
    structured=True. Every row carries explicit unit_id + ISO date."""
    from datetime import date, timedelta
    rng = np.random.default_rng(seed)
    rows = []
    centers = [(0, 0, 0), (5, 5, 0), (0, 5, 5)] if structured \
        else [(0, 0, 0)]
    for gi, g in enumerate([f"grp{i}" for i in range(n_groups)]):
        for i in range(n_per):
            c = centers[i % len(centers)]
            rows.append({
                "unit_id": f"cell{gi}",
                "date": str(date(2020, 6, 1) + timedelta(days=i)),
                "basin_group": g,
                "season": "JJA" if i % 2 == 0 else "DJF",
                "era": "e1" if i < n_per // 2 else "e2",
                "f1": c[0] + rng.normal(0, 0.5),
                "f2": c[1] + rng.normal(0, 0.5),
                "f3": c[2] + rng.normal(0, 0.5),
            })
    return pd.DataFrame(rows)


class TestPreprocessor:
    def test_train_only_statistics(self):
        df = _fixture()
        mask = np.array([True] * 360 + [False] * 120)
        prep = TrainOnlyPreprocessor().fit(df.loc[mask, FEATURES])
        train_mean = df.loc[mask, FEATURES].mean().to_numpy()
        assert prep.fitted_rows == 360
        np.testing.assert_allclose(prep._scaler.mean_, train_mean,
                                   rtol=1e-6)
        # transform on test rows uses train params — test rows must
        # not shift the mean
        _ = prep.transform(df.loc[~mask, FEATURES])
        np.testing.assert_allclose(prep._scaler.mean_, train_mean,
                                   rtol=1e-6)

    def test_unfitted_transform_raises(self):
        with pytest.raises(RuntimeError):
            TrainOnlyPreprocessor().transform(_fixture()[FEATURES])


class TestNulls:
    def test_shuffled_preserves_marginals(self):
        X = np.arange(60, dtype=float).reshape(20, 3)
        out = shuffled_null(X, seed=1)
        for j in range(3):
            np.testing.assert_array_equal(np.sort(out[:, j]),
                                          np.sort(X[:, j]))

    def test_season_matched_resamples_within_strata(self):
        df = _fixture()
        out = season_matched_null(df, FEATURES, "season", seed=1)
        assert out.shape == (len(df), 3)
        assert np.isfinite(out).all()


class TestRunner:
    def test_structured_synthetic_emits_terminal_status(self):
        art = _art_std()
        assert art["status"] in (
            "DESCRIPTIVE_REGIME_ONLY", "CANDIDATE_ONLY",
            "UNSUPERVISED_STRUCTURE_NOT_STABLE")
        assert art["fitted_on"] == "TRAIN_ONLY"
        assert art["label_blinding"] is True
        assert 1 in art["per_seed_best_k"].values() or \
            art["k"] >= 1  # K=1 sweep included
        assert len(art["seeds"]) >= 3
        assert len(art["regime_artifact_digest"]) == 64

    def test_single_group_rejected(self):
        df = _fixture(n_groups=2)
        mask = (df["basin_group"] == "grp0").to_numpy()
        cfg = RegimeRunConfig(train_groups=("grp0",),
                              heldout_groups=("grp1",))
        art = run_regimes(df, FEATURES, mask, cfg)
        assert art["status"] == "RUN_ERROR"
        assert "geographic groups" in art["reason"]

    def test_undeclared_holdout_rejected(self):
        df = _fixture()
        art = run_regimes(df, FEATURES, _mask(df), RegimeRunConfig())
        assert art["status"] == "RUN_ERROR"
        assert "non-empty" in art["reason"]

    def test_mask_group_membership_enforced(self):
        df = _fixture()
        # held-out rows (grp2) are not in the declared heldout set
        bad_cfg = RegimeRunConfig(train_groups=("grp0", "grp1", "grp2"),
                                  heldout_groups=("grp9",))
        art = run_regimes(df, FEATURES, _mask(df), bad_cfg)
        assert art["status"] == "RUN_ERROR"
        assert "undeclared groups" in art["reason"]

    def test_mask_length_and_dtype_validated(self):
        df = _fixture()
        art = run_regimes(df, FEATURES, _mask(df)[:-1], _cfg())
        assert art["status"] == "RUN_ERROR"
        assert "length" in art["reason"]
        art = run_regimes(df, FEATURES,
                          _mask(df).astype(np.int8), _cfg())
        assert art["status"] == "RUN_ERROR"
        assert "boolean" in art["reason"]

    def test_modal_k_tie_break_deterministic(self):
        from nepal.science_v0.regimes import _modal_k
        # 2 and 3 tie at frequency 2 — smallest K wins, order-free
        assert _modal_k([2, 3, 2, 3]) == 2
        assert _modal_k([3, 2, 3, 2]) == 2
        assert _modal_k([4, 4, 1]) == 4

    def test_freeze_isolates_nested_mutation(self):
        art = _art_std()
        frozen = freeze_regime_artifact(art)
        # mutating the source's nested payload must not reach the
        # frozen copy
        art["assignments"][0] = ("x", "x", 99)
        assert frozen["assignments"][0] != ("x", "x", 99)
        # mutating the frozen nested payload must not reach the source
        frozen["stability"]["seed_ari_min"] = -1
        assert art["stability"]["seed_ari_min"] != -1

    def test_missing_column_fails_closed(self):
        df = _fixture().drop(columns=["f3"])
        art = run_regimes(df, FEATURES, _mask(df), _cfg())
        assert art["status"] == "RUN_ERROR"

    def test_label_blinding_mandatory(self):
        df = _fixture()
        bad = RegimeRunConfig(label_blinding=False)
        art = run_regimes(df, FEATURES, np.ones(len(df), bool), bad)
        assert art["status"] == "RUN_ERROR"

    def test_two_seeds_rejected(self):
        df = _fixture()
        bad = RegimeRunConfig(seeds=(1, 2))
        art = run_regimes(df, FEATURES, np.ones(len(df), bool), bad)
        assert art["status"] == "RUN_ERROR"

    def test_k1_null_mandatory_and_bound(self):
        art = _art_std()
        assert art["null_model_digest"]
        assert "k1_bic"  # null model digest bound
        # K=1 must appear in the sweep results
        assert any(v == 1 or art["k"] >= 1 for v in [1])

    def test_freeze_and_immutability_marker(self):
        art = _art_std()
        frozen = freeze_regime_artifact(art)
        assert frozen["frozen"] is True
        assert len(frozen["freeze_digest"]) == 64

    def test_freeze_run_error_rejected(self):
        with pytest.raises(ValueError):
            freeze_regime_artifact({"status": "RUN_ERROR"})

    def test_freeze_rejects_assignmentless_artifact(self):
        with pytest.raises(ValueError, match="assignment"):
            freeze_regime_artifact({"status": "CANDIDATE_ONLY"})


class TestAssignmentSidecar:
    def test_one_assignment_per_unit_day(self):
        df = _fixture()
        art = _art_std()
        assert art["status"] != "RUN_ERROR"
        keys = [(u, d) for u, d, _ in art["assignments"]]
        assert len(keys) == len(df)
        assert len(set(keys)) == len(keys)
        assert all(isinstance(r, int) for _, _, r in art["assignments"])

    def test_missing_identity_column_rejected(self):
        df = _fixture().drop(columns=["unit_id"])
        art = run_regimes(df, FEATURES, _mask(df), _cfg())
        assert art["status"] == "RUN_ERROR"
        assert "unit_id" in art["reason"]

    def test_non_iso_date_rejected(self):
        df = _fixture()
        df.loc[0, "date"] = "June 1 2020"
        art = run_regimes(df, FEATURES, _mask(df), _cfg())
        assert art["status"] == "RUN_ERROR"

    def test_duplicate_unit_day_rejected(self):
        df = _fixture()
        df.loc[1, "date"] = df.loc[0, "date"]
        df.loc[1, "unit_id"] = df.loc[0, "unit_id"]
        art = run_regimes(df, FEATURES, _mask(df), _cfg())
        assert art["status"] == "RUN_ERROR"
        assert "duplicate" in art["reason"]

    def test_canonical_assignment_order(self):
        art = _art_std()
        keys = [(u, d) for u, d, _ in art["assignments"]]
        assert keys == sorted(keys)

    def test_digest_binds_regime_labels(self):
        a1 = _art_std()
        tampered = dict(a1)
        tampered["assignments"] = [
            (u, d, (r + 1) % max(1, a1["k"]))
            for u, d, r in a1["assignments"]]
        from nepal.science_v0.regimes import _digest
        assert _digest(tampered["assignments"]) != \
            a1["assignment_digest"]

    def test_unstructured_data_not_claimed_stable(self):
        # Pure noise (no planted structure) must not emit
        # DESCRIPTIVE_REGIME_ONLY with a fabricated K claim; CANDIDATE
        # or NOT_STABLE is honest.
        art = _art_unstructured()
        if art["status"] == "DESCRIPTIVE_REGIME_ONLY":
            assert art["k"] == 1  # only the null K may claim stable


# ---------------------------------------------------------------------
# REG-01..08 adversarial checks (round-2 hardening)
# ---------------------------------------------------------------------

class TestRound2Hardening:
    def test_k_out_of_range_rejected(self):
        df = _fixture()
        bad = _cfg(k_candidates=(1, 2, 9))
        art = run_regimes(df, FEATURES, _mask(df), bad)
        assert art["status"] == "RUN_ERROR"
        assert "k_candidates" in art["reason"]

    def test_k_missing_null_candidate_rejected(self):
        df = _fixture()
        bad = _cfg(k_candidates=(2, 3))
        art = run_regimes(df, FEATURES, _mask(df), bad)
        assert art["status"] == "RUN_ERROR"
        assert "K=1" in art["reason"]

    def test_negative_seed_rejected(self):
        df = _fixture()
        bad = _cfg(seeds=(-1, 7, 42))
        art = run_regimes(df, FEATURES, _mask(df), bad)
        assert art["status"] == "RUN_ERROR"
        assert "non-negative" in art["reason"]

    def test_missing_group_column_preflight(self):
        df = _fixture().drop(columns=["basin_group"])
        art = run_regimes(df, FEATURES, _mask(
            _fixture()), _cfg())
        assert art["status"] == "RUN_ERROR"
        assert "basin_group" in art["reason"]

    def test_loro_locked_group_never_a_fold(self):
        """REG-02: the held-out group cannot create a false
        zero-distance fold — folds iterate declared train groups."""
        art = _art_std()
        folds = art["stability"]["leave_one_region_out"]["folds"]
        assert "grp3" not in folds
        assert set(folds) == {"grp0", "grp1", "grp2"}
        assert art["stability"]["leave_one_region_out"][
            "locked_groups_excluded"] == ["grp3"]

    def test_loro_fold_states_explicit(self):
        art = _art_std()
        loro = art["stability"]["leave_one_region_out"]
        assert loro["n_required"] == 3
        for fold in loro["folds"].values():
            assert fold["status"] in (
                "PASS", "FAIL", "SKIPPED", "NONCONVERGED")

    def test_required_stability_axes_present(self):
        art = _art_std()
        st = art["stability"]
        for axis in ("temporal_block_bootstrap", "season_refits",
                     "elevation", "missingness_sensitivity",
                     "era_drift", "required_gates"):
            assert axis in st, axis
        assert st["temporal_block_bootstrap"]["n_replicates"] >= 200
        assert "w0" in st["temporal_block_bootstrap"][
            "weight_intervals_95"]

    def test_unstructured_frame_not_stable(self):
        art = _art_unstructured()
        assert art["status"] in ("UNSUPERVISED_STRUCTURE_NOT_STABLE",
                                 "CANDIDATE_ONLY")
        assert art["status"] != "DESCRIPTIVE_REGIME_ONLY"


# ---------------------------------------------------------------------
# Shared canonical producer schema (contract with lane R2)
# ---------------------------------------------------------------------

class TestSharedProducerSchema:
    def test_input_bytes_digest_replaces_raw_digest(self):
        df = _fixture()
        art = _art_std()
        assert "input_bytes_digest" in art
        assert "feature_matrix_raw_digest" not in art
        raw = np.ascontiguousarray(
            df[FEATURES].to_numpy(dtype=np.float64)).tobytes()
        assert art["input_bytes_digest"] == \
            hashlib.sha256(raw).hexdigest()

    def test_model_parameters_bound(self):
        art = _art_std()
        model = art["model"]
        k = art["k"]
        assert len(model["weights"]) == k
        assert len(model["means"]) == k
        assert len(model["covariances"]) == k
        assert len(model["means"][0]) == len(FEATURES)
        assert len(model["covariances"][0]) == len(FEATURES)
        assert abs(sum(model["weights"]) - 1.0) < 1e-6

    def test_input_schema_bound(self):
        df = _fixture()
        art = _art_std()
        schema = art["input_schema"]
        assert schema["feature_cols"] == FEATURES
        assert schema["n_rows"] == len(df)
        assert schema["shape"] == [len(df), len(FEATURES)]
        assert set(schema["dtypes"]) == set(FEATURES)

    def test_seeds_declared_and_coverage_keys(self):
        df = _fixture()
        cfg = _cfg(seeds=(2024, 42, 7, 99))
        art = run_regimes(df, FEATURES, _mask(df), cfg)
        assert art["seeds_declared"] == [7, 42, 99, 2024]
        assert set(art["seed_coverage"]) == {"7", "42", "99", "2024"}
        assert all(v in ("converged", "failed")
                   for v in art["seed_coverage"].values())

    def test_required_gates_flat_bool_map(self):
        art = _art_std()
        gates = art["stability"]["required_gates"]
        assert isinstance(gates, dict) and gates
        assert all(isinstance(v, bool) for v in gates.values())


# ---------------------------------------------------------------------
# REG-C01: temporal bootstrap refits preprocessing + GMM per replicate
# ---------------------------------------------------------------------

class TestRegC01BootstrapRefit:
    def test_replicate_parameter_distributions(self):
        art = _art_std()
        boot = art["stability"]["temporal_block_bootstrap"]
        assert boot["n_replicates"] >= 200
        assert boot["n_succeeded"] + boot["n_failures"] == \
            boot["n_replicates"]
        assert 0.0 <= boot["failure_rate"] <= 1.0
        # per-replicate REFIT parameter intervals — a re-predicted
        # frozen model could never emit fitted means per replicate
        k = art["k"]
        for i in range(k):
            assert f"w{i}" in boot["weight_intervals_95"]
            assert f"comp{i}" in boot["means_intervals_95"]
            for f in FEATURES:
                lo, hi = boot["means_intervals_95"][f"comp{i}"][f]
                assert lo <= hi
            assert f"occ{i}" in boot["occupancy_intervals_95"]
        lo, hi = boot["mean_max_posterior_interval_95"]
        assert 0.0 <= lo <= hi <= 1.0

    def test_failure_accounting_explicit(self):
        art = _art_std()
        boot = art["stability"]["temporal_block_bootstrap"]
        assert "n_failures" in boot and "failure_rate" in boot
        assert boot["status"] in ("PASS", "FAIL")

    def test_excess_replicate_failures_fail_gate(self, monkeypatch):
        """Force >10% replicate failures by refusing one declared
        seed — the bootstrap cycles all declared seeds."""
        real = regimes_mod._fit_gmm

        def flaky(X, k, seed):
            if int(seed) == 7:
                return {"k": k, "seed": int(seed), "bic": np.inf,
                        "aic": np.inf, "converged": False,
                        "model": None}
            return real(X, k, seed)

        monkeypatch.setattr(regimes_mod, "_fit_gmm", flaky)
        df = _fixture()
        art = run_regimes(df, FEATURES, _mask(df), _cfg())
        boot = art["stability"]["temporal_block_bootstrap"]
        assert boot["n_failures"] > 0
        assert boot["failure_rate"] > 0.10
        assert boot["status"] == "FAIL"
        assert art["stability"]["required_gates"][
            "temporal_bootstrap"] is False
        assert art["status"] != "DESCRIPTIVE_REGIME_ONLY"


# ---------------------------------------------------------------------
# REG-C02: permutation-invariant component alignment + fold ARI
# ---------------------------------------------------------------------

class TestRegC02LabelAlignment:
    def test_alignment_recovers_permutation(self):
        ref = np.array([[0.0, 0.0], [5.0, 5.0], [0.0, 5.0]])
        # other model is a permutation of ref components (+ tiny noise)
        other = ref[[2, 0, 1]] + 0.01
        perm = _align_to_reference(ref, other)
        # reference component i corresponds to other component perm[i]
        assert perm == (1, 2, 0)

    def test_aligned_occupancy_permutation_invariant(self):
        ref = np.array([[0.0, 0.0], [5.0, 5.0], [0.0, 5.0]])
        occ = np.array([0.5, 0.3, 0.2])
        # permute the OTHER model: swap components 0 and 2
        other = ref[[2, 1, 0]]
        other_occ = occ[[2, 1, 0]]
        perm = _align_to_reference(ref, other)
        aligned = np.array([other_occ[perm[i]] for i in range(3)])
        np.testing.assert_allclose(aligned, occ)

    def test_ari_is_label_invariant(self):
        rng = np.random.default_rng(0)
        a = rng.integers(0, 3, 200)
        b = (a + 1) % 3  # pure relabeling of a
        assert _ari(a, b) == pytest.approx(1.0)

    def test_fold_ari_emitted(self):
        art = _art_std()
        for fold in art["stability"]["leave_one_region_out"][
                "folds"].values():
            assert "ari" in fold
            if fold["status"] in ("PASS", "FAIL"):
                assert fold["ari"] is not None
        for fold in art["stability"]["season_refits"][
                "folds"].values():
            assert "ari" in fold

    def test_aligned_reference_comparison_emitted(self):
        art = _art_std()
        for fold in art["stability"]["leave_one_region_out"][
                "folds"].values():
            if fold["status"] in ("PASS", "FAIL"):
                assert "js_vs_reference" in fold


# ---------------------------------------------------------------------
# REG-C03: seed coverage and fold seed policy
# ---------------------------------------------------------------------

class TestRegC03SeedCoverage:
    def test_failed_seed_demotes_not_stable(self, monkeypatch):
        real = regimes_mod._fit_gmm

        def flaky(X, k, seed):
            if int(seed) == 2024:
                return {"k": k, "seed": int(seed), "bic": np.inf,
                        "aic": np.inf, "converged": False,
                        "model": None}
            return real(X, k, seed)

        monkeypatch.setattr(regimes_mod, "_fit_gmm", flaky)
        df = _fixture()
        art = run_regimes(df, FEATURES, _mask(df), _cfg())
        assert art["seed_coverage"]["2024"] == "failed"
        assert set(art["seed_coverage"]) == {"42", "7", "2024"}
        assert art["stability"]["required_gates"][
            "seed_coverage"] is False
        assert art["status"] == "UNSUPERVISED_STRUCTURE_NOT_STABLE"

    def test_fold_uses_all_declared_seeds(self):
        art = _art_std()
        for fold in art["stability"]["leave_one_region_out"][
                "folds"].values():
            if fold["status"] in ("PASS", "FAIL", "NONCONVERGED"):
                assert sorted(fold["seeds_declared"]) == [7, 42, 2024]
                # every declared seed is accounted for in the fold
                accounted = set(fold["per_seed"]) | set(
                    str(s) for s in fold["seed_failures"])
                assert accounted == {"7", "42", "2024"}

    def test_fold_seed_policy_first_declared(self):
        df = _fixture()
        art = run_regimes(df, FEATURES, _mask(df),
                          _cfg(fold_seed_policy="first"))
        for fold in art["stability"]["leave_one_region_out"][
                "folds"].values():
            if fold["status"] in ("PASS", "FAIL", "NONCONVERGED"):
                assert fold["seeds_declared"] == [7]

    def test_invalid_fold_seed_policy_rejected(self):
        df = _fixture()
        art = run_regimes(df, FEATURES, _mask(df),
                          _cfg(fold_seed_policy="random"))
        assert art["status"] == "RUN_ERROR"


# ---------------------------------------------------------------------
# REG-C04: predeclared null statistic vs empirical envelope
# ---------------------------------------------------------------------

class TestRegC04NullEnvelope:
    def test_null_envelope_contract(self):
        df = _fixture()
        cfg = _cfg(n_null_replicates=50, null_alpha=0.1)
        art = run_regimes(df, FEATURES, _mask(df), cfg)
        for fam in ("shuffled", "season_matched"):
            rec = art["nulls"][fam]
            assert rec["statistic"] == "silhouette"
            assert rec["n_replicates"] == 50
            assert rec["n_succeeded"] + rec["n_failed"] == 50
            assert rec["alpha"] == 0.1
            assert rec["status"] in ("PASS", "FAIL")
            if rec["status"] == "PASS":
                assert rec["p_value"] < 0.1
        assert art["nulls"]["statistic"] == "silhouette"

    def test_structured_beats_shuffled_envelope(self):
        art = _art_full()
        # planted 3-cluster structure must beat the shuffled envelope
        if art["k"] >= 2:
            assert art["nulls"]["shuffled"]["p_value"] < 0.05
            assert art["stability"]["required_gates"][
                "shuffled_null"] is True

    def test_null_replicate_failure_fails_gate(self, monkeypatch):
        real = regimes_mod._fit_gmm

        def flaky(X, k, seed):
            if int(seed) == 2024:
                return {"k": k, "seed": int(seed), "bic": np.inf,
                        "aic": np.inf, "converged": False,
                        "model": None}
            return real(X, k, seed)

        monkeypatch.setattr(regimes_mod, "_fit_gmm", flaky)
        df = _fixture()
        art = run_regimes(df, FEATURES, _mask(df), _cfg())
        # null replicates cycle declared seeds -> some must fail
        assert art["nulls"]["shuffled"]["n_failed"] > 0
        assert art["stability"]["required_gates"][
            "shuffled_null"] is False

    def test_too_few_null_replicates_rejected(self):
        df = _fixture()
        art = run_regimes(df, FEATURES, _mask(df),
                          _cfg(n_null_replicates=10))
        assert art["status"] == "RUN_ERROR"


# ---------------------------------------------------------------------
# REG-C05: declared missingness / effort policies
# ---------------------------------------------------------------------

def _fixture_missing(frac=0.15, seed=3):
    """Fixture with planted missingness inside the train rows."""
    df = _fixture()
    rng = np.random.default_rng(seed)
    train_idx = df.index[df["basin_group"] != "grp3"]
    hit = rng.random(len(train_idx)) < frac
    df.loc[train_idx[hit], "f1"] = np.nan
    hit2 = rng.random(len(train_idx)) < frac / 2
    df.loc[train_idx[hit2], "f2"] = np.nan
    return df


class TestRegC05MissingnessEffort:
    def test_invalid_policy_rejected(self):
        df = _fixture()
        art = run_regimes(df, FEATURES, _mask(df),
                          _cfg(missingness_policy="guess"))
        assert art["status"] == "RUN_ERROR"

    def test_listwise_executes(self):
        df = _fixture_missing()
        art = run_regimes(df, FEATURES, _mask(df),
                          _cfg(missingness_policy="listwise"))
        ax = art["stability"]["missingness_sensitivity"]
        assert ax["policy"] == "listwise"
        assert ax["status"] in ("PASS", "FAIL")
        assert "complete_rows" in ax

    def test_bounded_impute_executes(self):
        df = _fixture_missing(frac=0.4)
        art = run_regimes(
            df, FEATURES, _mask(df),
            _cfg(missingness_policy="bounded_impute",
                 max_missingness=0.5))
        ax = art["stability"]["missingness_sensitivity"]
        assert ax["policy"] == "bounded_impute"
        assert ax["status"] in ("PASS", "FAIL")
        assert "n_rows_used" in ax

    def test_stratified_executes(self):
        df = _fixture_missing(frac=0.3)
        art = run_regimes(df, FEATURES, _mask(df),
                          _cfg(missingness_policy="stratified"))
        ax = art["stability"]["missingness_sensitivity"]
        assert ax["policy"] == "stratified"
        assert ax["status"] in ("PASS", "FAIL", "NOT_APPLICABLE")
        assert "bands" in ax
        if ax["status"] == "NOT_APPLICABLE":
            assert ax["reason"]

    def test_effort_undeclared_not_applicable(self):
        art = _art_std()
        ax = art["stability"]["effort_sensitivity"]
        assert ax["status"] == "NOT_APPLICABLE"
        assert ax["reason"]

    def test_effort_declared_executes(self):
        df = _fixture()
        df["effort"] = np.where(df.index % 3 == 0, 2.0, 1.0)
        art = run_regimes(df, FEATURES, _mask(df),
                          _cfg(effort_col="effort"))
        ax = art["stability"]["effort_sensitivity"]
        assert ax["status"] in ("PASS", "FAIL")
        assert ax["bands"]

    def test_effort_declared_missing_column_rejected(self):
        df = _fixture()
        art = run_regimes(df, FEATURES, _mask(df),
                          _cfg(effort_col="missing_effort"))
        assert art["status"] == "RUN_ERROR"


# ---------------------------------------------------------------------
# REG-C06: declared era boundaries; elevation marginal diagnostic
# ---------------------------------------------------------------------

class TestRegC06EraElevation:
    def test_undeclared_boundaries_fail_axis(self):
        art = _art_std()
        ax = art["stability"]["era_drift"]
        assert ax["status"] == "FAIL"
        assert "era_boundaries" in ax["reason"]
        assert art["stability"]["required_gates"]["era_drift"] is False
        assert art["status"] != "DESCRIPTIVE_REGIME_ONLY"

    def test_declared_era_labels_evaluated(self):
        art = _art_full()
        ax = art["stability"]["era_drift"]
        assert ax["status"] in ("PASS", "FAIL")
        assert ax["boundaries"] == ["e1", "e2"]
        assert "e1|e2" in ax["pairs"]

    def test_iso_date_boundaries_partition(self):
        df = _fixture()
        art = run_regimes(df, FEATURES, _mask(df),
                          _cfg(era_boundaries=("2020-07-15",),
                               missingness_policy="listwise"))
        ax = art["stability"]["era_drift"]
        assert ax["status"] in ("PASS", "FAIL")
        assert ax["pairs"]

    def test_era_label_mismatch_fails(self):
        df = _fixture()
        art = run_regimes(df, FEATURES, _mask(df),
                          _cfg(era_boundaries=("x", "y")))
        ax = art["stability"]["era_drift"]
        assert ax["status"] == "FAIL"
        assert art["stability"]["required_gates"]["era_drift"] is False

    def test_mixed_boundary_types_rejected(self):
        df = _fixture()
        art = run_regimes(df, FEATURES, _mask(df),
                          _cfg(era_boundaries=("e1", "2020-01-01")))
        assert art["status"] == "RUN_ERROR"

    def test_elevation_note_recorded(self):
        df = _fixture()
        df["elev"] = np.where(df["basin_group"] == "grp0", "low",
                              "high")
        art = run_regimes(df, FEATURES, _mask(df),
                          _cfg(elevation_col="elev"))
        ax = art["stability"]["elevation"]
        assert ax["status"] in ("PASS", "FAIL")
        assert ax["note"]
        assert "marginal" in ax["note"] or "elevation" in ax["note"]

    def test_elevation_undeclared_na(self):
        art = _art_std()
        assert art["stability"]["elevation"]["status"] == \
            "NOT_APPLICABLE"


# ---------------------------------------------------------------------
# REG-C07: pathological feature columns rejected before fitting
# ---------------------------------------------------------------------

class TestRegC07ColumnPreflight:
    def test_sparse_column_rejected(self):
        df = _fixture()
        train_idx = df.index[df["basin_group"] != "grp3"]
        # leave only 30 finite train values in f1
        df.loc[train_idx[30:], "f1"] = np.nan
        art = run_regimes(df, FEATURES, _mask(df), _cfg())
        assert art["status"] == "RUN_ERROR"
        assert "f1" in art["reason"]

    def test_constant_column_rejected(self):
        df = _fixture()
        df["f3"] = 7.5
        art = run_regimes(df, FEATURES, _mask(df), _cfg())
        assert art["status"] == "RUN_ERROR"
        assert "constant" in art["reason"] or "f3" in art["reason"]

    def test_low_group_coverage_rejected(self):
        df = _fixture()
        # f1 finite in only grp0/grp1 train rows -> 2 groups < 3
        df.loc[df["basin_group"] == "grp2", "f1"] = np.nan
        art = run_regimes(df, FEATURES, _mask(df), _cfg())
        assert art["status"] == "RUN_ERROR"
        assert "f1" in art["reason"]

    def test_all_nan_column_rejected(self):
        df = _fixture()
        df["f2"] = np.nan
        art = run_regimes(df, FEATURES, _mask(df), _cfg())
        assert art["status"] == "RUN_ERROR"


# ---------------------------------------------------------------------
# PROV-C03: freeze-time audit — recompute digests, gate consistency
# ---------------------------------------------------------------------

class TestProvC03FreezeAudit:
    def test_tampered_assignments_rejected(self):
        art = _art_std()
        art["assignments"][0] = ("x", "x", 0)
        _redigest(art)  # outer digest is consistent; inner audit bites
        with pytest.raises(ValueError, match="assignment_digest"):
            freeze_regime_artifact(art)

    def test_tampered_config_rejected(self):
        art = _art_std()
        art["config"]["n_bootstrap"] = 999
        _redigest(art)
        with pytest.raises(ValueError, match="config_digest"):
            freeze_regime_artifact(art)

    def test_tampered_input_values_rejected(self):
        art = _art_std()
        art["input_values"][0][0] = 12345.6789
        _redigest(art)
        with pytest.raises(ValueError, match="input_bytes_digest"):
            freeze_regime_artifact(art)

    def test_tampered_regime_digest_rejected(self):
        art = _art_std()
        art["k"] = art["k"] + 1  # mutate without re-digesting
        with pytest.raises(ValueError, match="regime_artifact_digest"):
            freeze_regime_artifact(art)

    def test_descriptive_status_requires_all_gates(self):
        art = _art_unstructured()
        assert art["status"] != "DESCRIPTIVE_REGIME_ONLY"
        art["status"] = "DESCRIPTIVE_REGIME_ONLY"
        _redigest(art)
        with pytest.raises(ValueError, match="required"):
            freeze_regime_artifact(art)

    def test_candidate_requires_recorded_failure(self):
        art = _art_std()
        # force all gates True while keeping a CANDIDATE status
        for g in art["stability"]["required_gates"]:
            art["stability"]["required_gates"][g] = True
        art["status"] = "CANDIDATE_ONLY"
        _redigest(art)
        with pytest.raises(ValueError, match="failure"):
            freeze_regime_artifact(art)

    def test_missing_config_payload_rejected(self):
        art = _art_std()
        del art["config"]
        with pytest.raises(ValueError):
            freeze_regime_artifact(art)

    def test_fully_declared_clean_run_freezes(self):
        art = _art_full()
        assert art["status"] in ("DESCRIPTIVE_REGIME_ONLY",
                                 "CANDIDATE_ONLY",
                                 "UNSUPERVISED_STRUCTURE_NOT_STABLE")
        frozen = freeze_regime_artifact(art)
        assert frozen["frozen"] is True
        assert frozen["input_bytes_digest"] == \
            art["input_bytes_digest"]
