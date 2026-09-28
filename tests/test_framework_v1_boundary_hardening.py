"""BOUND-01 lint expansion + BOUND-02 runtime import guard tests
(fail-closed)."""
from __future__ import annotations

import importlib
import importlib.util
import sys

import pytest

from nepal.framework_v1 import research_boundaries as rb
from nepal.framework_v1 import runtime_quarantine as rq


def _doc(**kw):
    base = {"research_diagnostic_only": True,
            "promotion_eligible": False}
    base.update(kw)
    return base


class TestOperationalFlagVariants:
    def test_nested_production_authorization_rejected(self):
        ok, _ = rb.lint_research_claims(
            _doc(a={"production_authorization": True}))
        assert not ok

    def test_deeply_nested_warning_authorized_rejected(self):
        ok, _ = rb.lint_research_claims(
            _doc(a={"b": [{"c": {"warning_authorized": True}}]}))
        assert not ok

    def test_operational_authorized_rejected(self):
        ok, _ = rb.lint_research_claims(
            _doc(notes={"operational_authorized": True}))
        assert not ok

    def test_deploy_authorized_rejected(self):
        ok, _ = rb.lint_research_claims(_doc(deploy_authorized=True))
        assert not ok

    def test_deployment_authorized_rejected(self):
        ok, _ = rb.lint_research_claims(
            _doc(inner={"deployment_authorized": "yes"}))
        assert not ok

    def test_nested_production_authorized_rejected(self):
        """Regression: the existing flag check is recursive."""
        ok, _ = rb.lint_research_claims(
            _doc(notes={"production_authorized": True}))
        assert not ok

    def test_flag_name_as_string_value_rejected(self):
        ok, _ = rb.lint_research_claims(
            _doc(status="warning_authorized"))
        assert not ok

    def test_all_false_flags_pass(self):
        ok, problems = rb.lint_research_claims(_doc(
            production_authorized=False,
            warning_authorized=False,
            operational_authorized=False,
            deploy_authorized=False,
            deployment_authorized=False,
            production_authorization=False))
        assert ok, problems


class TestRankedKeyVariants:
    def test_top_five_rejected(self):
        ok, _ = rb.lint_research_claims(_doc(top_five=[0.9, 0.8]))
        assert not ok

    def test_loo_top5_rejected(self):
        ok, _ = rb.lint_research_claims(_doc(loo_top5=[{"x": 1}]))
        assert not ok

    def test_top5_rejected(self):
        ok, _ = rb.lint_research_claims(_doc(top5=[1, 2, 3]))
        assert not ok

    def test_ranked_top5_rejected(self):
        ok, _ = rb.lint_research_claims(_doc(ranked_top5={"a": 1}))
        assert not ok

    def test_digest_hint_key_still_permitted(self):
        ok, problems = rb.lint_research_claims(_doc(
            b_reference={"ranked_array_canonical_sha256": "ab" * 32}))
        assert ok, problems


class TestProseTokens:
    def test_authorized_for_production_rejected(self):
        ok, _ = rb.lint_research_claims(
            _doc(note="model is authorized for production use"))
        assert not ok

    def test_approved_for_warning_rejected(self):
        ok, _ = rb.lint_research_claims(
            _doc(note="approved for warning dissemination"))
        assert not ok

    def test_authorized_for_warning_rejected(self):
        ok, _ = rb.lint_research_claims(
            _doc(note="authorized for warning"))
        assert not ok

    def test_warning_authorized_prose_rejected(self):
        ok, _ = rb.lint_research_claims(
            _doc(note="this output is warning authorized"))
        assert not ok

    def test_cleared_for_deployment_rejected(self):
        ok, _ = rb.lint_research_claims(
            _doc(note="cleared for deployment"))
        assert not ok

    def test_prose_token_in_key_rejected(self):
        ok, _ = rb.lint_research_claims(
            _doc(**{"cleared for deployment": False}))
        assert not ok

    def test_warning_threshold_key_rejected(self):
        """Regression: pre-existing token still fails on keys."""
        ok, _ = rb.lint_research_claims(
            _doc(config={"warning_threshold": 0.8}))
        assert not ok


class TestLegitimatePayload:
    def test_clean_research_doc_still_passes(self):
        ok, problems = rb.lint_research_claims(_doc(
            b_status="B_TO_C_BLOCKED",
            b_reference={"ranked_array_canonical_sha256": "ab" * 32},
            claims=["contract/scaffold engineering only; no scientific "
                    "validation performed or implied"],
            legacy_output={"method": "gmm",
                           "descriptive_only": True,
                           "disclaimer": "retrospective descriptive "
                                         "statistic; not a precursor"}))
        assert ok, problems

    def test_human_approved_source_packet_passes_lint(self):
        """MEC source_packet legitimately carries human_approved=true."""
        ok, problems = rb.lint_research_claims(_doc(
            source_packet={"approved_by": "operator",
                           "human_approved": True}))
        assert ok, problems


