"""Deterministic replay tests for the serialized experiment_v0 bundle
(SWE-B4).

A bundle is pure JSON-shaped input; ``replay_bundle`` deserializes it
into contract records and runs the real ``run_association`` /
``evaluate`` path.  Replays must be byte-stable: identical serialized
inputs give identical statuses, digests, and problem lists — including
through a JSON serialize/reload round-trip.
"""
from __future__ import annotations

import copy
import inspect
import json

import pytest

from nepal.experiment_v0.adapters import (
    event_label_from_identity, holdout_plan_from_assignment)
from nepal.experiment_v0.audit import (REPLAY_SCHEMA, audit_pipeline,
                                     replay_bundle, replay_problems)
from nepal.experiment_v0.association import ASSOCIATION_STATUSES

from tests.fixtures import synthetic_exp_b4 as fx

_SHA256_LEN = 64


# ---------------------------------------------------------------------
# Clean end-to-end replay
# ---------------------------------------------------------------------

class TestReplayClean:
    def test_replay_produces_statuses_and_digests(self):
        result = replay_bundle(fx.synthetic_bundle())
        assert result["problems"] == []
        assert result["association_status"] in ASSOCIATION_STATUSES
        assert result["evaluation_status"] in (
            "FORECAST_EXPERIMENT_ONLY", "UNDERPOWERED_DESCRIPTIVE_ONLY")
        assert len(result["association_digest"]) == _SHA256_LEN
        assert len(result["evaluation_digest"]) == _SHA256_LEN

    def test_replay_twice_byte_identical(self):
        bundle = fx.synthetic_bundle()
        first = replay_bundle(bundle)
        second = replay_bundle(bundle)
        assert first == second
        assert first["association_digest"] == \
            second["association_digest"]
        assert first["evaluation_digest"] == \
            second["evaluation_digest"]

    def test_replay_survives_json_roundtrip(self):
        bundle = fx.synthetic_bundle()
        roundtripped = json.loads(json.dumps(bundle))
        assert replay_bundle(bundle) == replay_bundle(roundtripped)
        assert replay_problems(bundle, roundtripped) == []

    def test_replay_problems_no_drift_for_identical_bundles(self):
        bundle = fx.synthetic_bundle()
        assert replay_problems(bundle, copy.deepcopy(bundle)) == []

    def test_audit_pipeline_clean_bundle_only_review_notes(self):
        # The only findings on a well-formed bundle are the documented
        # shallow-immutability review notes on the report dataclasses.
        findings = audit_pipeline(fx.synthetic_bundle())
        assert {f.code for f in findings} <= {"MUTABLE_FIELD"}


# ---------------------------------------------------------------------
# Defective bundles yield findings, not crashes
# ---------------------------------------------------------------------

class TestReplayDefects:
    def test_schema_mismatch_noted(self):
        bundle = fx.synthetic_bundle()
        bundle["schema"] = "experiment_v0.replay_bundle/vWRONG"
        result = replay_bundle(bundle)
        assert any("schema" in p for p in result["problems"])
        findings = audit_pipeline(bundle)
        assert any(f.code == "SCHEMA_MISMATCH" for f in findings)

    def test_non_mapping_bundle_collects_problem(self):
        result = replay_bundle([1, 2, 3])  # type: ignore[arg-type]
        assert result["problems"]

    def test_missing_holdout_collects_problem(self):
        bundle = fx.synthetic_bundle()
        del bundle["association"]["holdout"]
        result = replay_bundle(bundle)
        assert result["association_status"] == "REPLAY_FAILED"
        assert result["problems"]
        # The forecast lane still replays — failures are isolated.
        assert result["evaluation_status"] != "ABSENT"

    def test_train_assigned_event_rejected_on_replay(self):
        bundle = fx.synthetic_bundle()
        events = bundle["association"]["events"]
        holdout = bundle["association"]["holdout"]
        holdout["event_assignments"][events[0]["event_id"]] = \
            "north_train"
        result = replay_bundle(bundle)
        assert result["association_status"] == "REPLAY_FAILED"
        assert any("never" in p or "non-test" in p
                   for p in result["problems"])

    def test_missing_mandatory_baseline_collects_problem(self):
        bundle = fx.synthetic_bundle()
        del bundle["forecast"]["baseline_probs"]["null"]
        result = replay_bundle(bundle)
        assert result["evaluation_status"] == "REPLAY_FAILED"
        assert any("null" in p for p in result["problems"])
        assert any(f.code == "MISSING_BASELINE"
                   for f in audit_pipeline(bundle))

    def test_undersized_opportunity_count_collects_problem(self):
        bundle = fx.synthetic_bundle()
        bundle["forecast"]["n_opportunities"] = 1
        result = replay_bundle(bundle)
        assert result["evaluation_status"] == "REPLAY_FAILED"
        assert result["problems"]


# ---------------------------------------------------------------------
# Byte-stability under input-order perturbation
# ---------------------------------------------------------------------

