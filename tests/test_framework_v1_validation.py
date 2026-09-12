"""Phase E validation tests: Wilson, as-of-event, group LOO no-leakage,
INDETERMINATE-default policy, UNOBSERVABLE classification, null E briefing."""
import pytest

from nepal.framework_v1 import contract as C
from nepal.framework_v1.validation import (wilson_interval, as_of_event_valid,
                                           classify_event_observability,
                                           PercentileNormalizer, run_validation,
                                           write_validation_artifact,
                                           verify_validation_artifact)
from nepal.framework_v1.controls import ControlsConfig, create_controls_lock
from nepal.framework_v1.briefing import generate_briefing
from nepal.framework_v1.provenance import (bind_artifact_envelope,
                                            bind_gate_artifact,
                                            sha256_canonical)
from nepal.framework_v1.validation import evaluate_e_gate
from nepal.framework_v1.input_manifest import InputManifestVerification

CONFIG = ControlsConfig(min_detectable_size_m3=4.0e6)
LOCK = create_controls_lock(CONFIG)
PLAN = {"groups": [{"group_id": "G1"}, {"group_id": "G2"},
                   {"group_id": "G3"}],
        "n_groups": 3, "plan_sha256": "0" * 64}


def _event(eid, group, score, date_s="2020-06-01", volume=9.0e6,
           availability="AVAILABLE", feature_avail=None):
    return {"event_id": eid, "group": group, "score": score,
            "event_date_min": date_s, "volume_m3": volume,
            "observation_availability": availability,
            "feature_available_from": feature_avail or
            {"insar": "2020-05-01"}}


def _control(uid, group, score, coverage="FULL"):
    return {"unit_id": uid, "group": group, "score": score,
            "observation_coverage": coverage}


class TestWilson:
    def test_reference_8_of_10(self):
        lo, hi = wilson_interval(8, 10)
        assert abs(lo - 0.4902) < 5e-4 and abs(hi - 0.9433) < 5e-4

    def test_interval_brackets_point_estimate(self):
        lo, hi = wilson_interval(7, 20)
        assert lo < 7 / 20 < hi

    def test_small_sample_wide_interval(self):
        lo, hi = wilson_interval(1, 2)
        assert hi - lo > 0.4

    def test_zero_wins_lower_bound_zero(self):
        lo, hi = wilson_interval(0, 10)
        assert abs(lo) < 1e-12 and hi < 0.31

    def test_perfect_wins_upper_below_one(self):
        lo, hi = wilson_interval(10, 10)
        assert lo > 0.72 and hi < 1.0

    def test_undefined_for_n_zero(self):
        assert wilson_interval(0, 0) is None


class TestAsOfEvent:
    def test_feature_before_event_valid(self):
        assert as_of_event_valid({"insar": "2020-05-01"}, "2020-06-01") == []

    def test_feature_after_event_flagged(self):
        assert as_of_event_valid({"insar": "2020-07-01"}, "2020-06-01") == \
            ["insar:available_after_event(2020-07-01)"]

    def test_feature_on_event_date_ok(self):
        assert as_of_event_valid({"dem": "2020-06-01"}, "2020-06-01") == []

    def test_unparseable_or_malformed_flagged(self):
        assert as_of_event_valid({"dem": "not-a-date"}, "2020-06-01") == \
            ["dem:unparseable_availability"]
        assert as_of_event_valid({"dem": "2020-13-40"}, "2020-06-01") == \
            ["dem:unparseable_availability"]

    def test_unresolved_event_date(self):
        assert as_of_event_valid({}, "") == ["event_date_unresolved"]


