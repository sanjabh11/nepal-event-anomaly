"""Gate, envelope, and leakage tests for research_v0.

Approval bindings are file-bound: tests write real matrix/policy files
to tmp_path and hash them. Fabricated digests, symlinks, missing files,
blocker-carrying execution statuses, and invalid records all reject.
"""
from __future__ import annotations

import pytest

from nepal.research_v0 import gates
from nepal.research_v0._hashing import sha256_file
from nepal.research_v0.gates import (
    DesignApprovalError, build_claim_envelope, cascade_atomicity_problems,
    design_approval_problems, envelope_status_problems,
    forecast_feature_problems, occurrence_feature_problems,
    scan_claims_text)
from nepal.research_v0.records import (
    ControlWindowV0, CutoffRecordV0, EventLabelV0, ForecastExperimentV0,
    ForecastVintageV0, HazardVerticalSpecV0, HoldoutPlanV0,
    ObservationOpportunityV0, RegimeArtifactV0, ResearchClaimEnvelopeV0,
    SourceRecordV0)


@pytest.fixture
def doc_paths(tmp_path):
    matrix = tmp_path / "matrix.md"
    policy = tmp_path / "policy.md"
    matrix.write_text("decision matrix v0", encoding="utf-8")
    policy.write_text("cutoff policy v0", encoding="utf-8")
    return matrix, policy


def _approval(doc_paths, **overrides):
    matrix, policy = doc_paths
    binding = {
        "matrix_path": str(matrix),
        "policy_path": str(policy),
        "matrix_sha256": sha256_file(matrix),
        "policy_sha256": sha256_file(policy),
        "source_review_date": "2026-09-13",
        "selected_pilot_rule": "first vertical passing all gates; "
                               "NO_QUALIFYING_PILOT_SOURCE if none",
        "unresolved_blockers": [],
        "human_approved": True,
        "approved_by": "principal-investigator",
        "approved_at": "2026-09-13T00:00:00Z",
    }
    binding.update(overrides)
    return binding


def _clean_record():
    return SourceRecordV0(source_id="s1", provider="p",
                          doi_or_url="https://example.org/x")


class TestDesignApproval:
    def test_complete_file_bound_approval_passes(self, doc_paths):
        assert design_approval_problems(_approval(doc_paths)) == []

    def test_missing_fields(self):
        problems = design_approval_problems({})
        assert len(problems) >= len(gates.APPROVAL_REQUIRED_FIELDS)

    def test_bare_hex_without_files_rejected(self, doc_paths):
        problems = design_approval_problems(_approval(
            doc_paths, matrix_path="/nonexistent/matrix.md"))
        assert any("matrix_path" in p for p in problems)

    def test_digest_mismatch_rejected(self, doc_paths, tmp_path):
        other = tmp_path / "other.md"
        other.write_text("different bytes", encoding="utf-8")
        problems = design_approval_problems(_approval(
            doc_paths, matrix_sha256=sha256_file(other)))
        assert any("digest mismatch" in p for p in problems)

    def test_symlink_path_rejected(self, doc_paths, tmp_path):
        matrix, _ = doc_paths
        link = tmp_path / "link.md"
        link.symlink_to(matrix)
        problems = design_approval_problems(_approval(
            doc_paths, matrix_path=str(link)))
        assert any("symlink" in p for p in problems)

    def test_no_approval_no_envelope(self):
        with pytest.raises(DesignApprovalError):
            build_claim_envelope("env-1", "INTAKE_COMPLETE", {},
                                 {"human_approved": False})