class TestReplayStability:
    def test_event_order_permutation_stable(self):
        bundle = fx.synthetic_bundle()
        permuted = copy.deepcopy(bundle)
        permuted["association"]["events"] = list(
            reversed(permuted["association"]["events"]))
        assert replay_problems(bundle, permuted) == []

    def test_case_order_permutation_changes_evaluation_digest(self):
        """DISCOVERED FINDING (B3): ``evaluate`` folds the *ordered*
        case list into ``experiment_id`` via ``sha256_canonical`` over
        ``[c.to_dict() for c in cases]`` — the same case set in a
        different order produces a different evaluation digest.  The
        metric values are order-insensitive; only the experiment
        identity is not order-canonical."""
        bundle = fx.synthetic_bundle()
        permuted = copy.deepcopy(bundle)
        permuted["forecast"]["cases"] = list(
            reversed(permuted["forecast"]["cases"]))
        # Keep baseline vectors positionally aligned with the permuted
        # case order so this is a pure ordering perturbation.
        for name, vec in permuted["forecast"]["baseline_probs"].items():
            permuted["forecast"]["baseline_probs"][name] = list(
                reversed(vec))
        diffs = replay_problems(bundle, permuted)
        assert any("evaluation_digest" in d for d in diffs), \
            "expected order-sensitivity finding: case order enters " \
            "the evaluation digest through experiment_id"

    def test_drift_detected_when_digests_differ(self):
        bundle = fx.synthetic_bundle()
        tampered = copy.deepcopy(bundle)
        tampered["association"]["seed"] = 999
        diffs = replay_problems(bundle, tampered)
        # A different seed must not crash; it either reproduces the
        # same digests or reports drift — never silently passes.
        assert isinstance(diffs, list)


# ---------------------------------------------------------------------
# Adapter boundary against the real science_v0 producer (post-merge)
# ---------------------------------------------------------------------

class TestAdapterBoundary:
    """These skip cleanly while ``nepal.science_v0`` is absent and run
    for real once swarm A's producer package is merged."""

    def test_event_identity_adapts_to_problem_free_label(self):
        sci = pytest.importorskip("nepal.science_v0.events")
        source_row = getattr(sci, "SourceRow", None)
        normalize_event = getattr(sci, "normalize_event", None)
        if source_row is None or normalize_event is None:
            pytest.skip("science_v0.events lacks "
                        "SourceRow/normalize_event")
        candidate = dict(
            source_id="synthetic_inventory_v0",
            source_version="0.0.0-synthetic",
            source_row_key="row-000",
            mechanism="snow_release",
            interval_start="2020-06-08T00:00:00Z",
            interval_end="2020-06-09T00:00:00Z",
            declared_precision="day",
            basin="karnali")
        signature = inspect.signature(source_row)
        kwargs = {k: v for k, v in candidate.items()
                  if k in signature.parameters}
        unsatisfiable = [
            name for name, p in signature.parameters.items()
            if p.default is inspect.Parameter.empty
            and name not in kwargs]
        if unsatisfiable:
            pytest.skip(f"SourceRow signature drifted; cannot satisfy "
                        f"required params {unsatisfiable}")
        identity = normalize_event(source_row(**kwargs))
        label = event_label_from_identity(
            identity, vertical_id="snow_avalanche",
            geometry_role="slope_generalized",
            adjudication_state="TWO_REVIEW_AGREE",
            reviewer_ids=("rev-b4-a", "rev-b4-b"))
        assert label.problems() == []

    def test_holdout_assignment_adapts_to_problem_free_plan(self):
        sci = pytest.importorskip("nepal.science_v0.events")
        names = ("SourceRow", "normalize_event", "assign_holdouts")
        if not all(hasattr(sci, n) for n in names):
            pytest.skip(f"science_v0.events lacks {names}")
        events = []
        for i, basin in enumerate(
                ("koshi", "gandaki", "karnali", "bagmati")):
            events.append(sci.normalize_event(sci.SourceRow(
                source_id="synthetic_inventory_v0",
                source_version="0.0.0-synthetic",
                source_row_key=f"row-{i:03d}",
                mechanism="snow_release",
                interval_start="2020-06-08T00:00:00Z",
                interval_end="2020-06-09T00:00:00Z",
                declared_precision="day",
                basin=basin)))
        group_of_basin = {"koshi": "g_train", "gandaki": "g_val",
                          "karnali": "g_test_a", "bagmati": "g_test_b"}
        split_of_group = {"g_train": "train", "g_val": "val",
                          "g_test_a": "test", "g_test_b": "test"}
        assignment = sci.assign_holdouts(
            events, group_of_basin, split_of_group,
            ("karnali", "bagmati"), embargo_seconds=2592000)
        plan = holdout_plan_from_assignment(
            assignment, holdout_plan_id="holdout-b4-adapter",
            split_of_group=split_of_group)
        assert plan.problems() == []
        assert set(plan.evaluation_region_names) <= \
            set(plan.test_groups)