def _quarantined_names():
    return (".".join(("nepal", "run_nepal_test")),
                "_".join(("multi", "event", "validation")))


@pytest.fixture(autouse=True)
def _guard_cleanup():
    # Suite-order safety: an earlier module may have imported the
    # quarantined names (or their nepal-prefixed siblings), in which
    # case sys.modules short-circuits the meta-path guard and the guard
    # never sees the import attempt.
    names = set(_quarantined_names())
    names |= {"nepal." + name.split(".")[-1] for name in names}
    names.add("nepal.multi_event_validation")
    stashed = {name: sys.modules.pop(name) for name in names
               if name in sys.modules}
    yield
    rq.uninstall_import_guard()
    sys.modules.update(stashed)


class TestRuntimeImportGuard:
    def test_not_installed_by_default(self):
        assert not rq.guard_active()

    def test_install_activates_guard(self):
        finder = rq.install_import_guard()
        assert rq.guard_active()
        assert finder in sys.meta_path

    def test_importlib_import_blocked(self):
        rq.install_import_guard()
        for name in _quarantined_names():
            with pytest.raises(ImportError):
                importlib.import_module(name)

    def test_dunder_import_blocked(self):
        rq.install_import_guard()
        for name in _quarantined_names():
            with pytest.raises(ImportError):
                __import__(name)

    def test_existing_on_disk_module_blocked(self):
        """The real ``nepal`` legacy harness exists on disk; a finder
        appended after PathFinder would never see it."""
        rq.install_import_guard()
        name = _quarantined_names()[0]
        with pytest.raises(ImportError) as exc:
            importlib.util.find_spec(name)
        assert "quarantined" in str(exc.value)

    def test_double_install_idempotent(self):
        f1 = rq.install_import_guard()
        depth = len(sys.meta_path)
        f2 = rq.install_import_guard()
        assert f1 is f2
        assert len(sys.meta_path) == depth
        assert sum(1 for f in sys.meta_path if f is f1) == 1

    def test_uninstall_restores_resolution(self):
        rq.install_import_guard()
        rq.uninstall_import_guard()
        assert not rq.guard_active()
        spec = importlib.util.find_spec(_quarantined_names()[0])
        assert spec is not None

    def test_uninstall_when_not_installed_is_noop(self):
        rq.uninstall_import_guard()
        assert not rq.guard_active()

    def test_benign_import_not_blocked(self):
        rq.install_import_guard()
        json = importlib.import_module("json")
        assert json.loads("1") == 1
        assert importlib.import_module(
            "nepal.framework_v1.catalog") is not None


class TestNormalizedProsePhrases:
    """BOUND-03: space-separated operational prose is rejected after
    ``[_-]``/whitespace normalization of values and dotted keys."""

    @pytest.mark.parametrize("note", [
        "warning authorization established",
        "production ready",
        "this model is operationally ready",
        "cleared for deployment",
        "the pipeline is warning ready",
        "cleared for warning release",
        "approved for deployment",
        "ready for production",
        "ready for warning",
        "ready for deployment",
        "fit for production",
        "fit for warning",
        "scientifically validated against held-out events",
        "authority approved for release",
        "deploy to production",
        "the pipeline may now issue warnings",
        "warning issuance is enabled",
        "authorised for operational use",
        "approved for warning dissemination",
    ])
    def test_forbidden_prose_value_rejected(self, note):
        ok, problems = rb.lint_research_claims(_doc(note=note))
        assert not ok and problems

    def test_hyphenated_prose_value_rejected(self):
        """Hyphens normalize to spaces: 'production-ready' is a claim."""
        ok, _ = rb.lint_research_claims(_doc(note="production-ready build"))
        assert not ok

    def test_underscored_prose_value_rejected(self):
        ok, _ = rb.lint_research_claims(
            _doc(note="warning_authorization granted"))
        assert not ok

    def test_mixed_separator_value_rejected(self):
        ok, _ = rb.lint_research_claims(
            _doc(note="warning-authorized_for deployment"))
        assert not ok

    def test_uppercase_prose_value_rejected(self):
        ok, _ = rb.lint_research_claims(_doc(note="PRODUCTION READY"))
        assert not ok

    def test_deeply_nested_prose_value_rejected(self):
        ok, _ = rb.lint_research_claims(
            _doc(a={"b": [{"note": "operationally ready"}]}))
        assert not ok

    @pytest.mark.parametrize("key", [
        "warning_ready",
        "warning ready",
        "warning-ready",
        "production ready status",
        "warning_ready: true",
        "cleared for deployment",
        "operationally ready flag",
    ])
    def test_forbidden_phrase_in_key_rejected(self, key):
        ok, problems = rb.lint_research_claims(_doc(**{key: False}))
        assert not ok and problems

    def test_nested_key_phrase_rejected(self):
        ok, _ = rb.lint_research_claims(
            _doc(meta={"warning ready status": "off"}))
        assert not ok

    def test_second_unnegated_occurrence_still_rejected(self):
        """A negated mention does not launder a later bare claim."""
        ok, _ = rb.lint_research_claims(_doc(
            note="not production ready; production ready"))
        assert not ok

    @pytest.mark.parametrize("note", [
        "no warning readiness is established",
        "not production ready",
        "warning_path_authorized is false",
        "no authority approval",
        "research diagnostic only",
        "without warning authorization",
        "no warning authorization",
        "never production ready",
        "non-production ready",
        "does not issue warnings",
        "no scientific validation, warning, production, or authority "
        "readiness is established",
        "no warning, production, or authority approval is established",
        "no operational warning or production authorization",
    ])
    def test_negated_or_limitation_language_passes(self, note):
        ok, problems = rb.lint_research_claims(_doc(note=note))
        assert ok, problems

    def test_clean_doc_with_legit_no_claims_passes(self):
        """Regression: limitation strings already shipped in envelopes
        must still lint clean."""
        ok, problems = rb.lint_research_claims(_doc(no_claims=[
            "no warning, production, or authority readiness",
            "framework implementation evidence is not scientific "
            "validation",
            "frozen feature-matrix metadata; freeze does not imply "
            "scientific or operational readiness",
        ]))
        assert ok, problems


