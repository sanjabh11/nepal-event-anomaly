"""Canonical serialized-boundary tests for experiment_v0.adapters.

These pin the swarm A -> contract-record mapping: explicit required
keys, no duck typing, strict rejection of missing/unknown/wrongly-typed
fields, and produced records that pass problems().
"""
from __future__ import annotations

import pytest

from nepal.experiment_v0.adapters import (
    control_from_science, event_label_from_identity,
    holdout_plan_from_assignment, opportunity_from_science,
    regime_assignment_from_artifact, vintage_from_request)
from nepal.experiment_v0.vintages import VintageRequest
from nepal.research_v0._hashing import sha256_canonical
from nepal.research_v0.gates import scan_claims_text
from nepal.research_v0.producer_validation import (
    canonical_unit_basin_pairs, row_key,
    semantic_feature_matrix_digest, sorted_row_key_digest)
from nepal.research_v0.records import EventLabelV0, HoldoutPlanV0

from tests.fixtures.synthetic_exp_b2 import synthetic_vintage_request


def _null_family(family: str, seeds, stats) -> dict:
    """A producer-shaped serialized null-family record whose
    ``family_digest`` is computed exactly as ``run_regimes`` binds
    it: sha256_canonical over the documented material dict."""
    replicates = [
        {"i": i, "gen_seed": int(seeds[0]) + 1000003 * (i + 1),
         "fit_seed": int(seeds[i % len(seeds)]),
         "k": 2, "stat": s, "ok": True,
         "input_digest": sha256_canonical(f"{family}-rep-{i}")}
        for i, s in enumerate(stats)]
    rec = {"statistic": "silhouette", "observed": 0.6,
           "n_replicates": len(replicates),
           "n_succeeded": len(replicates), "n_failed": 0,
           "p_value": 0.02, "alpha": 0.05, "status": "PASS",
           "reason": None,
           "selection": "bic_sweep_declared_candidates",
           "null_k_distribution": {"2": len(replicates)},
           "null_stat_min": min(stats), "null_stat_max": max(stats),
           "replicates": replicates}
    rec["family_digest"] = sha256_canonical({
        "family": family, "seed_cycle": list(seeds),
        "n_replicates": rec["n_replicates"],
        "statistic": rec["statistic"], "p_value": rec["p_value"],
        "observed": rec.get("observed"),
        "alpha": rec.get("alpha"),
        "n_succeeded": rec.get("n_succeeded"),
        "n_failed": rec.get("n_failed"),
        "status": rec.get("status"),
        "reason": rec.get("reason"),
        "selection": rec.get("selection"),
        "null_stat_min": rec["null_stat_min"],
        "null_stat_max": rec["null_stat_max"],
        "null_k_distribution": rec["null_k_distribution"],
        "replicates": rec["replicates"]})
    return rec


def _identity_payload(**overrides):
    """A science_v0.EventIdentity-shaped serialized mapping."""
    p = {
        "event_id": "synthetic_inventory_v0:0.0.0-synthetic:row-000",
        "source_id": "synthetic_inventory_v0",
        "source_version": "0.0.0-synthetic",
        "mechanism": "snow_avalanche",
        "interval_start": "2020-06-08T00:00:00Z",
        "interval_end": "2020-06-09T00:00:00Z",
        "uncertainty_seconds": 86400,
        "timing_class": "EXACT_DAY",
        "basin": "karnali",
        "cascade_group_id": "solo:row-000",
        "parent_event_id": None,
        "duplicate_of": None,
        "timing_variants": ("2020-06-08T00:00:00Z",
                            "2020-06-08T12:00:00Z",
                            "2020-06-09T00:00:00Z"),
    }
    p.update(overrides)
    return p


_SPLIT_OF_GROUP = {"g_train": "train", "g_val": "val",
                   "g_test_a": "test", "g_test_b": "test"}


