"""Round-5 adversarial probes — one behavioral test per closed
residual gate.  Everything here is synthetic fixture scope only."""
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from nepal.research_v0._hashing import sha256_canonical
from nepal.research_v0.producer_validation import (
    canonical_unit_basin_pairs, row_key,
    semantic_feature_matrix_digest, sorted_row_key_digest)
from nepal.science_v0.regimes import (
    RegimeRunConfig, _null_envelope, freeze_regime_artifact,
    run_regimes)
from nepal.experiment_v0.adapters import (
    regime_assignment_from_artifact)
from nepal.experiment_v0.audit import audit_producer_payload
from nepal.experiment_v0.evaluation import evaluate
from nepal.research_v0.gates import REQUIRED_REGIME_GATE_NAMES

from tests.fixtures import synthetic_exp_b4 as fx4
from tests.fixtures import synthetic_exp_b3 as fx3

FEATURES = ["f1", "f2", "f3"]


def _frame(n_per=120, groups=("grp0", "grp1", "grp2", "grp3"),
           day_step=1, seed=0):
    """Daily-grid synthetic frame, one unit per group."""
    rng = np.random.default_rng(seed)
    rows = []
    for gi, g in enumerate(groups):
        for i in range(n_per):
            rows.append({
                "unit_id": f"cell{gi}",
                "date": str(date(2020, 6, 1) + timedelta(days=i * day_step)),
                "basin_group": g,
                "season": "JJA" if i % 2 == 0 else "DJF",
                "era": "e1" if i < n_per // 2 else "e2",
                "f1": rng.normal(0, 1), "f2": rng.normal(0, 1),
                "f3": rng.normal(0, 1)})
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


def _redigest(payload):
    """Recompute the fixture's freeze chain after a mutation so only
    the named surface is exercised."""
    pre = {k: v for k, v in payload.items()
           if k not in ("regime_artifact_digest", "freeze_digest",
                        "frozen")}
    payload["regime_artifact_digest"] = sha256_canonical(pre)
    payload["freeze_digest"] = sha256_canonical(
        {k: v for k, v in payload.items()
         if k not in ("freeze_digest", "frozen")})
    return payload


class TestReg02CadenceGrid:
    def test_block_len_must_be_positive(self):
        df = _frame()
        cfg = _cfg(bootstrap_block_len=0)
        art = run_regimes(df, FEATURES, _mask(df), cfg)
        assert art["status"] == "RUN_ERROR"
        assert "bootstrap_block_len" in art["reason"]

    def test_negative_block_len_rejected(self):
        df = _frame()
        cfg = _cfg(bootstrap_block_len=-3)
        art = run_regimes(df, FEATURES, _mask(df), cfg)
        assert art["status"] == "RUN_ERROR"

    def test_finer_than_grid_cadence_rejected(self):
        """Declared 1H cadence over a daily grid fabricates the
        sampling rate — fail before fitting."""
        df = _frame()
        cfg = _cfg(cadence="1H")
        art = run_regimes(df, FEATURES, _mask(df), cfg)
        assert art["status"] == "RUN_ERROR"
        assert "cadence" in art["reason"]

    def test_off_grid_spacing_rejected(self):
        """3-day spacings are not integer multiples of a declared
        2-day cadence — the rows fall off the declared grid."""
        df = _frame(day_step=3)
        cfg = _cfg(cadence="2D")
        art = run_regimes(df, FEATURES, _mask(df), cfg)
        assert art["status"] == "RUN_ERROR"
        assert "grid" in art["reason"] or "cadence" in art["reason"]

    def test_coarser_than_declared_cadence_rejected(self):
        """A declared daily cadence over a 3-day-observed grid is a
        false sampling claim."""
        df = _frame(day_step=3)
        cfg = _cfg(cadence="1D")
        art = run_regimes(df, FEATURES, _mask(df), cfg)
        assert art["status"] == "RUN_ERROR"
        assert "cadence" in art["reason"] or "grid" in art["reason"]


