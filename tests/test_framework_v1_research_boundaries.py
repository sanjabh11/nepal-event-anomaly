"""W1 quarantine guard + research claim linter tests (fail-closed)."""
from __future__ import annotations

import pytest

from nepal.framework_v1 import research_boundaries as rb


class TestQuarantineGuard:
    def test_clean_source_passes(self):
        ok, hits = rb.scan_source_for_quarantined_imports(
            "import json\nfrom nepal.framework_v1 import provenance\n")
        assert ok and hits == []

    def test_direct_import_rejected(self):
        ok, hits = rb.scan_source_for_quarantined_imports(
            "from nepal import multi_event_validation\n")
        assert not ok and hits

    def test_module_import_rejected(self):
        ok, hits = rb.scan_source_for_quarantined_imports(
            "import nepal.run_nepal_test as t\n")
        assert not ok

    def test_dynamic_import_rejected(self):
        ok, hits = rb.scan_source_for_quarantined_imports(
            "m = __import__('nepal.multi_event_validation')\n")
        assert not ok

    def test_importlib_rejected(self):
        ok, hits = rb.scan_source_for_quarantined_imports(
            "import importlib\nm = importlib.import_module("
            "'nepal.run_nepal_test')\n")
        assert not ok

    def test_string_reference_rejected(self):
        ok, hits = rb.scan_source_for_quarantined_imports(
            "LEGACY = 'nepal.multi_event_validation'\n")
        assert not ok

    def test_syntax_error_fails_closed(self):
        ok, hits = rb.scan_source_for_quarantined_imports("def broken(:\n")
        assert not ok


class TestClaimLinter:
    def _doc(self, **kw):
        base = {"research_diagnostic_only": True,
                "promotion_eligible": False}
        base.update(kw)
        return base

    def test_clean_research_doc_passes(self):
        ok, problems = rb.lint_research_claims(self._doc())
        assert ok, problems

    def test_missing_diagnostic_flag_rejected(self):
        ok, problems = rb.lint_research_claims({"promotion_eligible": False})
        assert not ok
        assert any("research_diagnostic_only" in p for p in problems)

    def test_false_diagnostic_flag_rejected(self):
        ok, _ = rb.lint_research_claims(
            {"research_diagnostic_only": False})
        assert not ok

    def test_bare_ready_rejected(self):
        ok, problems = rb.lint_research_claims(
            self._doc(status="READY"))
        assert not ok

    def test_nested_ready_rejected(self):
        ok, _ = rb.lint_research_claims(
            self._doc(nested={"deep": [{"status": "READY"}]}))
        assert not ok

    def test_b_to_c_ready_rejected(self):
        ok, _ = rb.lint_research_claims(self._doc(gate="B_TO_C_READY"))
        assert not ok

    def test_b_to_c_blocked_permitted(self):
        ok, problems = rb.lint_research_claims(
            self._doc(inherited_b_status="B_TO_C_BLOCKED"))
        assert ok, problems

    def test_scientific_validation_claim_rejected(self):
        ok, _ = rb.lint_research_claims(
            self._doc(note="SCIENTIFICALLY_VALIDATED"))
        assert not ok

    def test_production_ready_rejected(self):
        ok, _ = rb.lint_research_claims(
            self._doc(status="PRODUCTION_READY"))
        assert not ok

    def test_authority_approved_rejected(self):
        ok, _ = rb.lint_research_claims(
            self._doc(approval="AUTHORITY_APPROVED"))
        assert not ok

    def test_warning_ready_rejected(self):
        ok, _ = rb.lint_research_claims(
            self._doc(status="WARNING_READY"))
        assert not ok

    def test_warning_threshold_rejected(self):
        ok, problems = rb.lint_research_claims(
            self._doc(config={"warning_threshold": 0.8}))
        assert not ok

    def test_evacuation_language_rejected(self):
        ok, _ = rb.lint_research_claims(
            self._doc(plan="evacuation corridor map"))
        assert not ok

    def test_alert_language_rejected(self):
        ok, _ = rb.lint_research_claims(
            self._doc(mode="alert_configuration"))
        assert not ok

    def test_production_authorized_true_rejected(self):
        ok, _ = rb.lint_research_claims(
            self._doc(production_authorized=True))
        assert not ok

    def test_promotion_eligible_true_rejected(self):
        ok, _ = rb.lint_research_claims(
            {"research_diagnostic_only": True,
             "promotion_eligible": True})
        assert not ok

    def test_fmx_ready_without_token_rejected(self):
        ok, problems = rb.lint_research_claims(
            self._doc(fmx_status="FMX_READY"))
        assert not ok
        assert any("freeze token" in p or "FMX_READY" in p
                   for p in problems)

    def test_ranked_array_as_feature_rejected(self):
        ok, problems = rb.lint_research_claims(
            self._doc(features={"ranked": [{"a": 1}, {"b": 2}]}))
        assert not ok

    def test_priority_values_as_feature_rejected(self):
        ok, _ = rb.lint_research_claims(
            self._doc(features={"priority_index": [0.9, 0.8]}))
        assert not ok

    def test_b_digest_reference_permitted(self):
        ok, problems = rb.lint_research_claims(self._doc(
            b_reference={"ranked_array_canonical_sha256": "ab" * 32}))
        assert ok, problems

    def test_gmm_output_requires_disclaimer(self):
        ok, problems = rb.lint_research_claims(
            self._doc(legacy_output={"method": "gmm", "scores": [1, 2]}))
        assert not ok
        ok2, _ = rb.lint_research_claims(self._doc(legacy_output={
            "method": "gmm", "scores": [1, 2],
            "descriptive_only": True,
            "disclaimer": "retrospective descriptive statistic; not a "
                          "precursor or validated predictor"}))
        assert ok2

    def test_isolation_forest_requires_disclaimer(self):
        ok, _ = rb.lint_research_claims(
            self._doc(legacy_output={"method": "isolation_forest"}))
        assert not ok

    def test_changepoint_requires_disclaimer(self):
        ok, _ = rb.lint_research_claims(
            self._doc(legacy_output={"method": "change_point"}))
        assert not ok

    def test_anomaly_requires_disclaimer(self):
        ok, _ = rb.lint_research_claims(
            self._doc(legacy_output={"method": "retrospective_anomaly"}))
        assert not ok