def _assignment_payload(**overrides):
    """A science_v0.HoldoutAssignment-shaped serialized mapping —
    ``assignments`` carry event -> geographic GROUP, matching the
    producer's ``assign_holdouts`` output.

    Four groups across four basins; every group has >=1 event; two
    test groups supply the named evaluation regions.
    """
    p = {
        "assignments": {"e-train": "g_train", "e-val": "g_val",
                        "e-test-a": "g_test_a", "e-test-b": "g_test_b"},
        "basin_of_event": {"e-train": "koshi", "e-val": "gandaki",
                           "e-test-a": "karnali", "e-test-b": "bagmati"},
        "basin_groups": {"g_train": ("koshi",), "g_val": ("gandaki",),
                         "g_test_a": ("karnali",),
                         "g_test_b": ("bagmati",)},
        "evaluation_regions": ("karnali", "bagmati"),
        "embargo_seconds": 2592000,
    }
    p.update(overrides)
    return p


# ------------------------------------------------------- event labels

def test_identity_adapts_to_problem_free_label():
    rec = event_label_from_identity(
        _identity_payload(), vertical_id="snow_avalanche",
        geometry_role="slope_generalized",
        adjudication_state="TWO_REVIEW_AGREE",
        reviewer_ids=("rev-a", "rev-b"))
    assert isinstance(rec, EventLabelV0)
    assert rec.problems() == []
    assert rec.basin_id == "karnali"
    assert rec.event_time_precision == "day"


def test_identity_missing_key_rejects():
    p = _identity_payload()
    del p["interval_start"]
    with pytest.raises(ValueError, match="missing required keys"):
        event_label_from_identity(p, vertical_id="snow_avalanche",
                                  geometry_role="slope_generalized")


def test_identity_unknown_key_rejects():
    p = _identity_payload(extra_field="x")
    with pytest.raises(ValueError, match="unknown keys"):
        event_label_from_identity(p, vertical_id="snow_avalanche",
                                  geometry_role="slope_generalized")


def test_identity_wrong_type_rejects():
    p = _identity_payload(uncertainty_seconds="false")
    with pytest.raises(ValueError, match="uncertainty_seconds"):
        event_label_from_identity(p, vertical_id="snow_avalanche",
                                  geometry_role="slope_generalized")


def test_identity_bad_timing_class_rejects():
    p = _identity_payload(timing_class="SOMETIME")
    with pytest.raises(ValueError, match="timing_class"):
        event_label_from_identity(p, vertical_id="snow_avalanche",
                                  geometry_role="slope_generalized")


def test_identity_non_mapping_rejects():
    with pytest.raises(TypeError):
        event_label_from_identity(42, vertical_id="snow_avalanche",
                                  geometry_role="slope_generalized")


def test_identity_adjudicated_needs_reviewers():
    with pytest.raises(ValueError, match="reviewer_ids"):
        event_label_from_identity(
            _identity_payload(), vertical_id="snow_avalanche",
            geometry_role="slope_generalized",
            adjudication_state="TWO_REVIEW_AGREE")


# --------------------------------------------------------- holdouts

def test_assignment_adapts_to_problem_free_plan():
    plan = holdout_plan_from_assignment(
        _assignment_payload(), holdout_plan_id="holdout-adapter-0",
        split_of_group=_SPLIT_OF_GROUP)
    assert isinstance(plan, HoldoutPlanV0)
    assert plan.problems() == []
    assert plan.train_groups == ("g_train",)
    assert plan.validation_groups == ("g_val",)
    assert plan.test_groups == ("g_test_a", "g_test_b")
    # basin-name evaluation regions map to their test groups
    assert plan.evaluation_region_names == ("g_test_a", "g_test_b")
    assert plan.event_assignments["e-test-a"] == "g_test_a"


def test_assignment_region_group_names_accepted():
    p = _assignment_payload(evaluation_regions=("g_test_a", "g_test_b"))
    plan = holdout_plan_from_assignment(p, holdout_plan_id="h0",
                                        split_of_group=_SPLIT_OF_GROUP)
    assert plan.problems() == []