class TestReg01UnitBasinPartition:
    def test_unit_in_two_groups_rejected(self):
        df = _frame()
        # move a slice of cell0's rows into grp1 — the unit now
        # straddles the LORO boundary
        idx = df.index[(df["unit_id"] == "cell0")][:30]
        df.loc[idx, "basin_group"] = "grp1"
        cfg = _cfg()
        art = run_regimes(df, FEATURES, _mask(df), cfg)
        assert art["status"] == "RUN_ERROR"
        assert "partition" in art["reason"]


class TestReg04EffortSplit:
    def test_invalid_effort_split_rejected(self):
        df = _frame()
        cfg = _cfg(effort_split="bogus-policy")
        art = run_regimes(df, FEATURES, _mask(df), cfg)
        assert art["status"] == "RUN_ERROR"
        assert "effort_split" in art["reason"]


class TestProv03EvidenceRoot:
    def test_nonfixture_manifest_needs_evidence_root(self):
        df = _frame()
        sm = {"source_id": "real_src", "units": {"f1": "mm"},
              "source_digests": ["a" * 64],
              "feature_allowlist": FEATURES,
              "lineage": {"fetch": "test"}}
        cfg = _cfg(source_manifest=sm)
        art = run_regimes(df, FEATURES, _mask(df), cfg)
        assert art["status"] == "RUN_ERROR"
        assert "evidence_root" in art["reason"]

    def test_audit_flags_missing_evidence_root(self):
        payload = fx4.planted_artifact_payload(fx4.make_events(21))
        payload["source_manifest"] = {
            "source_id": "real_src", "units": {"f1": "mm"},
            "source_digests": ["a" * 64],
            "feature_allowlist": FEATURES,
            "lineage": {"fetch": "test"}}
        _redigest(payload)
        findings = audit_producer_payload(payload)
        assert any("evidence_root" in f.detail
                   for f in findings)