class TestObservability:
    def test_fully_observed_event(self):
        cls = classify_event_observability(_event("E1", "G1", 0.8),
                                           CONFIG.to_dict(), ())
        assert cls["observable"] is True and cls["reasons"] == []

    def test_below_detectable_or_missing_volume_unobservable(self):
        for volume in (1.0e6, None):
            cls = classify_event_observability(
                _event("E1", "G1", 0.8, volume=volume), CONFIG.to_dict(), ())
            assert cls["observable"] is False
            assert "BELOW_MIN_DETECTABLE_SIZE" in cls["reasons"]

    def test_unknown_observation_availability_unobservable(self):
        cls = classify_event_observability(
            _event("E1", "G1", 0.8, availability="UNKNOWN"),
            CONFIG.to_dict(), ())
        assert "OBSERVATION_NOT_ADEQUATE:UNKNOWN" in cls["reasons"]

    def test_feature_after_event_unobservable(self):
        cls = classify_event_observability(
            _event("E1", "G1", 0.8, feature_avail={"insar": "2020-08-01"}),
            CONFIG.to_dict(), ())
        assert "FEATURE_NOT_AVAILABLE_AS_OF_EVENT:insar" in cls["reasons"]


class TestNormalizer:
    def test_fit_train_apply_holdout_unchanged(self):
        norm = PercentileNormalizer([0.1, 0.2, 0.3, 0.4])
        assert norm.transform(0.05) == 0.0      # below all training values
        assert norm.transform(0.15) == 0.25
        assert norm.transform(0.25) == 0.5
        assert norm.transform(0.35) == 0.75
        assert norm.transform(0.9) == 1.0

    def test_fit_signature_deterministic(self):
        assert PercentileNormalizer([0.3, 0.1, 0.2]).fit_sha256 == \
            PercentileNormalizer([0.1, 0.2, 0.3]).fit_sha256

    def test_empty_fit_transforms_to_none(self):
        assert PercentileNormalizer([]).transform(0.5) is None


class TestLogo:
    def _events_controls(self, controls_score=0.3):
        events = [_event("E1", "G1", 0.9, date_s="2020-06-01"),
                  _event("E2", "G2", 0.8, date_s="2021-06-01"),
                  _event("E3", "G3", 0.7, date_s="2022-06-01")]
        controls = [_control("C1", "G1", controls_score),
                    _control("C2", "G2", controls_score),
                    _control("C3", "G3", controls_score)]
        return events, controls

    def test_no_leakage_held_out_group_not_in_fit(self):
        events, controls = self._events_controls()
        summary = run_validation(events, controls, controls_lock=LOCK,
                                 holdout_plan=PLAN, min_pairwise_n=1)
        for fold in summary["folds"]:
            assert fold["group"] not in fold["fit_groups"]
            assert fold["held_out_group_was_used_in_fit"] is False

    def test_locked_controls_required(self):
        from nepal.framework_v1.controls import ControlsLock, \
            ControlsLockMismatch
        events, controls = self._events_controls()
        doc = create_controls_lock(CONFIG).to_dict()
        # Simulate post-freeze modification of the locked controls.
        doc["controls"]["min_detectable_size_m3"] = 123.0
        with pytest.raises(ControlsLockMismatch):
            run_validation(events, controls,
                           controls_lock=ControlsLock.from_dict(doc),
                           holdout_plan=PLAN, min_pairwise_n=1)

    def test_all_pairs_counted(self):
        events, controls = self._events_controls()
        summary = run_validation(events, controls, controls_lock=LOCK,
                                 holdout_plan=PLAN, min_pairwise_n=1)
        # 3 folds x (1 held-out event x 1 held-out control) = 3 pairs
        assert summary["n_pairs_total"] == 3

    def test_unknown_coverage_control_excluded_and_flagged(self):
        events, controls = self._events_controls()
        controls[1]["observation_coverage"] = "UNKNOWN"
        summary = run_validation(events, controls, controls_lock=LOCK,
                                 holdout_plan=PLAN, min_pairwise_n=1)
        assert summary["excluded_controls"]["C2"]["reason"] == \
            "coverage:UNKNOWN"
        assert summary["n_pairs_total"] == 2
