"""Adversarial probes for B-series audit findings (B01–B24).

Each test reproduces a semantic false-acceptance path and proves it now
fails closed.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nepal.research_v0 import cli, gates
from nepal.research_v0._hashing import hash_artifact, sha256_file
from nepal.research_v0.policy import (
    TargetState, assign_target_state, assign_target_state_typed,
    cutoff_order_problems, derive_control_state, derive_control_state_typed,
    eligible_horizons)
from nepal.research_v0.records import (
    ControlWindowV0, CutoffRecordV0, EventLabelV0, EvidenceArtifactV0,
    ForecastExperimentV0, ForecastVintageV0, HoldoutPlanV0,
    ObservationOpportunityV0, RegimeArtifactV0, SourceRecordV0)


def _label(**kw):
    base = dict(
        event_id="e1", vertical_id="glof", source_id="s",
        source_version="v1", event_time_precision="interval",
        event_time_basis="scene pair", geometry_role="deposit_polygon",
        basin_id="kosi", uncertainty_seconds=12 * 86400,
        event_time_start="2020-01-01T00:00:00Z",
        event_time_end="2020-01-13T00:00:00Z")
    base.update(kw)
    return EventLabelV0(**base)


class TestEventLabelSemantics:
    def test_b09_uncertainty_must_cover_interval(self):
        # 12-day interval claiming 1-hour uncertainty must fail.
        bad = _label(uncertainty_seconds=3600)
        assert any("cover the whole bracket" in p
                   for p in bad.problems())

    def test_b10_duplicate_reviewers_rejected(self):
        bad = _label(adjudication_state="TWO_REVIEW_AGREE",
                     reviewer_ids=("r1", "r1"))
        assert any("unique" in p for p in bad.problems())

    def test_b10_independent_reviewers_accepted(self):
        ok = _label(adjudication_state="TWO_REVIEW_AGREE",
                    reviewer_ids=("r1", "r2"))
        assert ok.problems() == []


class TestDerivedTargets:
    def test_b12_derived_control_state(self):
        # NEGATIVE only from a real OBSERVED_FULL opportunity.
        assert derive_control_state(
            1000, 2000, [], opportunity_state="OBSERVED_FULL") is \
            TargetState.NEGATIVE
        assert derive_control_state(
            1000, 2000, [], opportunity_state="OBSERVED_PARTIAL") is \
            TargetState.CENSORED_OR_AMBIGUOUS

    def test_b13_unresolved_overlap_dominant(self):
        # A contained adjudicated event + an overlapping unresolved
        # interval → CENSORED, not POSITIVE.
        ev = [{"start": 1100, "end": 1500, "adjudicated": True},
              {"start": 1800, "end": 2200, "adjudicated": False}]
        assert assign_target_state(
            1000, 2000, ev, opportunity_state="OBSERVED_FULL") is \
            TargetState.CENSORED_OR_AMBIGUOUS


class TestCutoffCompleteness:
    def _ok(self):
        return {
            "source_observation_end": "2026-08-20T00:00:00Z",
            "source_processing_complete": "2026-08-21T00:00:00Z",
            "source_publication": "2026-08-22T00:00:00Z",
            "feature_availability": "2026-08-23T00:00:00Z",
            "forecast_initialization": "2026-08-24T00:00:00Z",
            "forecast_issue": "2026-08-24T01:00:00Z",
            "forecast_valid_start": "2026-08-25T00:00:00Z",
            "forecast_valid_end": "2026-08-26T00:00:00Z",
            "archive_availability": "2026-09-01T00:00:00Z",
            "local_retrieval_time": "2026-09-14T00:00:00Z",
        }

    def test_b14_complete_chain(self):
        assert cutoff_order_problems(self._ok()) == []
        rec = CutoffRecordV0(cutoff_id="c1", **self._ok())
        assert rec.problems() == []

    def test_b14_archive_before_issue_rejected(self):
        bad = self._ok()
        bad["archive_availability"] = "2026-08-24T00:30:00Z"
        assert any("archive" in p for p in cutoff_order_problems(bad))


class TestHoldoutUniverse:
    def _plan(self, **kw):
        base = dict(
            holdout_plan_id="hp1", assignment_rule="basin",
            train_groups=("b1",), validation_groups=("b2",),
            test_groups=("b3",), embargo_seconds=7 * 86400,
            evaluation_region_names=("b1", "b2"),
            event_assignments={"e1": "b1", "e2": "b2", "e3": "b3"})
        base.update(kw)
        return HoldoutPlanV0(**base)

    def test_b16_complete_universe(self):
        assert self._plan().problems() == []

    def test_b16_undeclared_and_uncovered_groups_fail(self):
        assert any("undeclared" in p for p in self._plan(
            event_assignments={"e1": "ghost"}).problems())
        assert any("no assigned events" in p for p in self._plan(
            event_assignments={"e1": "b1"}).problems())

    def test_b16_named_regions_required(self):
        assert any("named" in p for p in self._plan(
            evaluation_region_names=("kosi",)).problems())
        assert any("named" in p for p in self._plan(
            evaluation_region_names=("kosi", "kosi")).problems())


class TestVintageAndRegimeBinding:
    def test_b15_vintage_requires_archived_bytes(self):
        v = ForecastVintageV0(
            vintage_id="v1", provider="NOAA", data_class="REFORECAST",
            initialization_time="2010-01-01T00:00:00Z",
            issue_time="2010-01-01T06:00:00Z",
            valid_start="2010-01-02T00:00:00Z",
            valid_end="2010-01-08T00:00:00Z",
            archive_availability="2020-01-01T00:00:00Z",
            model_version="gefs_v12", license_id="NOAA-PD",
            archive_mechanism="AWS noaa-gefs-retrospective")
        assert any("archive_payload_sha256" in p
                   for p in v.problems())
        good = ForecastVintageV0(
            vintage_id="v1", provider="NOAA", data_class="REFORECAST",
            initialization_time="2010-01-01T00:00:00Z",
            issue_time="2010-01-01T06:00:00Z",
            valid_start="2010-01-02T00:00:00Z",
            valid_end="2010-01-08T00:00:00Z",
            archive_availability="2020-01-01T00:00:00Z",
            archive_payload_sha256="a" * 64,
            retrieval_record_sha256="b" * 64,
            model_version="gefs_v12", license_id="NOAA-PD",
            archive_mechanism="AWS")
        assert good.problems() == []

    def test_b20_regime_requires_real_digests(self):
        reg = RegimeArtifactV0(
            regime_id="r1", mode="FORECAST_REGIME",
            preprocessing_digest="x" * 64, seeds=(1, 2, 3))
        problems = reg.problems()
        assert any("k_selection_digest" in p for p in problems)
        assert any("stability_report_digest" in p for p in problems)
        assert any("source_digests" in p for p in problems)


class TestExperimentGate:
    def test_b19_target_horizon_digests(self):
        exp = ForecastExperimentV0(
            experiment_id="x1", vertical_id="glof", target="occurrence",
            horizon="30d", holdout_plan_id="hp1",
            feature_digests=("f" * 64,),
            vintage_digests=("a" * 64,),
            baselines=("climatology", "rule", "regularized_supervised"),
            metrics=("brier", "calibration", "precision_recall",
                     "event_recall", "false_alarms_per_opportunity"),
            missing_data_policy="declared",
            power_report_digest="9" * 64,
            uncertainty_method="basin_block_bootstrap",
            evaluation_region_count=2)
        assert exp.problems() == []
        for bad in ({"target": "exposure"}, {"horizon": "13d"},
                    {"feature_digests": ()}, {"vintage_digests": ()},
                    {"power_report_digest": ""}):
            kw = dict(experiment_id="x1", vertical_id="glof",
                      target="occurrence", horizon="30d",
                      holdout_plan_id="hp1",
                      feature_digests=("f" * 64,),
                      vintage_digests=("a" * 64,),
                      baselines=("climatology", "rule",
                                 "regularized_supervised"),
                      metrics=("brier", "calibration",
                               "precision_recall", "event_recall",
                               "false_alarms_per_opportunity"),
                      missing_data_policy="declared",
                      power_report_digest="9" * 64,
                      uncertainty_method="basin_block_bootstrap",
                      evaluation_region_count=2)
            kw.update(bad)
            assert ForecastExperimentV0(**kw).problems(), bad


class TestHashArtifact:
    def test_b22_root_containment_and_metadata(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        f = root / "a.txt"
        f.write_bytes(b"payload")
        out = hash_artifact(f, root)
        assert out["relpath"] == "a.txt"
        assert out["size_bytes"] == 7
        assert len(out["sha256"]) == 64
        outside = tmp_path / "b.txt"
        outside.write_bytes(b"x")
        with pytest.raises(ValueError):
            hash_artifact(outside, root)


class TestRound3SemanticBinding:
    """C-series probes: identity, evidence binding, total functions."""

    def test_c02_impossible_calendar_dates_rejected(self):
        # exercised through design_approval_problems in gates tests;
        # here verify the date validator directly
        from nepal.research_v0.gates import _valid_calendar_date
        assert not _valid_calendar_date("2026-99-99")
        assert not _valid_calendar_date("2026-02-30")
        assert not _valid_calendar_date("2026-1-1")
        assert _valid_calendar_date("2026-02-28")

    def test_c04_spoofed_class_name_rejected(self, tmp_path):
        # A dynamically fabricated class named SourceRecordV0 must not
        # satisfy the allowlist — exact type identity is required.
        root = tmp_path / "r"
        root.mkdir()
        m = root / gates.EXPECTED_MATRIX_NAME
        p = root / gates.EXPECTED_POLICY_NAME
        m.write_text("m"); p.write_text("p")
        approval = {
            "artifact_root": str(root), "matrix_path": str(m),
            "policy_path": str(p), "matrix_sha256": sha256_file(m),
            "policy_sha256": sha256_file(p),
            "source_review_date": "2026-09-14",
            "selected_pilot_rule":
                "FIRST_PASSING_ALL_GATES_ELSE_NO_QUALIFYING",
            "approval_scope": "design_review_only",
            "unresolved_blockers": [], "human_approved": True,
            "approved_by": "pi", "approver_role": "approver",
            "approver_attestation": gates.EXPECTED_ATTESTATION,
            "approved_at": "2026-09-14T00:00:00Z"}
        Fake = type("SourceRecordV0", (), {
            "problems": lambda self: [],
            "to_dict": lambda self: {"source_id": "x"}})
        with pytest.raises(ValueError):
            gates.build_claim_envelope(
                "e", "METADATA_REVIEW_COMPLETE", {"src": Fake()},
                approval)

    def test_c03_evidence_root_required_for_verified(self, tmp_path):
        root = tmp_path / "r"
        root.mkdir()
        m = root / gates.EXPECTED_MATRIX_NAME
        p = root / gates.EXPECTED_POLICY_NAME
        m.write_text("m"); p.write_text("p")
        sidecar = root / "ev.json"
        sidecar.write_text("{}")
        approval = {
            "artifact_root": str(root), "matrix_path": str(m),
            "policy_path": str(p), "matrix_sha256": sha256_file(m),
            "policy_sha256": sha256_file(p),
            "source_review_date": "2026-09-14",
            "selected_pilot_rule":
                "FIRST_PASSING_ALL_GATES_ELSE_NO_QUALIFYING",
            "approval_scope": "design_review_only",
            "unresolved_blockers": [], "human_approved": True,
            "approved_by": "pi", "approver_role": "approver",
            "approver_attestation": gates.EXPECTED_ATTESTATION,
            "approved_at": "2026-09-14T00:00:00Z"}
        verified = SourceRecordV0(
            source_id="s", provider="p", doi_or_url="u",
            posture="EVIDENCE_VERIFIED", version="v1",
            as_of_date="2026-09-01", license_id="CC-BY-4.0",
            redistribution="yes", geography="Nepal",
            temporal_coverage="1972-2025", event_time_class="EXACT_DAY",
            spatial_semantics="x", observation_method="x",
            non_event_frame="x", update_cadence="annual",
            access_status="open",
            evidence_sidecar_path=str(sidecar),
            evidence_sidecar_sha256=sha256_file(sidecar),
            evidence_as_of="2026-09-14",
            evidence_review_state="INDEPENDENTLY_VERIFIED")
        # No evidence_root → builder must refuse.
        with pytest.raises(ValueError):
            gates.build_claim_envelope(
                "e", "METADATA_REVIEW_COMPLETE", {"src": verified},
                approval)
        # With evidence_root → verified bytes bind.
        approval["evidence_root"] = str(root)
        env = gates.build_claim_envelope(
            "e", "METADATA_REVIEW_COMPLETE", {"src": verified}, approval)
        assert env["artifact_root"] == str(root)

    def test_c05_parentless_fk_rejected(self, tmp_path):
        root = tmp_path / "r"; root.mkdir()
        m = root / gates.EXPECTED_MATRIX_NAME
        p = root / gates.EXPECTED_POLICY_NAME
        m.write_text("m"); p.write_text("p")
        approval = {
            "artifact_root": str(root), "matrix_path": str(m),
            "policy_path": str(p), "matrix_sha256": sha256_file(m),
            "policy_sha256": sha256_file(p),
            "source_review_date": "2026-09-14",
            "selected_pilot_rule":
                "FIRST_PASSING_ALL_GATES_ELSE_NO_QUALIFYING",
            "approval_scope": "design_review_only",
            "unresolved_blockers": [], "human_approved": True,
            "approved_by": "pi", "approver_role": "approver",
            "approver_attestation": gates.EXPECTED_ATTESTATION,
            "approved_at": "2026-09-14T00:00:00Z"}
        label = _label()
        with pytest.raises(ValueError):
            gates.build_claim_envelope(
                "e", "INTAKE_COMPLETE",
                {"lbl": label, "src": _label_source(),
                 "opp": _opportunity(), "ctl": _control(),
                 "hp": _holdout()}, approval)

    def test_c06_minimal_forecast_graph_rejected(self, tmp_path):
        root = tmp_path / "r"; root.mkdir()
        m = root / gates.EXPECTED_MATRIX_NAME
        p = root / gates.EXPECTED_POLICY_NAME
        m.write_text("m"); p.write_text("p")
        approval = {
            "artifact_root": str(root), "matrix_path": str(m),
            "policy_path": str(p), "matrix_sha256": sha256_file(m),
            "policy_sha256": sha256_file(p),
            "source_review_date": "2026-09-14",
            "selected_pilot_rule":
                "FIRST_PASSING_ALL_GATES_ELSE_NO_QUALIFYING",
            "approval_scope": "design_review_only",
            "unresolved_blockers": [], "human_approved": True,
            "approved_by": "pi", "approver_role": "approver",
            "approver_attestation": gates.EXPECTED_ATTESTATION,
            "approved_at": "2026-09-14T00:00:00Z",
            "evidence_root": str(root)}
        # Experiment + vintage + holdout only — no labels/controls/
        # opportunities/sources/artifacts → must fail.
        exp = ForecastExperimentV0(
            experiment_id="x1", vertical_id="glof", target="occurrence",
            horizon="30d", holdout_plan_id="hp1",
            feature_digests=("f" * 64,), vintage_digests=("a" * 64,),
            baselines=("climatology", "rule", "regularized_supervised"),
            metrics=("brier", "calibration", "precision_recall",
                     "event_recall", "false_alarms_per_opportunity"),
            missing_data_policy="declared",
            power_report_digest="9" * 64,
            uncertainty_method="basin_block_bootstrap",
            evaluation_region_count=2)
        hp = _holdout()
        v = _vintage()
        with pytest.raises(ValueError):
            gates.build_claim_envelope(
                "e", "FORECAST_EXPERIMENT_ONLY",
                {"exp": exp, "hp": hp, "v": v}, approval)

    def test_c10_inside_root_symlink_rejected(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        real = root / "real.txt"
        real.write_bytes(b"x")
        link = root / "link.txt"
        link.symlink_to(real)
        with pytest.raises(ValueError):
            hash_artifact(link, root)

    def test_c11_malformed_optional_cutoff_times(self):
        good = dict(
            cutoff_id="c1",
            source_observation_end="2026-08-20T00:00:00Z",
            source_processing_complete="2026-08-21T00:00:00Z",
            source_publication="2026-08-22T00:00:00Z",
            feature_availability="2026-08-23T00:00:00Z",
            forecast_initialization="2026-08-24T00:00:00Z",
            forecast_issue="2026-08-24T01:00:00Z",
            forecast_valid_start="2026-08-25T00:00:00Z",
            forecast_valid_end="2026-08-26T00:00:00Z",
            archive_availability="2026-09-01T00:00:00Z",
            local_retrieval_time="2026-09-14T00:00:00Z")
        assert CutoffRecordV0(**good).problems() == []
        bad = CutoffRecordV0(**{**good, "event_time_start": "bogus"})
        assert bad.problems()
        inverted = CutoffRecordV0(**{**good,
                                     "event_time_start":
                                     "2026-09-02T00:00:00Z",
                                     "event_time_end":
                                     "2026-09-01T00:00:00Z"})
        assert inverted.problems()
        vintage_no_event = CutoffRecordV0(**{**good,
                                           "forecast_vintage_id": "v1"})
        assert any("event_time" in p
                   for p in vintage_no_event.problems())

    def test_c12_total_functions(self):
        assert eligible_horizons(
            "bogus", event_uncertainty_seconds=0,
            observation_latency_seconds=0,
            processing_latency_seconds=0) == []
        assert eligible_horizons(
            None, event_uncertainty_seconds=0,
            observation_latency_seconds=0,
            processing_latency_seconds=0) == []

    def test_c13_typed_targets_only(self):
        opp = ObservationOpportunityV0(
            opportunity_id="o1", unit_id="u", platform="s1",
            window_start="2020-01-01T00:00:00Z",
            window_end="2020-01-02T00:00:00Z",
            coverage_fraction=1.0, state="OBSERVED_FULL",
            source_id="s", source_as_of="2020-01-03",
            frame_ids=("f1", "f2"))
        label = _label(
            event_time_start="2020-01-01T06:00:00Z",
            event_time_end="2020-01-01T10:00:00Z",
            event_time_precision="day",
            uncertainty_seconds=4 * 3600,
            adjudication_state="TWO_REVIEW_AGREE",
            reviewer_ids=("r1", "r2"))
        assert assign_target_state_typed(opp, [label]) is \
            TargetState.POSITIVE
        # raw dict opportunity is rejected outright
        assert assign_target_state_typed(
            {"state": "OBSERVED_FULL"}, [label]) is \
            TargetState.CENSORED_OR_AMBIGUOUS
        ctl = ControlWindowV0(
            control_id="c1", unit_id="u",
            window_start="2020-01-01T00:00:00Z",
            window_end="2020-01-02T00:00:00Z",
            opportunity_id="o1", opportunity_state="OBSERVED_FULL")
        assert derive_control_state_typed(ctl, opp, []) is \
            TargetState.NEGATIVE
        # mismatched windows censor
        ctl_bad = ControlWindowV0(
            control_id="c1", unit_id="u",
            window_start="2020-01-01T00:00:00Z",
            window_end="2020-01-03T00:00:00Z",
            opportunity_id="o1", opportunity_state="OBSERVED_FULL")
        assert derive_control_state_typed(ctl_bad, opp, []) is \
            TargetState.CENSORED_OR_AMBIGUOUS

    def test_c14_uncertainty_required(self):
        assert any("uncertainty_seconds is required"
                   in p for p in _label(
                       uncertainty_seconds=None).problems())
        # "interval" precision cannot claim sub-day uncertainty
        assert _label(uncertainty_seconds=3600,
                      event_time_precision="interval",
                      event_time_start="2020-01-01T00:00:00Z",
                      event_time_end="2020-01-01T01:00:00Z").problems()

    def test_c17_region_mapping(self):
        assert any("not drawn from declared" in p for p in _holdout(
            evaluation_region_names=("kosi", "mustang")).problems())

    def test_c19_regime_digests_must_bind_artifacts(self, tmp_path):
        root = tmp_path / "r"; root.mkdir()
        m = root / gates.EXPECTED_MATRIX_NAME
        p = root / gates.EXPECTED_POLICY_NAME
        m.write_text("m"); p.write_text("p")
        approval = {
            "artifact_root": str(root), "matrix_path": str(m),
            "policy_path": str(p), "matrix_sha256": sha256_file(m),
            "policy_sha256": sha256_file(p),
            "source_review_date": "2026-09-14",
            "selected_pilot_rule":
                "FIRST_PASSING_ALL_GATES_ELSE_NO_QUALIFYING",
            "approval_scope": "design_review_only",
            "unresolved_blockers": [], "human_approved": True,
            "approved_by": "pi", "approver_role": "approver",
            "approver_attestation": gates.EXPECTED_ATTESTATION,
            "approved_at": "2026-09-14T00:00:00Z",
            "evidence_root": str(root)}
        reg = RegimeArtifactV0(
            regime_id="r1", mode="FORECAST_REGIME",
            preprocessing_digest="c" * 64, k_selection_digest="d" * 64,
            stability_report_digest="e" * 64,
            source_digests=("1" * 64,), seeds=(1, 2, 3))
        # no artifacts bound → every digest is orphaned
        with pytest.raises(ValueError):
            gates.build_claim_envelope(
                "e", "DESCRIPTIVE_REGIME_ONLY", {"reg": reg}, approval)

    def test_c21_scanner_variants(self):
        assert gates.scan_claims_text("status: FMX_READY")  # YAML
        assert gates.scan_claims_text("status = production_ready")
        assert gates.scan_claims_text(
            "{'status': 'warning_ready'}")  # single quotes
        assert gates.scan_claims_text(
            '\\"status\\": \\"authority_approved\\"')  # escaped
        assert gates.scan_claims_text("warning_path_authorized: yes")
        assert gates.scan_claims_text(
            '{"status": ["ready"]}')  # array form
        assert not gates.scan_claims_text(
            '{"status": "B_TO_C_BLOCKED"}')

    def test_c07_cli_requires_bundle_for_execution_status(self,
                                                        tmp_path):
        env_file, matrix, policy_doc, recs = \
            TestCLIBundle()._build(tmp_path)
        # Craft an execution-status envelope that is internally
        # consistent — CLI must still refuse without bundle flags.
        env = json.loads(env_file.read_text())
        env["status"] = "INTAKE_COMPLETE"
        body = {k: v for k, v in env.items() if k != "envelope_sha256"}
        from nepal.research_v0._hashing import sha256_canonical
        env["envelope_sha256"] = sha256_canonical(body)
        env_file.write_text(json.dumps(env), encoding="utf-8")
        rc = cli.main(["validate-envelope", str(env_file)])
        assert rc == 1


def _label_source():
    return SourceRecordV0(source_id="s", provider="p", doi_or_url="u",
                          posture="CANDIDATE_ONLY")


def _opportunity():
    return ObservationOpportunityV0(
        opportunity_id="o1", unit_id="u", platform="s1",
        window_start="2020-01-01T00:00:00Z",
        window_end="2020-01-02T00:00:00Z", coverage_fraction=1.0,
        state="OBSERVED_FULL", source_id="s",
        source_as_of="2020-01-03", frame_ids=("f1",))


def _control():
    return ControlWindowV0(
        control_id="c1", unit_id="u",
        window_start="2020-01-01T00:00:00Z",
        window_end="2020-01-02T00:00:00Z", opportunity_id="o1",
        opportunity_state="OBSERVED_FULL", state="NEGATIVE")


def _holdout(**kw):
    base = dict(
        holdout_plan_id="hp1", assignment_rule="basin",
        train_groups=("b1",), validation_groups=("b2",),
        test_groups=("b3",), embargo_seconds=7 * 86400,
        evaluation_region_names=("b1", "b2"),
        event_assignments={"e1": "b1", "e2": "b2", "e3": "b3"})
    base.update(kw)
    return HoldoutPlanV0(**base)


def _vintage():
    return ForecastVintageV0(
        vintage_id="v1", provider="NOAA", data_class="REFORECAST",
        initialization_time="2010-01-01T00:00:00Z",
        issue_time="2010-01-01T06:00:00Z",
        valid_start="2010-01-02T00:00:00Z",
        valid_end="2010-01-08T00:00:00Z",
        archive_availability="2020-01-01T00:00:00Z",
        archive_payload_sha256="a" * 64,
        retrieval_record_sha256="b" * 64,
        model_version="gefs_v12", license_id="NOAA-PD",
        archive_mechanism="AWS")


class TestNarrativeLint:
    def test_b36_all_artifacts_pass_claim_scan(self):
        # Artifacts under lint: Markdown, JSON, manifests.  Package
        # sources are excluded — the scanner's own pattern literals
        # would self-trip, which is not a claim.
        repo = Path(__file__).resolve().parents[1]
        targets = list((repo / "docs" / "science").glob("*"))
        assert targets, "no science artifacts found"
        for path in targets:
            findings = gates.scan_claims_text(
                path.read_text(encoding="utf-8"))
            assert findings == [], f"{path.name}: {findings}"


class TestCLIBundle:
    def _build(self, tmp_path):
        # Real D1/D2-named artifacts + a source record, approved.
        root = tmp_path / "root"
        root.mkdir()
        matrix = root / gates.EXPECTED_MATRIX_NAME
        policy_doc = root / gates.EXPECTED_POLICY_NAME
        matrix.write_text("m", encoding="utf-8")
        policy_doc.write_text("p", encoding="utf-8")
        approval = {
            "artifact_root": str(root),
            "matrix_path": str(matrix), "policy_path": str(policy_doc),
            "matrix_sha256": sha256_file(matrix),
            "policy_sha256": sha256_file(policy_doc),
            "source_review_date": "2026-09-14",
            "selected_pilot_rule":
                "FIRST_PASSING_ALL_GATES_ELSE_NO_QUALIFYING",
            "approval_scope": "design_review_only",
            "unresolved_blockers": [],
            "human_approved": True, "approved_by": "pi",
            "approver_role": "science-design-approver",
            "approver_attestation": gates.EXPECTED_ATTESTATION,
            "approved_at": "2026-09-14T00:00:00Z"}
        src = SourceRecordV0(source_id="s1", provider="p",
                             doi_or_url="u")
        env = gates.build_claim_envelope(
            "env-x", "METADATA_REVIEW_COMPLETE", {"src": src}, approval)
        env_file = tmp_path / "env.json"
        env_file.write_text(json.dumps(env), encoding="utf-8")
        recs = tmp_path / "records"
        recs.mkdir()
        (recs / "src.json").write_text(
            json.dumps(src.to_dict()), encoding="utf-8")
        return env_file, matrix, policy_doc, recs

    def test_b24_bundle_verification(self, tmp_path):
        env_file, matrix, policy_doc, recs = self._build(tmp_path)
        rc = cli.main([
            "validate-envelope", str(env_file),
            "--matrix-path", str(matrix),
            "--policy-path", str(policy_doc),
            "--records-dir", str(recs)])
        assert rc == 0
        # Fabricated: same envelope file but wrong matrix bytes.
        matrix.write_text("tampered", encoding="utf-8")
        rc = cli.main([
            "validate-envelope", str(env_file),
            "--matrix-path", str(matrix)])
        assert rc == 1

    def test_b24_fabricated_self_consistent_envelope_fails(self,
                                                         tmp_path):
        env_file, matrix, _, _ = self._build(tmp_path)
        env = json.loads(env_file.read_text())
        # An attacker crafts a structurally valid envelope — but cannot
        # satisfy real-byte verification against the wrong file.
        env["matrix_sha256"] = "f" * 64
        body = {k: v for k, v in env.items() if k != "envelope_sha256"}
        from nepal.research_v0._hashing import sha256_canonical
        env["envelope_sha256"] = sha256_canonical(body)
        env_file.write_text(json.dumps(env), encoding="utf-8")
        rc = cli.main(["validate-envelope", str(env_file),
                       "--matrix-path", str(matrix)])
        assert rc == 1