def test_assignment_basin_in_two_groups_rejects():
    p = _assignment_payload()
    p["basin_groups"] = {"g_train": ("koshi",), "g_val": ("gandaki",),
                         "g_test_a": ("karnali", "bagmati"),
                         "g_test_b": ("bagmati",)}
    with pytest.raises(ValueError, match="multiple groups"):
        holdout_plan_from_assignment(p, holdout_plan_id="h0",
                                     split_of_group=_SPLIT_OF_GROUP)


def test_assignment_event_without_basin_rejects():
    p = _assignment_payload()
    del p["basin_of_event"]["e-test-a"]
    with pytest.raises(ValueError, match="basin"):
        holdout_plan_from_assignment(p, holdout_plan_id="h0",
                                     split_of_group=_SPLIT_OF_GROUP)


def test_assignment_event_basin_not_in_group_rejects():
    p = _assignment_payload()
    # e-test-a claims g_test_b but its basin (karnali) lives in
    # g_test_a — group membership is verified, not trusted.
    p["assignments"]["e-test-a"] = "g_test_b"
    with pytest.raises(ValueError, match="not a member"):
        holdout_plan_from_assignment(p, holdout_plan_id="h0",
                                     split_of_group=_SPLIT_OF_GROUP)


def test_assignment_undeclared_group_rejects():
    p = _assignment_payload()
    p["assignments"]["e-train"] = "g_ghost"
    with pytest.raises(ValueError, match="undeclared"):
        holdout_plan_from_assignment(p, holdout_plan_id="h0",
                                     split_of_group=_SPLIT_OF_GROUP)


def test_assignment_eventless_group_surfaces_problems():
    p = _assignment_payload()
    p["basin_groups"]["g_empty"] = ("mahakali",)
    splits = dict(_SPLIT_OF_GROUP, g_empty="test")
    with pytest.raises(ValueError, match="problems"):
        holdout_plan_from_assignment(p, holdout_plan_id="h0",
                                     split_of_group=splits)


def test_assignment_missing_split_rejects():
    splits = dict(_SPLIT_OF_GROUP)
    del splits["g_test_b"]
    with pytest.raises(ValueError, match="lack a split"):
        holdout_plan_from_assignment(
            _assignment_payload(), holdout_plan_id="h0",
            split_of_group=splits)


def test_assignment_invalid_split_rejects():
    splits = dict(_SPLIT_OF_GROUP, g_train="holdout")
    with pytest.raises(ValueError, match="not in"):
        holdout_plan_from_assignment(
            _assignment_payload(), holdout_plan_id="h0",
            split_of_group=splits)


def test_assignment_eval_region_outside_test_surfaces_problems():
    p = _assignment_payload(evaluation_regions=("karnali", "koshi"))
    with pytest.raises(ValueError, match="problems"):
        holdout_plan_from_assignment(p, holdout_plan_id="h0",
                                     split_of_group=_SPLIT_OF_GROUP)


def test_assignment_nonfinite_embargo_rejects():
    p = _assignment_payload(embargo_seconds="false")
    with pytest.raises(ValueError, match="embargo_seconds"):
        holdout_plan_from_assignment(p, holdout_plan_id="h0",
                                     split_of_group=_SPLIT_OF_GROUP)


# ---------------------------------------------------------- vintages

def test_vintage_from_request_instance():
    req = VintageRequest(**synthetic_vintage_request())
    v = vintage_from_request(req, vintage_id="v-tigge-0")
    assert v.vintage_id == "v-tigge-0"
    assert v.problems() == []


def test_vintage_from_serialized_mapping():
    v = vintage_from_request(synthetic_vintage_request(),
                             vintage_id="v-tigge-1")
    assert v.problems() == []


def test_vintage_from_bad_payload_rejects():
    p = synthetic_vintage_request(issue_time="")
    with pytest.raises(ValueError):
        vintage_from_request(p, vintage_id="v-bad")


def test_module_source_claim_scan_clean():
    import pathlib
    src = pathlib.Path(
        __file__).parents[1] / "nepal" / "experiment_v0" / "adapters.py"
    assert scan_claims_text(src.read_text()) == []


# ------------------------------------------------- regime artifacts


def _canon(obj):
    from nepal.research_v0._hashing import sha256_canonical
    return sha256_canonical(obj)