class TestDecisionPolicy:
    def _events_controls(self, event_score=0.9, controls_score=0.3, n=3):
        events = [_event(f"E{i}", f"G{i}", event_score) for i in range(1, n + 1)]
        controls = [_control(f"C{i}", f"G{i}", controls_score)
                    for i in range(1, n + 1)]
        return events, controls

    def test_default_indeterminate_small_sample(self):
        events, controls = self._events_controls()
        summary = run_validation(events, controls, controls_lock=LOCK,
                                 holdout_plan=PLAN, min_pairwise_n=30)
        assert summary["status"] == "INDETERMINATE"
        assert summary["small_sample"] is True

    def test_indeterminate_when_interval_spans_half(self):
        # Mixed folds: two winning groups, one losing group -> 4/6 win rate
        # whose Wilson interval spans 0.5.
        events = [_event("E1", "G1", 0.9), _event("E2", "G2", 0.1),
                  _event("E3", "G3", 0.9)]
        controls = [_control("C1", "G1", 0.2), _control("C2", "G2", 0.8),
                    _control("C3", "G3", 0.2)]
        summary = run_validation(events, controls, controls_lock=LOCK,
                                 holdout_plan=PLAN, min_pairwise_n=1)
        assert summary["status"] == "INDETERMINATE"
        assert summary["wilson95"][0] < 0.5 < summary["wilson95"][1]

    def test_null_when_below_chance_excluding_half(self):
        # 4 groups x (1 event x 1 control) = 4 pairs, 0 wins:
        # wilson(0, 4) upper bound < 0.5 -> NULL.
        events = [_event(f"E{i}", f"G{i}", 0.1) for i in range(1, 5)]
        controls = [_control(f"C{i}", f"G{i}", 0.9) for i in range(1, 5)]
        plan = {"groups": [{"group_id": f"G{i}"} for i in range(1, 5)],
                "n_groups": 4, "plan_sha256": "0" * 64}
        summary = run_validation(events, controls, controls_lock=LOCK,
                                 holdout_plan=plan, min_pairwise_n=1)
        assert summary["status"] == "NULL"
        assert summary["wilson95"][1] < 0.5

    def test_pass_when_above_chance_with_enough_pairs(self):
        # 4 groups x 4 events x 5 controls = 80 pairs, all wins.
        events = [_event(f"E{i}", f"G{i}", 0.95) for i in range(1, 5)]
        controls = [_control(f"C{i}{j}", f"G{i}", 0.05)
                    for i in range(1, 5) for j in range(5)]
        plan = {"groups": [{"group_id": f"G{i}"} for i in range(1, 5)],
                "n_groups": 4, "plan_sha256": "0" * 64}
        summary = run_validation(events, controls, controls_lock=LOCK,
                                 holdout_plan=plan, min_pairwise_n=20)
        assert summary["status"] == "PASS"
        assert summary["wilson95"][0] > 0.5

    def test_incomplete_holdout_plan_forces_indeterminate(self):
        events, controls = self._events_controls(n=2)  # G3 has no events
        summary = run_validation(events, controls, controls_lock=LOCK,
                                 holdout_plan=PLAN, min_pairwise_n=1)
        assert summary["incomplete_holdout"] is True
        assert summary["planned_groups_not_evaluated"] == ["G3"]
        assert summary["status"] == "INDETERMINATE"

    def test_no_tp60_threshold_and_rules_recorded(self):
        events, controls = self._events_controls()
        rules = run_validation(events, controls, controls_lock=LOCK,
                               holdout_plan=PLAN,
                               min_pairwise_n=1)["decision_rules"]
        assert rules["no_tp_gt_60_percent_threshold"] is True
        assert rules["indeterminate_default"] is True
        assert rules["no_random_cross_validation"] is True
        assert rules["null_is_publishable"] is True

    def test_hashes_committed(self):
        events, controls = self._events_controls()
        hashes = run_validation(events, controls, controls_lock=LOCK,
                                holdout_plan=PLAN,
                                min_pairwise_n=1)["input_hashes"]
        assert all(len(v) == 64 for v in hashes.values())

    def test_indeterminate_rows_retained(self):
        events = [_event("E1", "G1", 0.9, availability="UNKNOWN"),
                  _event("E2", "G2", 0.8)]
        controls = [_control("C1", "G1", 0.2), _control("C2", "G2", 0.2)]
        plan = {"groups": [{"group_id": "G1"}, {"group_id": "G2"}],
                "n_groups": 2, "plan_sha256": "0" * 64}
        summary = run_validation(events, controls, controls_lock=LOCK,
                                 holdout_plan=plan, min_pairwise_n=1)
        assert summary["n_unobservable"] == 1
        assert "E1" in summary["unobservable_reasons"]
        assert summary["n_events_total"] == 2

    def test_deterministic_summary(self):
        events, controls = self._events_controls()
        s1 = run_validation(events, controls, controls_lock=LOCK,
                            holdout_plan=PLAN, min_pairwise_n=1)
        s2 = run_validation(events, controls, controls_lock=LOCK,
                            holdout_plan=PLAN, min_pairwise_n=1)
        assert s1 == s2