class TestEnvelope:
    def test_envelope_flags_and_digests(self, doc_paths):
        env = build_claim_envelope(
            "env-1", "SOURCE_MATRIX_COMPLETE", {"src": _clean_record()},
            _approval(doc_paths))
        assert env["research_diagnostic_only"] is True
        assert env["claim_scope"] == \
            "research_only_no_operational_authorization"
        assert env["promotion_eligible"] is False
        assert env["production_authorized"] is False
        assert env["warning_path_authorized"] is False
        assert env["human_approved"] is True
        assert len(env["record_digests"]["src"]) == 64
        assert len(env["envelope_sha256"]) == 64

    def test_ready_like_status_rejected(self, doc_paths):
        for bad in ("READY", "FMX_READY", "B_TO_C_READY",
                    "PRODUCTION_READY", "SCIENTIFICALLY_VALIDATED",
                    "WARNING_READY", "AUTHORITY_APPROVED"):
            assert envelope_status_problems(bad), bad
            with pytest.raises(ValueError):
                build_claim_envelope("e", bad, {}, _approval(doc_paths))

    def test_neutral_statuses_accepted(self):
        assert envelope_status_problems("INTAKE_COMPLETE") == []
        assert envelope_status_problems("FMX_BLOCKED_CUTOFF") == []
        assert envelope_status_problems(
            "NO_QUALIFYING_PILOT_SOURCE") == []

    def test_direct_constructor_rejects_authority_true(self):
        for flag in ("promotion_eligible", "production_authorized",
                     "warning_path_authorized"):
            kwargs = {flag: True}
            with pytest.raises(ValueError):
                ResearchClaimEnvelopeV0(
                    envelope_id="e", status="INTAKE_COMPLETE",
                    matrix_sha256="a" * 64, policy_sha256="b" * 64,
                    **kwargs)
        with pytest.raises(ValueError):
            ResearchClaimEnvelopeV0(
                envelope_id="e", status="INTAKE_COMPLETE",
                matrix_sha256="a" * 64, policy_sha256="b" * 64,
                research_diagnostic_only=False)

    def test_blockers_bar_execution_statuses(self, doc_paths):
        blocked = _approval(
            doc_paths, unresolved_blockers=["license unresolved"])
        # design-stage status tolerates blockers
        env = build_claim_envelope("e", "DESIGN_DRAFT_COMPLETE", {},
                                   blocked)
        assert env["unresolved_blockers"] == ["license unresolved"]
        # execution status refuses
        with pytest.raises(ValueError):
            build_claim_envelope("e", "INTAKE_COMPLETE", {}, blocked)
        with pytest.raises(ValueError):
            build_claim_envelope("e", "FORECAST_EXPERIMENT_ONLY", {},
                                 blocked)

    def test_arbitrary_mapping_record_rejected(self, doc_paths):
        with pytest.raises(ValueError):
            build_claim_envelope("e", "SOURCE_MATRIX_COMPLETE",
                                 {"bad": {"a": 1}}, _approval(doc_paths))

    def test_invalid_record_rejected(self, doc_paths):
        bad = SourceRecordV0(source_id="", provider="p", doi_or_url="u")
        with pytest.raises(ValueError):
            build_claim_envelope("e", "SOURCE_MATRIX_COMPLETE",
                                 {"src": bad}, _approval(doc_paths))