def _frozen_regime_payload(**overrides):
    """A frozen science_v0 regime-artifact payload with REAL digests —
    tamper tests mutate fields and expect digest verification to fail.

    Round-8: the adapter runs the shared producer provenance floor
    (``research_v0.producer_validation``), so the fixture carries the
    full canonical schema — model parameters, input schema, source
    manifest, unit->basin map, run manifest, and the typed
    fit_partition binding — not just the adapter's historical
    subset."""
    assignments = [("u1", "2020-06-01", 0), ("u1", "2020-06-02", 1),
                   ("u2", "2020-06-01", 0)]
    seeds = [7, 42, 2024]
    feature_cols = ["f1", "f2"]
    n_rows = len(assignments)
    unit_basin_map = [["u1", "g0"], ["u2", "g1"]]
    group_of = dict(unit_basin_map)
    fit_groups = ["g0", "g1"]
    heldout = ["g3"]
    train_keys = [row_key(u, d) for u, d, _ in assignments
                  if group_of[u] in fit_groups]
    n_train_rows = len(train_keys)
    row_universe_digest = sorted_row_key_digest(
        [row_key(u, d) for u, d, _ in assignments])
    input_values = [[0.1 * (i + 1), -0.05 * (i + 1)]
                    for i in range(n_rows)]
    import numpy as _np
    import hashlib as _hl
    input_bytes_digest = _hl.sha256(
        _np.ascontiguousarray(
            _np.asarray(input_values, dtype=_np.float64))
        .tobytes()).hexdigest()
    feature_matrix_digest = semantic_feature_matrix_digest(
        input_values)
    env_digest = "2" * 64
    assignment_digest = _canon(assignments)
    stability = {
        "required_gates": {
            "seed_policy": True, "modal_k_unanimous": True,
            "seed_ari": True, "seed_coverage": True,
            "loro": True, "temporal_bootstrap": True,
            "season_refits": True, "elevation": True,
            "missingness": True, "effort": True,
            "era_drift": True,
            "shuffled_null": True, "season_matched_null": True,
        },
    }
    config = {"seeds": list(seeds), "k_candidates": [1, 2, 3],
              "null_alpha": 0.05, "cadence": "1D",
              "gap_policy": "calendar", "bootstrap_block_len": 7,
              "missingness_policy": "listwise",
              "effort_split": "median",
              "mode": "RETROSPECTIVE_REGIME",
              "train_groups": list(fit_groups),
              "heldout_groups": list(heldout),
              "forecast_feature_set": [],
              "forecast_vintage_digests": [],
              "source_manifest": {"fixture": True}}
    preprocessing = {"imputer_strategy": "median",
                     "imputer_statistics": [0.0, 0.0],
                     "scaler_mean": [0.0, 0.0],
                     "scaler_var": [1.0, 1.0],
                     "feature_order": list(feature_cols),
                     "row_keys_digest": row_universe_digest,
                     "train_mask_membership_digest": "f" * 64}
    fit_partition = {
        "record_type": "fit_partition/v0",
        "train_groups": list(fit_groups),
        "heldout_groups": list(heldout),
        "n_train_rows": n_train_rows,
        "n_rows": n_rows,
        "train_row_keys_digest": sorted_row_key_digest(train_keys),
        "cutoff_iso": "2020-06-02",
        "feature_matrix_digest": feature_matrix_digest,
        "feature_cols": list(feature_cols)}
    run_manifest = {
        "record_type": "RunManifestV0",
        "run_id": "adapter-test-run-001",
        "worker_id": "test-worker",
        "created_at": "2020-06-02T00:00:00Z",
        "environment_digest": env_digest,
        "seed": seeds[0],
        "input_digests": [input_bytes_digest],
        "output_digests": [assignment_digest],
        "checkpoint_policy": "atomic_publish_or_quarantine",
        "status": "COMPLETED"}
    k1_bic = [1010.5, 1020.25, 1030.75]
    nulls = {"statistic": "silhouette", "observed": 0.6,
             "alpha": 0.05, "n_replicates": 4,
             "season_era_stratified": False,
             "k1_bic": k1_bic,
             "shuffled": _null_family(
                 "shuffled", seeds, [0.10, 0.20, 0.15, 0.05]),
             "season_matched": _null_family(
                 "season_matched", seeds,
                 [0.30, 0.25, 0.20, 0.10])}
    art = {
        "mode": "RETROSPECTIVE_REGIME",
        "data_class": "REANALYSIS",
        "fitted_on": "TRAIN_ONLY",
        "label_blinding": True,
        "k": 2,
        "seeds": seeds,
        "seeds_declared": seeds,
        "seed_coverage": {str(s): "converged" for s in seeds},
        "per_seed_best_k": {str(s): 2 for s in seeds},
        "modal_k_frequency": 1.0,
        "assignments": assignments,
        "assignment_digest": assignment_digest,
        "feature_cols": list(feature_cols),
        "feature_matrix_digest": feature_matrix_digest,
        "input_values": input_values,
        "input_bytes_digest": input_bytes_digest,
        "input_schema": {"feature_cols": list(feature_cols),
                         "n_rows": n_rows,
                         "dtypes": {c: "float64"
                                    for c in feature_cols},
                         "shape": [n_rows, len(feature_cols)]},
        "model": {"weights": [0.6, 0.4],
                  "means": [[1.0, 2.0], [4.0, 5.0]],
                  "covariances": [[[0.25, 0.0], [0.0, 0.25]],
                                  [[0.5, 0.0], [0.0, 0.5]]]},
        "config": config,
        "config_digest": _canon(config),
        "fit_groups": list(fit_groups),
        "heldout_groups_declared": list(heldout),
        "fit_partition": fit_partition,
        "unit_basin_map": unit_basin_map,
        "unit_basin_map_digest": _canon(
            canonical_unit_basin_pairs(unit_basin_map)),
        "run_manifest": run_manifest,
        "run_manifest_digest": _canon(run_manifest),
        "n_train_rows": n_train_rows,
        "n_rows": n_rows,
        "train_mask_digest": "d" * 64,
        "occupancy": [0.6, 0.4],
        "stability": stability,
        "nulls": nulls,
        "preprocessing": preprocessing,
        "k_selection_digest": "6" * 64,
        "null_model_digest": _canon({
            "k1_bic": k1_bic,
            "null_families": {
                "shuffled": nulls["shuffled"]["family_digest"],
                "season_matched":
                    nulls["season_matched"]["family_digest"]}}),
        "status": "DESCRIPTIVE_REGIME_ONLY",
        "terminal": True,
        "associable": True,
        "source_manifest": {"fixture": True},
        "missingness_applied": {"policy": "listwise",
                                "train_rows_total": n_train_rows,
                                "train_rows_fitted": n_train_rows,
                                "train_rows_dropped": 0},
        "environment_digest": env_digest,
        "disclaimer": "synthetic",
    }
    art["preprocessing_digest"] = _canon(preprocessing)
    art["fit_partition_digest"] = _canon(fit_partition)
    art["stability_report_digest"] = _canon(stability)
    art["regime_artifact_digest"] = _canon(art)
    # freeze_digest covers the PRE-freeze surface (producer semantics:
    # frozen flag is added after the digest is computed)
    art["freeze_digest"] = _canon(dict(art))
    art["frozen"] = True
    art.update(overrides)
    return art


