"""Gate, envelope, and leakage tests for research_v0.

Approval bindings are file- and root-bound: tests create real
D1/D2-named artifacts under a declared artifact root. Fabricated
digests, symlinks, outside-root files, wrong filenames, bad dates,
missing attestation, blocker-carrying execution statuses, and invalid
records all reject.
"""
from __future__ import annotations

import pytest

from nepal.research_v0 import gates
from nepal.research_v0._hashing import sha256_file
from nepal.research_v0.gates import (
    DesignApprovalError, build_claim_envelope, cascade_atomicity_problems,
    design_approval_problems, envelope_status_problems,
    forecast_feature_problems, occurrence_feature_problems,
    scan_claims_text, source_evidence_problems)
from nepal.research_v0.records import (
    ControlWindowV0, CutoffRecordV0, EventLabelV0, ForecastExperimentV0,
    ForecastVintageV0, HazardVerticalSpecV0, HoldoutPlanV0,
    ObservationOpportunityV0, RegimeArtifactV0, ResearchClaimEnvelopeV0,
    SourceRecordV0)


@pytest.fixture
def doc_paths(tmp_path):
    root = tmp_path / "artifact_root"
    root.mkdir()
    matrix = root / gates.EXPECTED_MATRIX_NAME
    policy = root / gates.EXPECTED_POLICY_NAME
    matrix.write_text("decision matrix v0", encoding="utf-8")
    policy.write_text("cutoff policy v0", encoding="utf-8")
    return root, matrix, policy


