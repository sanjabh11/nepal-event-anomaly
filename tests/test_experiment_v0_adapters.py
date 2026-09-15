"""Canonical serialized-boundary tests for experiment_v0.adapters.

These pin the swarm A -> contract-record mapping: explicit required
keys, no duck typing, strict rejection of missing/unknown/wrongly-typed
fields, and produced records that pass problems().
"""
from __future__ import annotations

import pytest

from nepal.experiment_v0.adapters import (
    event_label_from_identity, holdout_plan_from_assignment,
    vintage_from_request)
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


def _assignment_payload(**overrides):
    """A science_v0.HoldoutAssignment-shaped serialized mapping.

    Three groups across four basins; every group has >=1 event; two
    test groups supply the named evaluation regions.
    """
    p = {
        "assignments": {"e-train": "train", "e-val": "val",
                        "e-test-a": "test", "e-test-b": "test"},
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
        _assignment_payload(), holdout_plan_id="holdout-adapter-0")
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
    plan = holdout_plan_from_assignment(p, holdout_plan_id="h0")
    assert plan.problems() == []


def test_assignment_basin_in_two_groups_rejects():
    p = _assignment_payload()
    p["basin_groups"] = {"g_train": ("koshi",), "g_val": ("gandaki",),
                         "g_test_a": ("karnali", "bagmati"),
                         "g_test_b": ("bagmati",)}
    with pytest.raises(ValueError, match="multiple groups"):
        holdout_plan_from_assignment(p, holdout_plan_id="h0")


def test_assignment_event_without_basin_rejects():
    p = _assignment_payload()
    del p["basin_of_event"]["e-test-a"]
    with pytest.raises(ValueError, match="basin"):
        holdout_plan_from_assignment(p, holdout_plan_id="h0")


def test_assignment_eventless_group_rejects():
    p = _assignment_payload()
    p["basin_groups"]["g_empty"] = ("mahakali",)
    with pytest.raises(ValueError, match="no member events"):
        holdout_plan_from_assignment(p, holdout_plan_id="h0")


def test_assignment_split_disagreement_rejects():
    p = _assignment_payload()
    # Two member events of one group claim different splits.
    p["basin_groups"]["g_test_a"] = ("karnali", "bagmati")
    p["basin_groups"].pop("g_test_b")
    p["assignments"]["e-test-b"] = "val"
    with pytest.raises(ValueError, match="disagree"):
        holdout_plan_from_assignment(p, holdout_plan_id="h0")


def test_assignment_invalid_split_rejects():
    p = _assignment_payload()
    p["assignments"]["e-train"] = "holdout"
    with pytest.raises(ValueError, match="invalid splits"):
        holdout_plan_from_assignment(p, holdout_plan_id="h0")


def test_assignment_eval_region_outside_test_surfaces_problems():
    p = _assignment_payload(evaluation_regions=("karnali", "koshi"))
    with pytest.raises(ValueError, match="problems"):
        holdout_plan_from_assignment(p, holdout_plan_id="h0")


def test_assignment_nonfinite_embargo_rejects():
    p = _assignment_payload(embargo_seconds="false")
    with pytest.raises(ValueError, match="embargo_seconds"):
        holdout_plan_from_assignment(p, holdout_plan_id="h0")


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