class TestGateUniverseShared:
    def test_missing_gate_blocks_adapter(self):
        payload = fx4.planted_artifact_payload(fx4.make_events(21))
        payload["stability"]["required_gates"].pop("loro")
        _redigest(payload)
        with pytest.raises(ValueError, match="gate universe|required_gates"):
            regime_assignment_from_artifact(payload, artifact_id="t1")

    def test_extra_gate_blocks_adapter(self):
        payload = fx4.planted_artifact_payload(fx4.make_events(21))
        payload["stability"]["required_gates"]["bogus_gate"] = True
        _redigest(payload)
        with pytest.raises(ValueError, match="gate universe|required_gates"):
            regime_assignment_from_artifact(payload, artifact_id="t1")

    def test_audit_flags_missing_and_extra_gates(self):
        payload = fx4.planted_artifact_payload(fx4.make_events(21))
        gates = payload["stability"]["required_gates"]
        gates.pop("temporal_bootstrap")
        gates["injected"] = True
        payload["stability_report_digest"] = sha256_canonical(
            payload["stability"])
        _redigest(payload)
        findings = audit_producer_payload(payload)
        msgs = "; ".join(f.detail for f in findings)
        assert "temporal_bootstrap" in msgs
        assert "injected" in msgs

    def test_freeze_rejects_nonuniverse_gates(self):
        """A hand-assembled artifact reaching freeze with an
        off-universe gate map fails closed — every other bound
        digest recomputes honestly so only the gate surface is
        exercised."""
        assignments = [["u1", "2020-01-01", 0],
                       ["u1", "2020-01-02", 1]]
        assignment_digest = sha256_canonical(assignments)
        vals = [[1.0, 2.0], [3.0, 4.0]]
        import numpy as _np
        raw = _np.asarray(vals, dtype=_np.float64)
        import hashlib
        input_bytes_digest = hashlib.sha256(
            raw.tobytes()).hexdigest()
        fm_digest = semantic_feature_matrix_digest(vals)
        gates = {g: True for g in REQUIRED_REGIME_GATE_NAMES}
        gates["forged_extra"] = True
        seeds = [1, 2, 3]
        ubm = [["u1", "g1"]]
        train_keys = [row_key(u, d) for u, d, _ in assignments]
        row_universe_digest = sorted_row_key_digest(train_keys)
        k1_bic = [410.5, 420.25, 430.75]
        null_fams = {}
        for fam in ("shuffled", "season_matched"):
            rec = {"statistic": "silhouette", "observed": 0.6,
                   "n_replicates": 2, "n_succeeded": 2,
                   "n_failed": 0, "p_value": 0.02, "alpha": 0.05,
                   "status": "PASS", "reason": None,
                   "selection": "bic_sweep_declared_candidates",
                   "null_k_distribution": {"1": 2},
                   "null_stat_min": 0.05, "null_stat_max": 0.15,
                   "replicates": [
                       {"i": i,
                        "gen_seed": seeds[0] + 1000003 * (i + 1),
                        "fit_seed": seeds[i % len(seeds)], "k": 1,
                        "stat": s, "ok": True}
                       for i, s in enumerate((0.05, 0.15))]}
            rec["family_digest"] = sha256_canonical({
                "family": fam, "seed_cycle": list(seeds),
                "n_replicates": rec["n_replicates"],
                "statistic": rec["statistic"],
                "p_value": rec["p_value"],
                "observed": rec.get("observed"),
                "alpha": rec.get("alpha"),
                "n_succeeded": rec.get("n_succeeded"),
                "n_failed": rec.get("n_failed"),
                "status": rec.get("status"),
                "reason": rec.get("reason"),
                "selection": rec.get("selection"),
                "null_stat_min": rec["null_stat_min"],
                "null_stat_max": rec["null_stat_max"],
                "null_k_distribution":
                    rec["null_k_distribution"],
                "replicates": rec["replicates"]})
            null_fams[fam] = rec
        fit_partition = {
            "record_type": "fit_partition/v0",
            "train_groups": ["g1"], "heldout_groups": ["g_holdout"],
            "n_train_rows": len(train_keys), "n_rows": 2,
            "train_row_keys_digest": sorted_row_key_digest(
                train_keys),
            "cutoff_iso": "2020-01-01",
            "feature_matrix_digest": fm_digest,
            "feature_cols": ["f1", "f2"]}
        run_manifest = {
            "record_type": "RunManifestV0",
            "run_id": "r5-test-001", "worker_id": "test",
            "created_at": "2020-01-02T00:00:00Z",
            "environment_digest": "b" * 64, "seed": seeds[0],
            "input_digests": [input_bytes_digest],
            "output_digests": [assignment_digest],
            "checkpoint_policy": "atomic_publish_or_quarantine",
            "status": "COMPLETED"}
        art = {
            "status": "DESCRIPTIVE_REGIME_ONLY",
            "mode": "RETROSPECTIVE_REGIME",
            "data_class": "REANALYSIS",
            "forecast_vintage_digests": [],
            "forecast_feature_set": [],
            "label_blinding": True,
            "fitted_on": "TRAIN_ONLY",
            "k": 1,
            "seeds": seeds,
            "seeds_declared": seeds,
            "seed_coverage": {str(s): "converged" for s in seeds},
            "per_seed_best_k": {str(s): 1 for s in seeds},
            "modal_k_frequency": 1.0,
            "occupancy": [1.0],
            "model": {"weights": [1.0],
                      "means": [[1.5, 3.0]],
                      "covariances": [[[0.25, 0.0], [0.0, 0.25]]]},
            "feature_cols": ["f1", "f2"],
            "feature_matrix_digest": fm_digest,
            "assignments": assignments,
            "config": {"seeds": [1, 2, 3], "cadence": "1D",
                       "gap_policy": "calendar", "bootstrap_block_len": 7,
                       "k_candidates": [1, 2, 3],
                       "missingness_policy": "listwise",
                       "effort_split": "median",
                       "mode": "RETROSPECTIVE_REGIME",
                       "train_groups": ["g1"],
                       "heldout_groups": ["g_holdout"],
                       "forecast_feature_set": [],
                       "forecast_vintage_digests": [],
                       "source_manifest": {"fixture": True}},
            "input_values": vals,
            "input_schema": {"feature_cols": ["f1", "f2"],
                             "n_rows": 2,
                             "dtypes": {"f1": "float64",
                                        "f2": "float64"},
                             "shape": [2, 2]},
            "preprocessing": {
                "row_keys_digest": row_universe_digest},
            "stability": {"required_gates": gates},
            "nulls": {"statistic": "silhouette", "observed": 0.6,
                      "alpha": 0.05, "n_replicates": 2,
                      "season_era_stratified": False,
                      "k1_bic": k1_bic,
                      "shuffled": null_fams["shuffled"],
                      "season_matched":
                          null_fams["season_matched"]},
            "fit_groups": ["g1"],
            "heldout_groups_declared": ["g_holdout"],
            "fit_partition": fit_partition,
            "n_train_rows": len(train_keys),
            "n_rows": 2,
            "train_mask_digest": "0" * 64,
            "k_selection_digest": "1" * 64,
            "null_model_digest": sha256_canonical({
                "k1_bic": k1_bic,
                "null_families": {
                    fam: rec["family_digest"]
                    for fam, rec in null_fams.items()}}),
            "unit_basin_map": ubm,
            "unit_basin_map_digest": sha256_canonical(
                canonical_unit_basin_pairs(ubm)),
            "run_manifest": run_manifest,
            "environment_digest": "b" * 64,
            "source_manifest": {"fixture": True},
            "missingness_applied": {"policy": "listwise",
                                    "train_rows_total": 2,
                                    "train_rows_fitted": 2,
                                    "train_rows_dropped": 0},
            "terminal": True,
            "associable": True,
            "disclaimer": "synthetic r5 artifact",
        }
        art["assignment_digest"] = assignment_digest
        art["config_digest"] = sha256_canonical(art["config"])
        art["input_bytes_digest"] = input_bytes_digest
        art["preprocessing_digest"] = sha256_canonical(
            art["preprocessing"])
        art["run_manifest_digest"] = sha256_canonical(
            art["run_manifest"])
        art["fit_partition_digest"] = sha256_canonical(
            fit_partition)
        art["stability_report_digest"] = sha256_canonical(
            art["stability"])
        art["regime_artifact_digest"] = sha256_canonical(
            {k: v for k, v in art.items()
             if k != "regime_artifact_digest"})
        with pytest.raises(ValueError, match="gate universe|extra"):
            freeze_regime_artifact(art)