class TestClaimVariantKeys:
    """BOUND-05: authorization/ready-shaped keys may only carry falsy
    values; normalized string values that are themselves forbidden
    claims reject."""

    @pytest.mark.parametrize("key", [
        "B_TO_C_READY", "b_to_c_ready", "Fmx Ready", "fmx-ready",
        "warning_ready", "production ready", "production-ready",
        "t2_ready", "e_ready", "f_ready", "eligible",
        "authorized", "authorised", "enabled", "cleared",
        "deployed", "gate authorized flag",
    ])
    def test_truthy_value_under_auth_key_rejected(self, key):
        ok, problems = rb.lint_research_claims(_doc(**{key: True}))
        assert not ok and problems

    def test_nested_claim_variant_key_rejected(self):
        ok, _ = rb.lint_research_claims(
            _doc(a={"B_TO_C_READY": True}))
        assert not ok

    def test_deeply_nested_claim_variant_key_rejected(self):
        ok, _ = rb.lint_research_claims(
            _doc(a={"b": [{"c": {"fmx_ready": True}}]}))
        assert not ok

    @pytest.mark.parametrize("value", [1, "yes", "true", "TRUE", "on",
                                       ["x"], {"x": 1}, 0.5])
    def test_non_string_truthy_values_rejected(self, value):
        ok, _ = rb.lint_research_claims(_doc(fmx_ready=value))
        assert not ok

    @pytest.mark.parametrize("value", [False, None, "false", "FALSE",
                                       "no", "none", "", 0, "0"])
    def test_falsy_values_under_auth_key_pass(self, value):
        ok, problems = rb.lint_research_claims(
            _doc(b_to_c_ready=value, fmx_ready=value))
        assert ok, problems

    def test_mixed_case_unicode_key_rejected(self):
        ok, _ = rb.lint_research_claims(_doc(**{"FmX_ReAdY": True}))
        assert not ok

    @pytest.mark.parametrize("key", [
        "human_approved", "approved_by", "approved_at",
        "group_disjoint", "assigned_before_filtering",
        "exact_day15_supported", "research_diagnostic_only",
        "required", "group_separation",
    ])
    def test_allowlisted_truthy_keys_pass(self, key):
        ok, problems = rb.lint_research_claims(_doc(**{key: True}))
        assert ok, problems

    @pytest.mark.parametrize("value", [
        "b to c ready", "B_TO_C_READY".lower().replace("_", " "),
        "fmx ready", "fmx-ready", "e ready", "f ready", "t2 ready",
        "warning ready", "production ready", "scientifically validated",
        "authority approved", "all gaps closed", "no remaining gaps",
        "validation passed",
    ])
    def test_forbidden_claim_value_rejected(self, value):
        ok, problems = rb.lint_research_claims(_doc(status=value))
        assert not ok and problems

    @pytest.mark.parametrize("value", [
        "B_TO_C_BLOCKED", "FMX_BLOCKED_PENDING_EXPLICIT_FREEZE",
        "b to c blocked", "not fmx ready", "e blocked", "f blocked",
        "t2 real v1", "PASS", "BLOCKED_PENDING_FMX",
    ])
    def test_blocked_or_negated_values_pass(self, value):
        ok, problems = rb.lint_research_claims(_doc(status=value))
        assert ok, problems

    def test_blocked_key_variants_pass(self):
        ok, problems = rb.lint_research_claims(_doc(
            fmx_status="FMX_BLOCKED_PENDING_EXPLICIT_FREEZE",
            b_status="B_TO_C_BLOCKED",
            e_status="E_BLOCKED", f_status="F_BLOCKED"))
        assert ok, problems