class TestFeatureLeakage:
    def _feat(self, **kw):
        base = {"name": "gefs_2t", "namespace": "occurrence",
                "field_class": "meteorological_reforecast",
                "data_class": "REFORECAST",
                "source_lineage": "GEFSv12 reforecast run 00Z",
                "availability_time": 100}
        base.update(kw)
        return base

    def test_clean_feature_passes(self):
        assert occurrence_feature_problems([self._feat()],
                                           issue_time=200) == []

    def test_b_priority_rejected(self):
        problems = occurrence_feature_problems(
            [self._feat(name="b_priority_score")], issue_time=200)
        assert any("B-derived" in p for p in problems)

    def test_ranked_name_rejected(self):
        assert occurrence_feature_problems(
            [self._feat(name="screen_rank_top5")], issue_time=200)

    def test_exposure_namespace_rejected(self):
        problems = occurrence_feature_problems(
            [self._feat(name="ghsl_built_up_m2", namespace="exposure")],
            issue_time=200)
        assert any("namespace" in p for p in problems)

    def test_renamed_exposure_field_rejected(self):
        # A14: an exposure value renamed into the occurrence namespace
        # is caught by the denylist.
        problems = occurrence_feature_problems(
            [self._feat(name="built_up_area_total")], issue_time=200)
        assert any("denylist" in p for p in problems)

    def test_unknown_field_class_rejected(self):
        problems = occurrence_feature_problems(
            [self._feat(field_class="exposure_proxy")], issue_time=200)
        assert any("field_class" in p for p in problems)

    def test_missing_lineage_rejected(self):
        problems = occurrence_feature_problems(
            [self._feat(source_lineage="")], issue_time=200)
        assert any("lineage" in p for p in problems)

    def test_future_availability_rejected(self):
        problems = occurrence_feature_problems(
            [self._feat(availability_time=500)], issue_time=200)
        assert any("after forecast issue_time" in p for p in problems)

    def test_nonfinite_availability_rejected(self):
        problems = occurrence_feature_problems(
            [self._feat(availability_time=float("nan"))], issue_time=200)
        assert problems

    def test_reanalysis_rejected_in_forecast(self):
        problems = forecast_feature_problems(
            [self._feat(data_class="REANALYSIS",
                        field_class="meteorological_reforecast")],
            issue_time=200)
        assert any("data_class" in p for p in problems)

    def test_current_feed_rejected_in_forecast(self):
        problems = forecast_feature_problems(
            [self._feat(data_class="CURRENT_FEED")], issue_time=200)
        assert problems

    def test_forecast_admissible_passes(self):
        features = [
            self._feat(),
            self._feat(name="ifs_2t",
                       field_class="meteorological_archived_operational",
                       data_class="ARCHIVED_OPERATIONAL"),
        ]
        assert forecast_feature_problems(features, issue_time=200) == []


class TestCascadeAtomicity:
    def test_split_violation(self):
        problems = cascade_atomicity_problems(
            {"e1": "train", "e2": "test"}, {"g1": ["e1", "e2"]})
        assert any("atomic" in p for p in problems)

    def test_unmapped_member_rejected(self):
        problems = cascade_atomicity_problems(
            {"e1": "train"}, {"g1": ["e1", "e2"]})
        assert any("unmapped" in p for p in problems)

    def test_complete_atomic_ok(self):
        assert cascade_atomicity_problems(
            {"e1": "train", "e2": "train"},
            {"g1": ["e1", "e2"]}) == []


