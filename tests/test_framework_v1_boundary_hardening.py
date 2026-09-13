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
    yield
    rq.uninstall_import_guard()


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