class TestReg05NullReplicateEvidence:
    def test_null_envelope_carries_replicates(self):
        rng = np.random.default_rng(0)

        def gen(i, seed):
            r = np.random.default_rng(seed)
            return r.normal(0, 1, size=(60, 2))

        rec = _null_envelope(0.9, gen, 2, [1, 2, 3],
                             n_replicates=5, alpha=0.05)
        reps = rec["replicates"]
        assert len(reps) == 5
        for i, r in enumerate(reps):
            assert r["i"] == i and "gen_seed" in r and \
                "fit_seed" in r and "ok" in r


def _eval_kwargs(design):
    cases = design["cases"]
    return {"holdout": design["holdout"],
            "baseline_probs": fx3.make_baseline_probs(cases),
            "admitted_vintages": fx3.admitted_for_cases(cases),
            "opportunities": design.get(
                "opportunities", fx3.opportunity_registry(cases)),
            "unit_basins": design.get(
                "unit_basins", fx3.unit_basins_for(cases)),
            "region_basins": design.get(
                "region_basins",
                fx3.region_basins_for(
                    sorted({c.region for c in cases}))),
            "n_boot": 30, "seed": 7}


class TestEvalLane:
    def test_degradation_never_empty(self):
        design = fx3.underpowered_design()
        report = evaluate(design["cases"], **_eval_kwargs(design))
        assert report.degradation["scenarios"]

    def test_declared_scenarios_execute(self):
        design = fx3.underpowered_design()
        kw = _eval_kwargs(design)
        kw["degradation_scenarios"] = [
            {"name": "half", "drop_fraction": 0.5}]
        report = evaluate(design["cases"], **kw)
        assert [s["name"] for s in
                report.degradation["scenarios"]] == ["half"]

    def test_uncertainty_clusters_by_event_group(self):
        design = fx3.underpowered_design()
        report = evaluate(design["cases"], **_eval_kwargs(design))
        assert report.uncertainty["cluster_key"] == \
            "event_group_id_else_unit+season"

    def test_fixture_only_cannot_emit_forecast_status(self):
        design = fx3.powered_design()
        report = evaluate(design["cases"], **_eval_kwargs(design))
        assert report.power["powered"] is True
        assert report.status == "UNDERPOWERED_DESCRIPTIVE_ONLY"