class TestRecordProblems:
    def test_vertical_ontology_enforced(self):
        bad = HazardVerticalSpecV0(
            vertical_id="flood_generic",
            physical_event_unit="x", mechanism="unknown",
            exclusions=("a",))
        problems = bad.problems()
        assert any("ontology" in p for p in problems)
        assert any("mechanism" in p for p in problems)

    def test_vertical_requires_exclusions(self):
        bad = HazardVerticalSpecV0(
            vertical_id="glof", physical_event_unit="x",
            mechanism="lake_outburst", exclusions=())
        assert any("exclusions" in p for p in bad.problems())

    def test_event_label_strict_time(self):
        base = dict(event_id="e1", vertical_id="glof", source_id="s",
                    source_version="v1", event_time_precision="day",
                    event_time_basis="report", geometry_role="lake_point",
                    basin_id="kosi", uncertainty_seconds=86400,
                    event_time_start="2020-01-01T00:00:00Z",
                    event_time_end="2020-01-01T12:00:00Z")
        assert EventLabelV0(**base).problems() == []
        assert EventLabelV0(
            **{**base, "event_time_start": "2020-01-01T00:00:00"}
        ).problems()  # naive timestamp
        assert EventLabelV0(
            **{**base, "event_time_end": "2019-12-31T00:00:00Z"}
        ).problems()  # inverted
        assert EventLabelV0(
            **{**base, "uncertainty_seconds": float("inf")}).problems()
        assert EventLabelV0(
            **{**base, "event_time_precision": "exact_timestamp"}
        ).problems()  # precision/class inconsistency
        assert EventLabelV0(
            **{**base, "latitude": 91.0}).problems()

    def test_adjudicated_requires_two_reviewers(self):
        rec = EventLabelV0(
            event_id="e1", vertical_id="glof", source_id="s",
            source_version="v1", event_time_precision="day",
            event_time_basis="report", geometry_role="lake_point",
            basin_id="kosi", uncertainty_seconds=86400,
            event_time_start="2020-01-01T00:00:00Z",
            event_time_end="2020-01-01T12:00:00Z",
            adjudication_state="TWO_REVIEW_AGREE",
            reviewer_ids=("r1",))
        assert any("reviewer" in p for p in rec.problems())

    def test_opportunity_consistency(self):
        full = ObservationOpportunityV0(
            opportunity_id="o1", unit_id="u", platform="s1",
            window_start="2020-01-01T00:00:00Z",
            window_end="2020-01-02T00:00:00Z",
            coverage_fraction=1.0, state="OBSERVED_FULL")
        assert full.problems() == []
        bad = ObservationOpportunityV0(
            opportunity_id="o1", unit_id="u", platform="s1",
            window_start="2020-01-02T00:00:00Z",
            window_end="2020-01-01T00:00:00Z",
            coverage_fraction=1.0, state="OBSERVED_FULL")
        assert bad.problems()
        partial_lie = ObservationOpportunityV0(
            opportunity_id="o1", unit_id="u", platform="s1",
            window_start="2020-01-01T00:00:00Z",
            window_end="2020-01-02T00:00:00Z",
            coverage_fraction=0.4, state="OBSERVED_FULL")
        assert any("OBSERVED_FULL" in p for p in partial_lie.problems())

    def test_negative_control_requires_full_opportunity(self):
        base = dict(control_id="c1", unit_id="u",
                    window_start="2020-01-01T00:00:00Z",
                    window_end="2020-01-02T00:00:00Z",
                    opportunity_id="o1", state="NEGATIVE")
        assert ControlWindowV0(
            **{**base, "opportunity_state": "OBSERVED_FULL"}
        ).problems() == []
        for weak in ("OBSERVED_PARTIAL", "UNOBSERVED", "UNKNOWN"):
            problems = ControlWindowV0(
                **{**base, "opportunity_state": weak}).problems()
            assert any("NEGATIVE" in p for p in problems)

    def test_cutoff_requires_initialization(self):
        rec = CutoffRecordV0(
            cutoff_id="c1",
            source_observation_end="2026-08-20T00:00:00Z",
            source_processing_complete="2026-08-21T00:00:00Z",
            source_publication="2026-08-22T00:00:00Z",
            feature_availability="2026-08-23T00:00:00Z",
            forecast_initialization="2026-08-24T00:00:00Z",
            forecast_issue="2026-08-24T01:00:00Z",
            forecast_valid_start="2026-08-25T00:00:00Z",
            forecast_valid_end="2026-08-26T00:00:00Z")
        assert rec.problems() == []

    def test_holdout_requires_basin_mapping_and_regions(self):
        plan = HoldoutPlanV0(
            holdout_plan_id="hp1", assignment_rule="basin",
            train_groups=("b1",), validation_groups=("b2",),
            test_groups=("b3",), embargo_seconds=7 * 86400,
            evaluation_region_count=2,
            event_assignments={"e1": "b1", "e2": "b2", "e3": "b3"})
        assert plan.problems() == []

        random_split = HoldoutPlanV0(
            holdout_plan_id="hp1", assignment_rule="random",
            train_groups=("b1",), validation_groups=("b2",),
            test_groups=("b3",), embargo_seconds=1,
            evaluation_region_count=2, event_assignments={"e1": "b1"})
        assert any("random" in p for p in random_split.problems())

        single_region = HoldoutPlanV0(
            holdout_plan_id="hp1", assignment_rule="basin",
            train_groups=("b1",), validation_groups=("b2",),
            test_groups=("b3",), embargo_seconds=1,
            evaluation_region_count=1,
            event_assignments={"e1": "b1"})
        assert any("two independent evaluation regions" in p
                   for p in single_region.problems())

        stray = HoldoutPlanV0(
            holdout_plan_id="hp1", assignment_rule="basin",
            train_groups=("b1",), validation_groups=("b2",),
            test_groups=("b3",), embargo_seconds=1,
            evaluation_region_count=2,
            event_assignments={"e1": "ghost_basin"})
        assert any("undeclared" in p for p in stray.problems())

    def test_vintage_temporal_order_and_archive(self):
        good = ForecastVintageV0(
            vintage_id="v1", provider="NOAA", data_class="REFORECAST",
            initialization_time="2010-01-01T00:00:00Z",
            issue_time="2010-01-01T06:00:00Z",
            valid_start="2010-01-02T00:00:00Z",
            valid_end="2010-01-08T00:00:00Z",
            archive_availability="2020-01-01T00:00:00Z",
            model_version="gefs_v12", license_id="NOAA-PD",
            archive_mechanism="AWS noaa-gefs-retrospective")
        assert good.problems() == []
        inverted = ForecastVintageV0(
            vintage_id="v1", provider="x", data_class="REFORECAST",
            initialization_time="2010-01-01T00:00:00Z",
            issue_time="2009-12-31T00:00:00Z",  # before init
            valid_start="2010-01-02T00:00:00Z",
            valid_end="2010-01-08T00:00:00Z",
            archive_availability="2020-01-01T00:00:00Z",
            model_version="m", license_id="l", archive_mechanism="a")
        assert any("order violated" in p for p in inverted.problems())
        reanalysis = ForecastVintageV0(
            vintage_id="v1", provider="x", data_class="REANALYSIS",
            license_id="l")
        assert any("not admissible" in p for p in reanalysis.problems())

    def test_regime_requires_three_seeds(self):
        reg = RegimeArtifactV0(
            regime_id="r1", mode="FORECAST_REGIME",
            preprocessing_digest="x" * 64, seeds=(1,))
        assert any("three" in p for p in reg.problems())

    def test_experiment_requires_vintage_binding(self):
        exp = ForecastExperimentV0(
            experiment_id="x1", vertical_id="glof", target="occurrence",
            horizon="30d", holdout_plan_id="hp1",
            baselines=("climatology", "rule", "regularized_supervised"),
            metrics=("brier", "calibration", "precision_recall",
                     "event_recall", "false_alarms_per_opportunity"),
            missing_data_policy="declared", evaluation_region_count=2)
        assert any("intage" in p for p in exp.problems())

    def test_source_verified_posture_requires_evidence(self):
        src = SourceRecordV0(source_id="s", provider="p",
                             doi_or_url="u", posture="EVIDENCE_VERIFIED",
                             version="v1", license_id="CC-BY-4.0",
                             redistribution="yes", geography="Nepal",
                             event_time_class="EXACT_DAY",
                             non_event_frame="lake inventory",
                             access_status="open",
                             evidence_sidecar_sha256="a" * 64,
                             evidence_review_state="INDEPENDENTLY_VERIFIED")
        assert src.problems() == []
        weak = SourceRecordV0(source_id="s", provider="p", doi_or_url="u",
                              posture="EVIDENCE_VERIFIED")
        assert weak.problems()


class TestClaimScan:
    def test_forbidden_content_detected(self):
        assert scan_claims_text('{"status": "FMX_READY"}')
        assert scan_claims_text('{"warning_path_authorized": true}')
        assert scan_claims_text("issues an operational warning now")
        assert scan_claims_text('{"status": "SCIENTIFICALLY_VALIDATED"}')

    def test_clean_content_passes(self):
        assert scan_claims_text(
            '{"status": "DESIGN_DRAFT_COMPLETE", '
            '"warning_path_authorized": false, "b_status": '
            '"B_TO_C_BLOCKED"}') == []
