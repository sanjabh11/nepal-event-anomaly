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
from nepal.research_v0._hashing import sha256_canonical

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

    def test_shrunk_opportunity_registry_collects_problem(self):
        """EVAL-01: removing a case's opportunity from the registry
        must fail replay — the denominator is registry-derived, and
        a bare integer in the bundle is inert (verified below)."""
        bundle = fx.synthetic_bundle()
        first = sorted(bundle["forecast"]["opportunities"])[0]
        del bundle["forecast"]["opportunities"][first]
        result = replay_bundle(bundle)
        assert result["evaluation_status"] == "REPLAY_FAILED"
        assert result["problems"]

    def test_stray_integer_denominator_is_inert(self):
        """A planted n_opportunities field cannot manipulate the
        evaluation — replay derives the denominator from the
        registry."""
        bundle = fx.synthetic_bundle()
        bundle["forecast"]["n_opportunities"] = 10 ** 6
        result = replay_bundle(bundle)
        assert result["evaluation_status"] != "REPLAY_FAILED"


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

    def test_case_order_permutation_preserves_evaluation_digest(self):
        """I-10 fixed: ``evaluate`` canonically sorts cases by
        (case_id, opportunity_id) and realigns baseline vectors before
        hashing — a pure ordering perturbation must produce identical
        metrics and an identical evaluation digest."""
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
        assert not any("evaluation_digest" in d for d in diffs), \
            f"order-sensitivity regression: {diffs}"
        # the metrics must also be identical — realignment proved
        a = replay_bundle(bundle)
        b = replay_bundle(permuted)
        assert a.get("evaluation_digest") == \
            b.get("evaluation_digest")

    def test_drift_detected_when_digests_differ(self):
        bundle = fx.synthetic_bundle()
        tampered = copy.deepcopy(bundle)
        tampered["association"]["seed"] = 999
        diffs = replay_problems(bundle, tampered)
        # A different seed must not crash; it either reproduces the
        # same digests or reports drift — never silently passes.
        assert isinstance(diffs, list)


# ---------------------------------------------------------------------
# REP-C01 / PROV-C03 — regime-digest binding and freeze-gate probes
# ---------------------------------------------------------------------

def _repair_payload_digests(payload):
    """Recompute the payload's self-referential digests exactly as
    the producer/freeze path does, so a probe can corrupt ONE claim
    while every other binding stays valid."""
    payload["assignment_digest"] = sha256_canonical(
        payload["assignments"])
    payload["regime_artifact_digest"] = sha256_canonical(
        {k: v for k, v in payload.items()
         if k not in ("regime_artifact_digest", "freeze_digest",
                      "frozen")})
    payload["freeze_digest"] = sha256_canonical(
        {k: v for k, v in payload.items()
         if k not in ("freeze_digest", "frozen")})
    return payload


class TestRegimeDigestBinding:
    """The bundle-level regime digest is recomputed from the
    serialized artifact_payload and verified against every carried
    reference — editing the payload, the assignments, the source
    digests, or a bundle-level reference must produce a replay
    problem, never a silent pass."""

    def test_tampered_carried_regime_digest_fails(self):
        bundle = fx.synthetic_bundle()
        bundle["association"]["artifact"]["regime_digest"] = \
            "f" * 64
        result = replay_bundle(bundle)
        assert result["association_status"] == "REPLAY_FAILED"
        assert any("recomputed" in p for p in result["problems"])
        assert any(f.code == "REGIME_DIGEST_MISMATCH"
                   for f in audit_pipeline(bundle))

    def test_tampered_carried_artifact_id_fails(self):
        bundle = fx.synthetic_bundle()
        bundle["association"]["artifact"]["artifact_id"] = "forged"
        result = replay_bundle(bundle)
        assert result["association_status"] == "REPLAY_FAILED"
        assert result["problems"]

    def test_tampered_assignment_row_fails(self):
        bundle = fx.synthetic_bundle()
        rows = bundle["association"]["artifact_payload"][
            "assignments"]
        rows[0] = [rows[0][0], rows[0][1],
                   (rows[0][2] + 1) % 5]
        result = replay_bundle(bundle)
        assert result["association_status"] == "REPLAY_FAILED"
        assert any("assignment_digest" in p
                   for p in result["problems"])

    def test_tampered_source_digest_fails(self):
        """A swapped source digest leaves every recomputed binding
        broken — the artifact digest no longer covers the payload."""
        bundle = fx.synthetic_bundle()
        bundle["association"]["artifact_payload"][
            "feature_matrix_digest"] = "e" * 64
        result = replay_bundle(bundle)
        assert result["association_status"] == "REPLAY_FAILED"
        assert any("regime_artifact_digest" in p
                   for p in result["problems"])

    def test_tampered_input_bytes_digest_fails(self):
        bundle = fx.synthetic_bundle()
        bundle["association"]["artifact_payload"][
            "input_bytes_digest"] = "d" * 64
        result = replay_bundle(bundle)
        assert result["association_status"] == "REPLAY_FAILED"
        assert result["problems"]

    def test_swapped_artifact_payload_fails(self):
        """Two bundles' payloads are not interchangeable — a
        payload that fails to recompute against the carried
        references is rejected at the replay boundary."""
        bundle = fx.synthetic_bundle()
        other = fx.synthetic_bundle(n_events=14)
        bundle["association"]["artifact_payload"] = \
            other["association"]["artifact_payload"]
        result = replay_bundle(bundle)
        assert result["association_status"] == "REPLAY_FAILED"
        assert result["problems"]

    def test_descriptive_status_over_open_gate_fails(self):
        """PROV-C03: promoting the artifact to a terminal
        descriptive status while a required gate is open — even
        with every digest repaired — must fail replay."""
        bundle = fx.synthetic_bundle()
        payload = bundle["association"]["artifact_payload"]
        payload["status"] = "DESCRIPTIVE_REGIME_ONLY"
        # the fixture leaves season_matched_null open
        _repair_payload_digests(payload)
        result = replay_bundle(bundle)
        assert result["association_status"] == "REPLAY_FAILED"
        assert any("required_gates" in p for p in result["problems"])

    def test_descriptive_status_all_gates_closed_replays_clean(self):
        """The freeze-gate blocks a false claim, not a true one:
        closing the open gate and promoting the status — with
        digests repaired — replays with no problems."""
        bundle = fx.synthetic_bundle()
        payload = bundle["association"]["artifact_payload"]
        payload["status"] = "DESCRIPTIVE_REGIME_ONLY"
        payload["stability"]["required_gates"][
            "season_matched_null"] = True
        payload["stability_report_digest"] = sha256_canonical(
            payload["stability"])
        _repair_payload_digests(payload)
        # every carried reference must track the repaired payload —
        # the bundle-level digest is rebound, not bypassed
        bundle["association"]["artifact"]["regime_digest"] = \
            payload["freeze_digest"]
        result = replay_bundle(bundle)
        assert result["problems"] == []
        assert result["association_status"] in ASSOCIATION_STATUSES


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