def test_regime_artifact_adapts_cleanly():
    rec = regime_assignment_from_artifact(
        _frozen_regime_payload(), artifact_id="ra-0")
    assert rec.problems() == []
    assert rec.assignments[1] == ("u1", "2020-06-02", "1")
    assert rec.label_blinding is True


def test_regime_digest_parity_with_producer():
    """The local canonical digest must match the producer's digest
    construction — both are sha256_canonical over strict canonical
    JSON."""
    from nepal.science_v0.regimes import _digest as prod
    obj = {"b": [1, "x", None], "a": {"y": 2.5}}
    assert _canon(obj) == prod(obj)


def test_regime_forged_digest_rejected():
    p = _frozen_regime_payload(freeze_digest="a" * 64)
    with pytest.raises(ValueError, match="freeze_digest"):
        regime_assignment_from_artifact(p, artifact_id="x")


def test_regime_tampered_assignment_rejected():
    p = _frozen_regime_payload()
    p["assignments"][0] = ("u1", "2020-06-01", 9)
    with pytest.raises(ValueError, match="assignment_digest"):
        regime_assignment_from_artifact(p, artifact_id="x")


def test_regime_unfrozen_rejected():
    p = _frozen_regime_payload()
    p["frozen"] = False
    with pytest.raises(ValueError, match="frozen"):
        regime_assignment_from_artifact(p, artifact_id="x")