class TestNullStillProducesBriefing:
    def _null_summary(self):
        events = [_event(f"E{i}", f"G{i}", 0.1) for i in range(1, 5)]
        controls = [_control(f"C{i}", f"G{i}", 0.9) for i in range(1, 5)]
        plan = {"groups": [{"group_id": f"G{i}"} for i in range(1, 5)],
                "n_groups": 4, "plan_sha256": "0" * 64}
        summary = run_validation(events, controls, controls_lock=LOCK,
                                 holdout_plan=plan, min_pairwise_n=1)
        assert summary["status"] == "NULL"
        return summary

    def test_null_briefing_has_required_sections(self):
        text = generate_briefing(self._null_summary(),
                                 contract_hash=C.contract_hash())
        assert "NULL" in text
        assert "NOT an evacuation map" in text
        assert "early-warning" in text
        assert "not a prediction" in text.lower()
        assert "publishable" in text
        assert "Liability" in text
        assert "Maintenance" in text
        for p in ("Pathway 1", "Pathway 2", "Pathway 3"):
            assert p in text
        assert "Transboundary" in text

    def test_incomplete_summary_still_briefed(self):
        events = [_event("E1", "G1", 0.9)]
        controls = [_control("C1", "G1", 0.2)]
        summary = run_validation(events, controls, controls_lock=LOCK,
                                 holdout_plan=PLAN, min_pairwise_n=30)
        assert "INDETERMINATE" in generate_briefing(summary)

    def test_briefing_deterministic(self):
        summary = {"status": "INDETERMINATE", "input_hashes": {}}
        assert generate_briefing(summary) == generate_briefing(summary)
        assert as_of_event_valid({}, "") == ["event_date_unresolved"]


