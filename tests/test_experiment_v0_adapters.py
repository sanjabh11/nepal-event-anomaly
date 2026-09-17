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
from nepal.research_v0.gates import scan_claims_text
from nepal.research_v0.records import EventLabelV0, HoldoutPlanV0

from tests.fixtures.synthetic_exp_b2 import synthetic_vintage_request


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
    preprocessing = {"imputer_strategy": "median",
                     "imputer_statistics": [0.0, 0.0],
                     "scaler_mean": [0.0, 0.0],
                     "scaler_var": [1.0, 1.0],
                     "feature_order": ["f1", "f2"],
                     "row_keys_digest": "e" * 64,
                     "train_mask_membership_digest": "f" * 64}
    fit_partition = {
        "record_type": "fit_partition/v0",
        "train_groups": ["g0", "g1", "g2"],
        "heldout_groups": ["g3"],
        "n_train_rows": 2,
        "n_rows": 3,
        "train_row_keys_digest": "1" * 64,
        "cutoff_iso": "2020-06-02",
        "feature_matrix_digest": "b" * 64,
        "feature_cols": ["f1", "f2"]}
    run_manifest = {
        "run_id": "adapter-test-run-001",
        "worker_id": "test-worker",
        "created_at": "2020-06-02T00:00:00Z",
        "environment_digest": "2" * 64,
        "seed": 7,
        "input_digests": ["3" * 64],
        "output_digests": ["4" * 64],
        "checkpoint_policy": "atomic_publish_or_quarantine",
        "status": "COMPLETED"}
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
        "assignment_digest": _canon(assignments),
        "feature_cols": ["f1", "f2"],
        "feature_matrix_digest": "b" * 64,
        "input_bytes_digest": "5" * 64,
        "input_schema": {"feature_cols": ["f1", "f2"], "n_rows": 3,
                         "dtypes": {"f1": "float64", "f2": "float64"},
                         "shape": [3, 2]},
        "model": {"weights": [0.6, 0.4],
                  "means": [[1.0, 2.0], [4.0, 5.0]],
                  "covariances": [[[0.25, 0.0], [0.0, 0.25]],
                                  [[0.5, 0.0], [0.0, 0.5]]]},
        "config_digest": "c" * 64,
        "fit_groups": ["g0", "g1", "g2"],
        "heldout_groups_declared": ["g3"],
        "fit_partition": fit_partition,
        "unit_basin_map": [["u1", "g0"], ["u2", "g1"]],
        "run_manifest": run_manifest,
        "run_manifest_digest": _canon(run_manifest),
        "n_train_rows": 2,
        "n_rows": 3,
        "train_mask_digest": "d" * 64,
        "occupancy": [0.6, 0.4],
        "stability": stability,
        "nulls": {"shuffled_js": 0.31, "season_matched_js": 0.008},
        "preprocessing": preprocessing,
        "k_selection_digest": "6" * 64,
        "null_model_digest": "7" * 64,
        "status": "DESCRIPTIVE_REGIME_ONLY",
        "terminal": True,
        "associable": True,
        "source_manifest": {"fixture": True},
        "missingness_applied": {"policy": "listwise",
                                "train_rows_total": 3,
                                "train_rows_fitted": 3,
                                "train_rows_dropped": 0},
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
    with pytest.raises(ValueError, match="strings"):
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
    keep every other binding valid."""
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
        with pytest.raises(ValueError, match="required_gates"):
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
