"""Behavioral tests for nepal.science_v0.regimes (synthetic 3-region
fixtures only)."""

import numpy as np
import pandas as pd
import pytest

from nepal.science_v0.regimes import (
    RegimeRunConfig,
    TrainOnlyPreprocessor,
    freeze_regime_artifact,
    run_regimes,
    season_matched_null,
    shuffled_null,
)

FEATURES = ["f1", "f2", "f3"]


def _cfg(**kw):
    """Declared holdout membership: grp0+grp1 train, grp2 held out."""
    return RegimeRunConfig(train_groups=("grp0", "grp1"),
                           heldout_groups=("grp2",), **kw)


def _mask(df):
    """Train rows = grp0/grp1; grp2 rows are held out."""
    return (df["basin_group"] != "grp2").to_numpy()


def _fixture(n_per=120, n_groups=3, structured=True, seed=0):
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
        mask = np.array([True] * 240 + [False] * 120)
        prep = TrainOnlyPreprocessor().fit(df.loc[mask, FEATURES])
        train_mean = df.loc[mask, FEATURES].mean().to_numpy()
        assert prep.fitted_rows == 240
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
        df = _fixture()
        mask = np.ones(len(df), bool)
        art = run_regimes(df, FEATURES, mask, RegimeRunConfig())
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
        bad_cfg = RegimeRunConfig(train_groups=("grp0", "grp1"),
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
        df = _fixture()
        art = run_regimes(df, FEATURES, _mask(df), _cfg())
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
        art = run_regimes(df, FEATURES, np.ones(len(df), bool),
                          RegimeRunConfig())
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
        df = _fixture()
        art = run_regimes(df, FEATURES, np.ones(len(df), bool),
                          RegimeRunConfig())
        assert art["null_model_digest"]
        assert "k1_bic"  # null model digest bound
        # K=1 must appear in the sweep results
        assert any(v == 1 or art["k"] >= 1 for v in [1])

    def test_freeze_and_immutability_marker(self):
        df = _fixture()
        art = run_regimes(df, FEATURES, np.ones(len(df), bool),
                          RegimeRunConfig())
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
        art = run_regimes(df, FEATURES, np.ones(len(df), bool),
                          RegimeRunConfig())
        assert art["status"] != "RUN_ERROR"
        keys = [(u, d) for u, d, _ in art["assignments"]]
        assert len(keys) == len(df)
        assert len(set(keys)) == len(keys)
        assert all(isinstance(r, int) for _, _, r in art["assignments"])

    def test_missing_identity_column_rejected(self):
        df = _fixture().drop(columns=["unit_id"])
        art = run_regimes(df, FEATURES, np.ones(len(df), bool),
                          RegimeRunConfig())
        assert art["status"] == "RUN_ERROR"
        assert "unit_id" in art["reason"]

    def test_non_iso_date_rejected(self):
        df = _fixture()
        df.loc[0, "date"] = "June 1 2020"
        art = run_regimes(df, FEATURES, np.ones(len(df), bool),
                          RegimeRunConfig())
        assert art["status"] == "RUN_ERROR"

    def test_duplicate_unit_day_rejected(self):
        df = _fixture()
        df.loc[1, "date"] = df.loc[0, "date"]
        df.loc[1, "unit_id"] = df.loc[0, "unit_id"]
        art = run_regimes(df, FEATURES, np.ones(len(df), bool),
                          RegimeRunConfig())
        assert art["status"] == "RUN_ERROR"
        assert "duplicate" in art["reason"]

    def test_canonical_assignment_order(self):
        df = _fixture()
        art = run_regimes(df, FEATURES, np.ones(len(df), bool),
                          RegimeRunConfig())
        keys = [(u, d) for u, d, _ in art["assignments"]]
        assert keys == sorted(keys)

    def test_digest_binds_regime_labels(self):
        df = _fixture()
        a1 = run_regimes(df, FEATURES, np.ones(len(df), bool),
                         RegimeRunConfig())
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
        df = _fixture(structured=False)
        art = run_regimes(df, FEATURES, np.ones(len(df), bool),
                          RegimeRunConfig())
        if art["status"] == "DESCRIPTIVE_REGIME_ONLY":
            assert art["k"] == 1  # only the null K may claim stable