def test_regime_caller_claims_do_not_override():
    """Payload claiming label_blinding=False / fitted_on=ALL_DATA /
    mode=FORECAST must reject, not be silently corrected."""
    for field, bad in (("label_blinding", False),
                       ("fitted_on", "ALL_DATA"),
                       ("mode", "FORECAST")):
        p = _frozen_regime_payload()
        p[field] = bad
        # repair digests so ONLY the provenance claim is wrong
        p["regime_artifact_digest"] = _canon(
            {k: v for k, v in p.items()
             if k not in ("regime_artifact_digest", "freeze_digest",
                          "frozen")})
        p["freeze_digest"] = _canon(
            {k: v for k, v in p.items()
             if k not in ("freeze_digest", "frozen")})
        with pytest.raises(ValueError, match=field):
            regime_assignment_from_artifact(p, artifact_id="x")


def test_regime_provenance_field_missing_rejected():
    p = _frozen_regime_payload()
    del p["fitted_on"]
    with pytest.raises(ValueError, match="fitted_on"):
        regime_assignment_from_artifact(p, artifact_id="x")


def test_regime_nonstring_identity_rejected():
    p = _frozen_regime_payload()
    p["assignments"] = [(7, "2020-06-01", 0)]
    p["assignment_digest"] = _canon(p["assignments"])
    p["regime_artifact_digest"] = _canon(
        {k: v for k, v in p.items()
         if k not in ("regime_artifact_digest", "freeze_digest",
                      "frozen")})
    p["freeze_digest"] = _canon(
        {k: v for k, v in p.items()
         if k not in ("freeze_digest", "frozen")})
    with pytest.raises(ValueError, match="string"):
        regime_assignment_from_artifact(p, artifact_id="x")


def test_regime_bad_regime_id_type_rejected():
    p = _frozen_regime_payload()
    p["assignments"] = [("u1", "2020-06-01", 0.5)]
    p["assignment_digest"] = _canon(p["assignments"])
    p["regime_artifact_digest"] = _canon(
        {k: v for k, v in p.items()
         if k not in ("regime_artifact_digest", "freeze_digest",
                      "frozen")})
    p["freeze_digest"] = _canon(
        {k: v for k, v in p.items()
         if k not in ("freeze_digest", "frozen")})
    with pytest.raises(ValueError, match="regime_id"):
        regime_assignment_from_artifact(p, artifact_id="x")


def test_regime_post_freeze_nested_mutation_detected():
    p = _frozen_regime_payload()
    p["occupancy"] = [0.5, 0.5]   # mutate a nested field post-freeze
    with pytest.raises(ValueError, match="digest"):
        regime_assignment_from_artifact(p, artifact_id="x")


def test_regime_summary_only_rejected():
    with pytest.raises(ValueError, match="assignment"):
        regime_assignment_from_artifact(
            {"regime_artifact_digest": "a" * 64, "assignments": []},
            artifact_id="x")


def test_regime_run_error_status_rejected():
    p = _frozen_regime_payload(status="RUN_ERROR")
    with pytest.raises(ValueError, match="status"):
        regime_assignment_from_artifact(p, artifact_id="x")


def test_regime_unstable_status_rejected():
    p = _frozen_regime_payload(status="UNSUPERVISED_STRUCTURE_NOT_STABLE")
    with pytest.raises(ValueError):
        regime_assignment_from_artifact(p, artifact_id="x")