def _approval(doc_paths, _matrix_sha256=None, _policy_sha256=None,
              **overrides):
    root, matrix, policy = doc_paths
    binding = {
        "artifact_root": str(root),
        "matrix_path": str(matrix),
        "policy_path": str(policy),
        "matrix_sha256": _matrix_sha256 or sha256_file(matrix),
        "policy_sha256": _policy_sha256 or sha256_file(policy),
        "source_review_date": "2026-09-14",
        "selected_pilot_rule":
            "FIRST_PASSING_ALL_GATES_ELSE_NO_QUALIFYING",
        "approval_scope": "design_review_only",
        "unresolved_blockers": [],
        "human_approved": True,
        "approved_by": "principal-investigator",
        "approver_attestation": "I reviewed the design artifacts only",
        "approved_at": "2026-09-14T00:00:00Z",
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

    def test_outside_root_file_rejected(self, doc_paths, tmp_path):
        outside = tmp_path / gates.EXPECTED_MATRIX_NAME
        outside.write_text("not the artifact", encoding="utf-8")
        problems = design_approval_problems(_approval(
            doc_paths, matrix_path=str(outside),
            matrix_sha256=sha256_file(outside)))
        assert any("outside artifact_root" in p for p in problems)

    def test_wrong_filename_rejected(self, doc_paths):
        root, matrix, _ = doc_paths
        renamed = root / "renamed.md"
        renamed.write_text(matrix.read_text(), encoding="utf-8")
        problems = design_approval_problems(_approval(
            doc_paths, matrix_path=str(renamed),
            matrix_sha256=sha256_file(renamed)))
        assert any("must reference" in p for p in problems)

    def test_digest_mismatch_rejected(self, doc_paths, tmp_path):
        other = doc_paths[0] / gates.EXPECTED_POLICY_NAME
        other.write_text("different bytes", encoding="utf-8")
        problems = design_approval_problems(_approval(
            doc_paths, matrix_sha256=sha256_file(other)))
        assert any("digest mismatch" in p for p in problems)

    def test_symlink_path_rejected(self, doc_paths):
        root, matrix, _ = doc_paths
        realdir = root / "sub"
        realdir.mkdir()
        real = realdir / gates.EXPECTED_MATRIX_NAME
        real.write_text("x", encoding="utf-8")
        matrix.unlink()
        matrix.symlink_to(real)
        problems = design_approval_problems(_approval(
            doc_paths, _matrix_sha256=sha256_file(real)))
        assert any("symlink" in p for p in problems)

    def test_bad_dates_and_scope_rejected(self, doc_paths):
        assert design_approval_problems(_approval(
            doc_paths, source_review_date="Sept 14"))
        assert design_approval_problems(_approval(
            doc_paths, approved_at="2026-09-14 00:00"))
        assert design_approval_problems(_approval(
            doc_paths, approval_scope="full_authorization"))
        assert design_approval_problems(_approval(
            doc_paths, selected_pilot_rule="glof_is_the_pilot"))
        assert design_approval_problems(_approval(
            doc_paths, approver_attestation=""))

    def test_no_approval_no_envelope(self):
        with pytest.raises(DesignApprovalError):
            build_claim_envelope("env-1", "INTAKE_COMPLETE", {},
                                 {"human_approved": False})


class TestEnvelope:
    def test_envelope_flags_and_digests(self, doc_paths):
        env = build_claim_envelope(
            "env-1", "METADATA_REVIEW_COMPLETE", {"src": _clean_record()},
            _approval(doc_paths))
        assert env["research_diagnostic_only"] is True
        assert env["claim_scope"] == \
            "research_only_no_operational_authorization"
        assert env["promotion_eligible"] is False
        assert env["production_authorized"] is False
        assert env["warning_path_authorized"] is False
        assert env["human_approved"] is True
        assert env["approval_scope"] == "design_review_only"
        assert len(env["record_digests"]["src"]) == 64
        assert len(env["envelope_sha256"]) == 64

    def test_ready_like_status_rejected(self, doc_paths):
        for bad in ("READY", "FMX_READY", "B_TO_C_READY",
                    "PRODUCTION_READY", "SCIENTIFICALLY_VALIDATED",
                    "WARNING_READY", "AUTHORITY_APPROVED",
                    "SOURCE_MATRIX_COMPLETE"):
            assert envelope_status_problems(bad), bad
            with pytest.raises((ValueError, DesignApprovalError)):
                build_claim_envelope("e", bad, {}, _approval(doc_paths))

    def test_neutral_statuses_accepted(self):
        assert envelope_status_problems("INTAKE_COMPLETE") == []
        assert envelope_status_problems("FMX_BLOCKED_CUTOFF") == []
        assert envelope_status_problems(
            "NO_QUALIFYING_PILOT_SOURCE") == []
        assert envelope_status_problems(
            "METADATA_REVIEW_COMPLETE") == []

    def test_direct_constructor_rejects_malformed(self):
        for flag in ("promotion_eligible", "production_authorized",
                     "warning_path_authorized"):
            with pytest.raises(ValueError):
                ResearchClaimEnvelopeV0(
                    envelope_id="e", status="INTAKE_COMPLETE",
                    matrix_sha256="a" * 64, policy_sha256="b" * 64,
                    **{flag: True})
        with pytest.raises(ValueError):
            ResearchClaimEnvelopeV0(
                envelope_id="", status="INTAKE_COMPLETE",
                matrix_sha256="a" * 64, policy_sha256="b" * 64)
        with pytest.raises(ValueError):
            ResearchClaimEnvelopeV0(
                envelope_id="e", status="INTAKE_COMPLETE",
                matrix_sha256="zz", policy_sha256="b" * 64)
        with pytest.raises(ValueError):
            ResearchClaimEnvelopeV0(
                envelope_id="e", status="READY",
                matrix_sha256="a" * 64, policy_sha256="b" * 64)

    def test_blockers_bar_execution_statuses(self, doc_paths):
        blocked = _approval(
            doc_paths, unresolved_blockers=["license unresolved"])
        env = build_claim_envelope("e", "DESIGN_DRAFT_COMPLETE", {},
                                   blocked)
        assert env["unresolved_blockers"] == ["license unresolved"]
        with pytest.raises(ValueError):
            build_claim_envelope("e", "INTAKE_COMPLETE", {}, blocked)
        with pytest.raises(ValueError):
            build_claim_envelope("e", "FORECAST_EXPERIMENT_ONLY", {},
                                 blocked)

    def test_empty_execution_envelope_rejected(self, doc_paths):
        # B04: execution statuses need their required record graph.
        for status in ("INTAKE_COMPLETE", "EVENT_INTAKE_VALIDATED",
                       "FORECAST_EXPERIMENT_ONLY",
                       "DESCRIPTIVE_REGIME_ONLY",
                       "METADATA_REVIEW_COMPLETE"):
            with pytest.raises(ValueError):
                build_claim_envelope("e", status, {},
                                     _approval(doc_paths))

    def test_status_requires_typed_records(self, doc_paths):
        # METADATA_REVIEW_COMPLETE requires a SourceRecordV0; an
        # EventLabelV0 alone does not satisfy it.
        label = EventLabelV0(
            event_id="e1", vertical_id="glof", source_id="s",
            source_version="v1", event_time_precision="interval",
            event_time_basis="scene pair",
            geometry_role="deposit_polygon", basin_id="kosi",
            uncertainty_seconds=12 * 86400,
            event_time_start="2020-01-01T00:00:00Z",
            event_time_end="2020-01-13T00:00:00Z")
        with pytest.raises(ValueError):
            build_claim_envelope("e", "METADATA_REVIEW_COMPLETE",
                                 {"lbl": label}, _approval(doc_paths))

    def test_arbitrary_mapping_record_rejected(self, doc_paths):
        with pytest.raises(ValueError):
            build_claim_envelope("e", "METADATA_REVIEW_COMPLETE",
                                 {"bad": {"a": 1}}, _approval(doc_paths))

    def test_invalid_record_rejected(self, doc_paths):
        bad = SourceRecordV0(source_id="", provider="p", doi_or_url="u")
        with pytest.raises(ValueError):
            build_claim_envelope("e", "METADATA_REVIEW_COMPLETE",
                                 {"src": bad}, _approval(doc_paths))

    def test_cross_record_foreign_keys(self, doc_paths):
        src = _clean_record()
        label = EventLabelV0(
            event_id="e1", vertical_id="glof", source_id="ghost",
            source_version="v1", event_time_precision="interval",
            event_time_basis="scene pair",
            geometry_role="deposit_polygon", basin_id="kosi",
            uncertainty_seconds=12 * 86400,
            event_time_start="2020-01-01T00:00:00Z",
            event_time_end="2020-01-13T00:00:00Z")
        with pytest.raises(ValueError):
            build_claim_envelope(
                "e", "METADATA_REVIEW_COMPLETE",
                {"src": src, "lbl": label}, _approval(doc_paths))


class TestSourceEvidenceBinding:
    def test_verified_requires_real_sidecar_bytes(self, doc_paths,
                                                tmp_path):
        root, _, _ = doc_paths
        sidecar = root / "sidecar.json"
        sidecar.write_text('{"evidence": true}', encoding="utf-8")
        good = SourceRecordV0(
            source_id="s", provider="p", doi_or_url="u",
            posture="EVIDENCE_VERIFIED", version="v1",
            as_of_date="2026-09-01", license_id="CC-BY-4.0",
            redistribution="yes", geography="Nepal",
            temporal_coverage="1972-2025",
            event_time_class="EXACT_DAY",
            spatial_semantics="generalized slope point",
            observation_method="literature synthesis",
            non_event_frame="lake inventory", update_cadence="annual",
            access_status="open",
            evidence_sidecar_path=str(sidecar),
            evidence_sidecar_sha256=sha256_file(sidecar),
            evidence_as_of="2026-09-14",
            evidence_review_state="INDEPENDENTLY_VERIFIED")
        assert good.problems() == []
        assert source_evidence_problems(good, evidence_root=root) == []
        fake = SourceRecordV0(
            source_id="s", provider="p", doi_or_url="u",
            posture="EVIDENCE_VERIFIED", version="v1",
            as_of_date="2026-09-01", license_id="CC-BY-4.0",
            redistribution="yes", geography="Nepal",
            temporal_coverage="1972-2025",
            event_time_class="EXACT_DAY",
            spatial_semantics="x", observation_method="x",
            non_event_frame="x", update_cadence="annual",
            access_status="open",
            evidence_sidecar_path=str(sidecar),
            evidence_sidecar_sha256="f" * 64,
            evidence_as_of="2026-09-14",
            evidence_review_state="INDEPENDENTLY_VERIFIED")
        assert any("mismatch" in p
                   for p in source_evidence_problems(
                       fake, evidence_root=root))


class TestFeatureLeakage:
    def _feat(self, **kw):
        base = {"name": "gefs_2t", "namespace": "occurrence",
                "field_class": "meteorological_reforecast",
                "data_class": "REFORECAST",
                "source_lineage": "GEFSv12 reforecast run 00Z",
                "availability_time": 100, "valid_start": 110,
                "valid_end": 200, "vintage_digest": "a" * 64}
        base.update(kw)
        return base

    def test_clean_feature_passes(self):
        assert occurrence_feature_problems([self._feat()],
                                           issue_time=200) == []
        assert forecast_feature_problems([self._feat()],
                                         issue_time=200) == []

    def test_b_priority_rejected(self):
        problems = occurrence_feature_problems(
            [self._feat(name="b_priority_score")], issue_time=200)
        assert any("B-derived" in p for p in problems)

    def test_exposure_namespace_and_denylist(self):
        assert occurrence_feature_problems(
            [self._feat(name="ghsl_x", namespace="exposure")],
            issue_time=200)
        assert occurrence_feature_problems(
            [self._feat(name="built_up_area_total")], issue_time=200)

    def test_missing_lineage_field_class_vintage(self):
        assert occurrence_feature_problems(
            [self._feat(source_lineage="")], issue_time=200)
        assert occurrence_feature_problems(
            [self._feat(field_class="exposure_proxy")], issue_time=200)
        assert occurrence_feature_problems(
            [self._feat(vintage_digest="not-a-hash")], issue_time=200)
        assert occurrence_feature_problems(
            [self._feat(valid_end=50)], issue_time=200)  # inverted

    def test_future_and_nonfinite_availability(self):
        assert occurrence_feature_problems(
            [self._feat(availability_time=500)], issue_time=200)
        assert occurrence_feature_problems(
            [self._feat(availability_time=float("nan"))], issue_time=200)

    def test_forecast_class_gate(self):
        assert forecast_feature_problems(
            [self._feat(data_class="REANALYSIS")], issue_time=200)
        assert forecast_feature_problems(
            [self._feat(data_class="CURRENT_FEED")], issue_time=200)
        # catalog labels are targets, not predictors
        assert forecast_feature_problems(
            [{"name": "event_label", "namespace": "occurrence",
              "field_class": "catalog_label", "data_class": "LABEL",
              "source_lineage": "inventory", "availability_time": 10,
              "valid_start": 0, "valid_end": 1}], issue_time=200)
        # empty matrix is not an experiment
        assert forecast_feature_problems([], issue_time=200)


class TestCascadeAtomicity:
    def test_split_violation_and_unmapped(self):
        assert cascade_atomicity_problems(
            {"e1": "train", "e2": "test"}, {"g1": ["e1", "e2"]})
        assert cascade_atomicity_problems(
            {"e1": "train"}, {"g1": ["e1", "e2"]})

    def test_duplicate_and_invalid_split(self):
        assert cascade_atomicity_problems(
            {"e1": "train"}, {"g1": ["e1"], "g2": ["e1"]})
        assert cascade_atomicity_problems(
            {"e1": "staging"}, {"g1": ["e1"]})

    def test_complete_atomic_ok(self):
        assert cascade_atomicity_problems(
            {"e1": "train", "e2": "train"},
            {"g1": ["e1", "e2"]}) == []


class TestClaimScan:
    def test_forbidden_content_detected(self):
        assert scan_claims_text('{"status": "FMX_READY"}')
        assert scan_claims_text('{"status": "fmx-ready"}')
        assert scan_claims_text('{"warning-path-authorized": true}')
        assert scan_claims_text("issues an operational warning now")
        assert scan_claims_text('{"status": "scientifically validated"}')
        assert scan_claims_text('{"readiness": "production_ready"}')

    def test_clean_content_passes(self):
        assert scan_claims_text(
            '{"status": "DESIGN_DRAFT_COMPLETE", '
            '"warning_path_authorized": false, "b_status": '
            '"B_TO_C_BLOCKED"}') == []