class TestStrictValidationContract:
    def test_strict_validation_rejects_caller_gate_booleans_without_artifacts(self):
        plan = self._strict_plan(["G1"])
        summary = run_validation(
            [_event("E1", "G1", 0.9)], [_control("C1", "G1", 0.1)],
            controls_lock=LOCK, holdout_plan=plan,
            input_manifest=self._strict_manifest(),
            input_manifest_verification=self._strict_manifest_verification(),
            strict_contract=True, a_gate_passed=True, b_gate_passed=True,
            min_pairwise_n=1,
        )
        assert summary["status"] == "BLOCKED"
        assert any("gate artifact" in error.lower()
                   for error in summary["validation_errors"])

    def test_validation_artifact_binds_summary_and_gate(self, tmp_path):
        from nepal.framework_v1.provenance import (bind_artifact_envelope,
                                                   bind_gate_artifact)
        summary = {"status": "INDETERMINATE", "validation_errors": []}
        gate = bind_gate_artifact({"gate_id": "E_VALIDATION",
                                   "passed": False, "checks": {}})
        path = tmp_path / "e.json"
        artifact = write_validation_artifact(path, summary, gate)
        assert verify_validation_artifact(artifact) == (True, [])
        tampered = dict(artifact)
        tampered["summary"] = {"status": "PASS", "validation_errors": []}
        assert verify_validation_artifact(tampered)[0] is False

        tampered_gate = dict(artifact["gate"])
        tampered_gate["summary_sha256"] = "0" * 64
        tampered_gate = bind_gate_artifact(tampered_gate)
        tampered_envelope = dict(artifact)
        tampered_envelope["gate"] = tampered_gate
        tampered_envelope = bind_artifact_envelope(tampered_envelope)
        assert verify_validation_artifact(tampered_envelope)[0] is False

    def _strict_plan(self, groups):
        plan = {"groups": [{"group_id": group} for group in groups],
                "n_groups": len(groups),
                "frozen_before_eligibility_filtering": True}
        plan["plan_sha256"] = sha256_canonical(plan)
        return plan

    def _strict_manifest(self):
        manifest = {"schema_version": "2.0.0-reconciled",
                    "contract_sha256": C.contract_hash(),
                    "artifacts": []}
        from nepal.framework_v1.input_manifest import canonical_input_manifest_hash
        manifest["artifact_count"] = 0
        manifest["manifest_sha256"] = canonical_input_manifest_hash(manifest)
        return manifest

    def _strict_manifest_verification(self):
        from nepal.framework_v1.input_manifest import canonical_input_manifest_hash
        manifest = self._strict_manifest()
        return InputManifestVerification(
            ok=True,
            can_run_primary=True,
            checks={
                "self_hash_verified": True,
                "canonical_manifest_authorized": True,
                "manifest_sha256": canonical_input_manifest_hash(manifest),
                "contract_bound": True,
                "framework_contract_bound": True,
                "raw_slc_scan": "PASS",
            })

    def test_strict_path_uses_one_to_one_pairs(self):
        events = [_event("E1", "G1", 0.9), _event("E2", "G2", 0.8)]
        controls = [_control("C1", "G1", 0.1), _control("C2", "G2", 0.2)]
        a_gate = bind_gate_artifact({
            "gate_id": C.GateId.A_CATALOG.value,
            "passed": True,
            "checks": {},
        })
        b_gate = bind_artifact_envelope({
            "gate": bind_gate_artifact({
                "gate_id": C.GateId.B_TO_C.value,
                "passed": True,
                "checks": {},
            }),
            "provenance": {"framework_contract_sha256": C.contract_hash()},
        })
        summary = run_validation(
            events, controls, controls_lock=LOCK,
            holdout_plan=self._strict_plan(["G1", "G2"]),
            feature_config={"b_screen_sha256": "a" * 64},
            input_manifest=self._strict_manifest(),
            input_manifest_verification=self._strict_manifest_verification(),
            strict_contract=True, a_gate_artifact=a_gate,
            b_gate_artifact=b_gate,
            min_pairwise_n=1,
        )
        assert summary["status"] in {"PASS", "NULL", "INDETERMINATE"}
        assert summary["matching_policy"] == "one_to_one_deterministic"
        assert summary["n_pairs_total"] == 2
        assert summary["n_ties_total"] == 1

    def test_strict_path_blocks_tampered_holdout_plan(self):
        events = [_event("E1", "G1", 0.9)]
        controls = [_control("C1", "G1", 0.1)]
        plan = self._strict_plan(["G1"])
        plan["groups"].append({"group_id": "G2"})
        summary = run_validation(
            events, controls, controls_lock=LOCK, holdout_plan=plan,
            input_manifest=self._strict_manifest(), strict_contract=True,
            a_gate_passed=True, b_gate_passed=True, min_pairwise_n=1,
        )
        assert summary["status"] == "BLOCKED"
        assert any("holdout_plan_sha256" in error
                   for error in summary["validation_errors"])

    def test_strict_path_requires_manifest_verification(self):
        events = [_event("E1", "G1", 0.9)]
        controls = [_control("C1", "G1", 0.1)]
        summary = run_validation(
            events, controls, controls_lock=LOCK,
            holdout_plan=self._strict_plan(["G1"]),
            input_manifest=self._strict_manifest(), strict_contract=True,
            a_gate_passed=True, b_gate_passed=True, min_pairwise_n=1,
        )
        assert summary["status"] == "BLOCKED"
        assert any("manifest verification" in error
                   for error in summary["validation_errors"])

    def test_strict_path_rejects_records_outside_frozen_plan(self):
        events = [_event("E1", "G2", 0.9)]
        controls = [_control("C1", "G2", 0.1)]
        summary = run_validation(
            events, controls, controls_lock=LOCK,
            holdout_plan=self._strict_plan(["G1"]),
            input_manifest=self._strict_manifest(),
            input_manifest_verification=self._strict_manifest_verification(),
            strict_contract=True, a_gate_passed=True, b_gate_passed=True,
            min_pairwise_n=1,
        )
        assert summary["status"] == "BLOCKED"
        assert any("outside" in error
                   for error in summary["validation_errors"])

    def test_strict_path_rejects_verification_for_different_manifest(self):
        verification = InputManifestVerification(
            ok=True, can_run_primary=True,
            checks={"self_hash_verified": True, "manifest_sha256": "b" * 64,
                    "contract_bound": True, "framework_contract_bound": True,
                    "raw_slc_scan": "PASS"})
        summary = run_validation(
            [_event("E1", "G1", 0.9)], [_control("C1", "G1", 0.1)],
            controls_lock=LOCK, holdout_plan=self._strict_plan(["G1"]),
            input_manifest=self._strict_manifest(),
            input_manifest_verification=verification,
            strict_contract=True, a_gate_passed=True, b_gate_passed=True,
            min_pairwise_n=1,
        )
        assert summary["status"] == "BLOCKED"
        assert any("does not match the input manifest" in error
                   for error in summary["validation_errors"])

    def test_strict_path_rejects_forged_verification_mapping(self):
        verification = self._strict_manifest_verification().to_dict()
        summary = run_validation(
            [_event("E1", "G1", 0.9)], [_control("C1", "G1", 0.1)],
            controls_lock=LOCK, holdout_plan=self._strict_plan(["G1"]),
            input_manifest=self._strict_manifest(),
            input_manifest_verification=verification,
            strict_contract=True, a_gate_passed=True, b_gate_passed=True,
            min_pairwise_n=1,
        )
        assert summary["status"] == "BLOCKED"
        assert any("typed" in error.lower() for error in summary["validation_errors"])

    def test_strict_path_rejects_missing_record_ids(self):
        event = _event("E1", "G1", 0.9)
        event.pop("event_id")
        summary = run_validation(
            [event], [_control("C1", "G1", 0.1)],
            controls_lock=LOCK, holdout_plan=self._strict_plan(["G1"]),
            input_manifest=self._strict_manifest(),
            input_manifest_verification=self._strict_manifest_verification(),
            strict_contract=True, a_gate_passed=True, b_gate_passed=True,
            min_pairwise_n=1,
        )
        assert summary["status"] == "BLOCKED"
        assert any("event_id" in error for error in summary["validation_errors"])

    def test_required_feature_missing_is_unobservable(self):
        event = _event("E1", "G1", 0.9)
        event["feature_available_from"] = {}
        cls = classify_event_observability(event, CONFIG.to_dict(), ["insar"])
        assert cls["observable"] is False
        assert "FEATURE_AVAILABILITY_UNKNOWN:insar" in cls["reasons"]

    def test_e_gate_accepts_honest_indeterminate_only_with_bound_evidence(self):
        from nepal.framework_v1.provenance import (bind_artifact_envelope,
                                                    sha256_canonical)
        from nepal.framework_v1.input_manifest import canonical_input_manifest_hash
        plan = self._strict_plan(["G1"])
        manifest = self._strict_manifest()
        manifest_hash = canonical_input_manifest_hash(manifest)
        summary = {"status": "INDETERMINATE",
                   "input_hashes": {
                       "catalog": "a" * 64, "controls": "b" * 64,
                       "feature_config": "c" * 64,
                       "holdout_plan": plan["plan_sha256"],
                       "input_manifest": manifest_hash,
                       "controls_lock": LOCK.sha256},
                   "validation_errors": []}
        verification = self._strict_manifest_verification()
        a_gate = {"gate_id": "A_CATALOG", "passed": True,
                  "catalog_sha256": summary["input_hashes"]["catalog"],
                  "holdout_plan_sha256": plan["plan_sha256"]}
        a_gate["gate_artifact_sha256"] = sha256_canonical(a_gate)
        b_gate = {"gate": {"gate_id": "B_TO_C", "passed": True},
                  "provenance": {
                      "input_manifest_sha256": manifest_hash,
                      "framework_contract_sha256": C.contract_hash(),
                      "input_manifest_framework_runtime_bound": True,
                      "input_manifest_framework_contract_bound": True,
                  }}
        b_gate["gate"]["gate_artifact_sha256"] = sha256_canonical(b_gate["gate"])
        b_gate = bind_artifact_envelope(b_gate)
        gate = evaluate_e_gate(
            summary, a_gate_artifact=a_gate, b_gate_artifact=b_gate,
            controls_lock=LOCK, holdout_plan=plan,
            input_manifest_verification=verification)
        assert gate["passed"] is True

    def test_e_gate_rejects_b_envelope_for_different_framework_contract(self):
        from nepal.framework_v1.input_manifest import canonical_input_manifest_hash
        from nepal.framework_v1.provenance import bind_artifact_envelope
        plan = self._strict_plan(["G1"])
        manifest = self._strict_manifest()
        manifest_hash = canonical_input_manifest_hash(manifest)
        summary = {"status": "INDETERMINATE",
                   "input_hashes": {
                       "catalog": "a" * 64, "controls": "b" * 64,
                       "feature_config": "c" * 64,
                       "holdout_plan": plan["plan_sha256"],
                       "input_manifest": manifest_hash,
                       "controls_lock": LOCK.sha256},
                   "validation_errors": []}
        verification = self._strict_manifest_verification()
        a_gate = {"gate_id": "A_CATALOG", "passed": True,
                  "catalog_sha256": summary["input_hashes"]["catalog"],
                  "holdout_plan_sha256": plan["plan_sha256"]}
        a_gate["gate_artifact_sha256"] = sha256_canonical(a_gate)
        b_gate = {
            "gate": {"gate_id": "B_TO_C", "passed": True},
            "provenance": {
                "input_manifest_sha256": manifest_hash,
                "framework_contract_sha256": "0" * 64,
            },
        }
        b_gate["gate"]["gate_artifact_sha256"] = sha256_canonical(b_gate["gate"])
        b_gate = bind_artifact_envelope(b_gate)
        gate = evaluate_e_gate(
            summary, a_gate_artifact=a_gate, b_gate_artifact=b_gate,
            controls_lock=LOCK, holdout_plan=plan,
            input_manifest_verification=verification)
        assert gate["passed"] is False
        assert gate["checks"]["B_framework_contract_matches_runtime"]["passed"] is False

    def test_e_gate_rejects_caller_supplied_booleans(self):
        summary = {"status": "INDETERMINATE", "input_hashes": {},
                   "validation_errors": []}
        gate = evaluate_e_gate(summary, a_gate_passed=True,
                               b_gate_passed=True,
                               input_manifest_verified=True)
        assert gate["passed"] is False