def test_regime_missing_i05_provenance_rejected():
    for field in ("feature_cols", "feature_matrix_digest",
                  "config_digest", "fit_groups",
                  "heldout_groups_declared", "train_mask_digest"):
        p = _frozen_regime_payload()
        del p[field]
        with pytest.raises(ValueError, match=field):
            regime_assignment_from_artifact(p, artifact_id="x")


def _repair_digests(p):
    """Recompute the self-referential digests exactly as the
    producer/freeze path does, so a test can corrupt ONE claim and
    keep every other binding valid.  Section digests are rebound
    from the (possibly mutated) section contents — the R9 floor
    recomputes ``stability_report_digest`` over the carried
    stability block, so an in-section mutation must re-bind it to
    isolate the intended problem."""
    if isinstance(p.get("stability"), dict):
        p["stability_report_digest"] = _canon(p["stability"])
    if isinstance(p.get("preprocessing"), dict):
        p["preprocessing_digest"] = _canon(p["preprocessing"])
    if isinstance(p.get("fit_partition"), dict):
        p["fit_partition_digest"] = _canon(p["fit_partition"])
    if isinstance(p.get("run_manifest"), dict):
        p["run_manifest_digest"] = _canon(p["run_manifest"])
    p["assignment_digest"] = _canon(p["assignments"])
    p["regime_artifact_digest"] = _canon(
        {k: v for k, v in p.items()
         if k not in ("regime_artifact_digest", "freeze_digest",
                      "frozen")})
    p["freeze_digest"] = _canon(
        {k: v for k, v in p.items()
         if k not in ("freeze_digest", "frozen")})
    return p


def test_regime_descriptive_over_open_gate_rejected():
    """PROV-C03: a frozen artifact whose required_gates are not all
    True cannot carry a terminal descriptive status — even with
    internally consistent digests."""
    p = _frozen_regime_payload()
    p["stability"]["required_gates"]["season_refits"] = False
    _repair_digests(p)
    with pytest.raises(ValueError, match="required_gates"):
        regime_assignment_from_artifact(p, artifact_id="x")


def test_regime_descriptive_without_gate_map_rejected():
    """PROV-C03: the descriptive status requires the flat gate map
    to be present — absent gate evidence is not closed gates."""
    for mutation in (
            lambda p: p.pop("stability"),
            lambda p: p["stability"].pop("required_gates"),
            lambda p: p["stability"].__setitem__(
                "required_gates", {})):
        p = _frozen_regime_payload()
        mutation(p)
        _repair_digests(p)
        with pytest.raises(ValueError, match="stability"):
            regime_assignment_from_artifact(p, artifact_id="x")


def test_regime_descriptive_with_nonbool_gate_rejected():
    """A non-boolean gate value is not ``True`` — the freeze-gate
    admits no tri-state verdicts."""
    p = _frozen_regime_payload()
    p["stability"]["required_gates"]["loro"] = "PASS"
    _repair_digests(p)
    with pytest.raises(ValueError, match="required_gates"):
        regime_assignment_from_artifact(p, artifact_id="x")


def test_regime_candidate_status_with_open_gate_adapts():
    """An honestly-demoted artifact — open evidence gate, candidate
    status — is NONASSOCIABLE (REG-13): only a fully-gated
    descriptive artifact may enter held-out association."""
    p = _frozen_regime_payload(status="CANDIDATE_ONLY")
    p["stability"]["required_gates"]["season_matched_null"] = False
    _repair_digests(p)
    try:
        regime_assignment_from_artifact(p, artifact_id="ra-cand")
        raise AssertionError("CANDIDATE_ONLY must not adapt")
    except ValueError as exc:
        assert "CANDIDATE_ONLY" in str(exc)


def test_holdout_asymmetric_universe_rejected():
    payload = _assignment_payload()
    payload["basin_of_event"]["ev-999"] = "orphan_basin"
    with pytest.raises(ValueError, match="same.*event universe"):
        holdout_plan_from_assignment(
            payload, holdout_plan_id="hp",
            split_of_group={"g_train": "train", "g_val": "validation",
                            "g_test_a": "test", "g_test_b": "test"})