class TestRankedPayloadKeys:
    """BOUND-06: ranked/score-named keys may not carry list/dict
    payloads; digest-keyed references and scalar metadata are legal."""

    @pytest.mark.parametrize("key", [
        "scores", "score", "ranked", "ranking", "top five", "top5",
        "priority", "priorities", "anomaly scores", "screen scores",
        "ranked array", "ranked list", "anomaly_scores",
        "screen-scores",
    ])
    def test_ranked_key_with_list_rejected(self, key):
        ok, problems = rb.lint_research_claims(_doc(**{key: [1, 2, 3]}))
        assert not ok and problems

    def test_ranked_key_with_dict_rejected(self):
        ok, _ = rb.lint_research_claims(
            _doc(**{"anomaly scores": {"AU-1": 0.9}}))
        assert not ok

    def test_scores_payload_rejected(self):
        ok, _ = rb.lint_research_claims(_doc(scores=[1, 2, 3]))
        assert not ok

    def test_nested_ranked_payload_rejected(self):
        ok, _ = rb.lint_research_claims(
            _doc(features={"ranked array": [0.9, 0.8]}))
        assert not ok

    @pytest.mark.parametrize("key", [
        "ranked_array_canonical_sha256", "ranked_payload_sha256",
        "loo_top5_digest", "scores_sha256", "ranked hash",
        "score_digest",
    ])
    def test_digest_keyed_ranked_fields_pass(self, key):
        ok, problems = rb.lint_research_claims(
            _doc(**{key: "ab" * 32}))
        assert ok, problems

    def test_ranked_bool_metadata_passes(self):
        """Scalar metadata like ranking_rerun=false is not a payload.
        (A bare ``ranked`` key is already rejected by the pre-existing
        exact-key check, bool or not.)"""
        ok, problems = rb.lint_research_claims(
            _doc(ranking_rerun=False, priority_rerun=False))
        assert ok, problems

    def test_scalar_string_under_ranked_key_passes(self):
        ok, problems = rb.lint_research_claims(
            _doc(top_five="withheld; digest reference only"))
        assert ok, problems


class TestMethodNameValues:
    """BOUND-06: descriptive-only method names in prose values need an
    inline disclaimer token in the same string."""

    @pytest.mark.parametrize("value", [
        "isolation forest", "isolation_forest", "ISOLATION FOREST",
        "gaussian mixture", "gmm", "GMM", "change point",
        "changepoint", "change-point", "bayesian change",
        "anomaly detection", "screening model",
        "baseline uses isolation forest",
    ])
    def test_method_name_without_disclaimer_rejected(self, value):
        ok, problems = rb.lint_research_claims(_doc(note=value))
        assert not ok and problems

    @pytest.mark.parametrize("value", [
        "isolation forest diagnostic only",
        "gmm descriptive statistic",
        "change point analysis; research only",
        "screening model, screening only",
        "anomaly detection is non authorizing",
    ])
    def test_method_name_with_disclaimer_passes(self, value):
        ok, problems = rb.lint_research_claims(_doc(note=value))
        assert ok, problems

    def test_method_key_exempt_bare_identifier(self):
        """A bare method identifier under ``method`` is governed by the
        structured descriptive_only/disclaimer check, not the prose
        rule."""
        ok, problems = rb.lint_research_claims(_doc(legacy_output={
            "method": "gmm", "descriptive_only": True,
            "disclaimer": "retrospective descriptive statistic"}))
        assert ok, problems

    def test_method_key_undisclaimed_still_rejected(self):
        """The structured check still rejects a bare method mapping."""
        ok, _ = rb.lint_research_claims(
            _doc(legacy_output={"method": "gmm"}))
        assert not ok

    def test_legit_docs_still_pass(self):
        ok, problems = rb.lint_research_claims(_doc(
            b_status="B_TO_C_BLOCKED",
            fmx_status="FMX_BLOCKED_PENDING_EXPLICIT_FREEZE",
            e_status="E_BLOCKED", f_status="F_BLOCKED",
            ranking_rerun=False,
            inherited_state={"b_status": "B_TO_C_BLOCKED",
                             "ranking_rerun": False},
            b_reference={"ranked_array_canonical_sha256": "ab" * 32},
            source_packet={"approved_by": "operator",
                           "human_approved": True,
                           "approved_at": "2026-09-13"},
            label_spec={"adjudication": {"required": True}},
            holdout={"assigned_before_filtering": True,
                     "event_separation": {"group_disjoint": True}}))
        assert ok, problems
