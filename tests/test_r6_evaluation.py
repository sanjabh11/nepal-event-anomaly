"""Round-6 evaluation hardening tests (synthetic fixtures only).

Covers the audit-confirmed residuals in
``nepal.experiment_v0.evaluation``:

* EVAL-03 — forecast-regime binding: ``forecast_regime_digest`` is an
  optional declaration field, but ``_decl_complete`` requires it
  non-empty 64-hex before the report can emit
  ``FORECAST_EXPERIMENT_ONLY``.
* EVAL-05a — declared mode fails closed on unbound baselines: under a
  bound declaration ``baseline_evidence`` is required, must cover
  every supplied baseline, and every entry must carry
  ``fit_provenance == "bound"`` with a digest recomputing over the
  aligned vector.  Fixture mode (no declaration) keeps the
  degrade-and-cap behavior.
* EVAL-05b — feature-row membership cross-check: the optional
  ``feature_row_keys`` universe must recompute to the declared
  ``feature_row_keys_digest``, and every baseline evidence entry's
  declared fit ``row_keys`` must be drawn from it.

Nothing here is a real-data finding.
"""
from __future__ import annotations

import pytest

from nepal.experiment_v0.evaluation import (
    ForecastExperimentDeclaration, evaluate)
from nepal.research_v0._hashing import sha256_canonical

from tests.fixtures import synthetic_exp_b3 as fx

_REGIME_DIGEST = "c" * 64


def _eval_kwargs(design):
    cases = design["cases"]
    return {"holdout": design["holdout"],
            "baseline_probs": fx.make_baseline_probs(cases),
            "admitted_vintages": fx.admitted_for_cases(cases),
            "opportunities": design.get(
                "opportunities", fx.opportunity_registry(cases)),
            "unit_basins": design.get(
                "unit_basins", fx.unit_basins_for(cases)),
            "region_basins": design.get(
                "region_basins",
                fx.region_basins_for(
                    sorted({c.region for c in cases}))),
            "n_boot": 20, "seed": 7}


def _bound_evidence(cases, row_keys=None):
    """baseline_evidence whose digests recompute over the supplied
    vectors and whose fit provenance is ``"bound"``; ``row_keys``
    optionally declares each baseline's fit-row membership."""
    probs = fx.make_baseline_probs(cases)
    out = {}
    for name, vec in probs.items():
        ev = {"digest": sha256_canonical(
                  [round(float(v), 9) for v in vec]),
              "fit_provenance": "bound"}
        if row_keys is not None:
            ev["row_keys"] = tuple(row_keys)
        out[name] = ev
    return out


def _fully_bound_kwargs(design):
    """A powered-design evaluate() call with every R6 binding in
    place — bound evidence, declared regime digest, declared and
    supplied feature-row universe."""
    cases = design["cases"]
    rows = fx.feature_row_keys_for(cases)
    kw = _eval_kwargs(design)
    kw["experiment"] = fx.experiment_declaration(
        cases, forecast_regime_digest=_REGIME_DIGEST,
        feature_row_keys=rows)
    kw["baseline_evidence"] = _bound_evidence(cases)
    kw["feature_row_keys"] = rows
    return kw


# ---------------------------------------------------------------------
# EVAL-03 — forecast-regime binding in _decl_complete
# ---------------------------------------------------------------------

class TestEval03ForecastRegimeBinding:
    def test_full_binding_unlocks_forecast_status(self):
        design = fx.powered_design()
        report = evaluate(design["cases"], **_fully_bound_kwargs(design))
        assert report.power["powered"] is True
        assert report.status == "FORECAST_EXPERIMENT_ONLY"
        assert report.declaration["mode"] == "declared"
        assert report.declaration["forecast_regime_digest"] == \
            _REGIME_DIGEST
        assert report.declaration["forecast_regime_bound"] is True
        assert report.declaration["feature_row_keys_bound"] is True

    def test_missing_regime_digest_stays_capped(self):
        """Identical bound declaration minus forecast_regime_digest:
        powered and otherwise complete, but the status cannot unlock.
        """
        design = fx.powered_design()
        cases = design["cases"]
        rows = fx.feature_row_keys_for(cases)
        kw = _fully_bound_kwargs(design)
        kw["experiment"] = fx.experiment_declaration(
            cases, feature_row_keys=rows)  # no regime digest
        report = evaluate(cases, **kw)
        assert report.power["powered"] is True
        assert report.status == "UNDERPOWERED_DESCRIPTIVE_ONLY"
        assert report.declaration["forecast_regime_digest"] == ""
        assert report.declaration["forecast_regime_bound"] is False

    def test_malformed_regime_digest_rejects(self):
        decl = ForecastExperimentDeclaration(
            declaration_id="decl-x",
            feature_artifact_digest="a" * 64,
            threshold_record={"threshold": 0.5},
            ablations=("model",),
            vintage_lineage=("b" * 64,),
            forecast_regime_digest="not-hex")
        assert any("forecast_regime_digest" in p
                   for p in decl.problems())
        design = fx.underpowered_design()
        cases = design["cases"]
        payload = fx.experiment_declaration(
            cases, forecast_regime_digest="not-hex")
        kw = _eval_kwargs(design)
        kw["experiment"] = payload
        kw["baseline_evidence"] = _bound_evidence(cases)
        with pytest.raises(ValueError,
                           match="forecast_regime_digest"):
            evaluate(cases, **kw)

    def test_mapping_roundtrip_carries_new_fields(self):
        cases = fx.underpowered_design()["cases"]
        rows = fx.feature_row_keys_for(cases)
        decl = ForecastExperimentDeclaration.from_mapping(
            fx.experiment_declaration(
                cases, forecast_regime_digest=_REGIME_DIGEST,
                feature_row_keys=rows))
        assert decl.forecast_regime_digest == _REGIME_DIGEST
        assert decl.feature_row_keys_digest == sha256_canonical(
            sorted(rows))
        assert decl.problems() == []
        d = decl.to_dict()
        assert d["forecast_regime_digest"] == _REGIME_DIGEST
        assert d["feature_row_keys_digest"] == \
            decl.feature_row_keys_digest


# ---------------------------------------------------------------------
# EVAL-05a — declared mode requires bound baseline evidence
# ---------------------------------------------------------------------

class TestEval05aDeclaredBaselineEvidence:
    def test_declared_without_evidence_rejects(self):
        design = fx.underpowered_design()
        cases = design["cases"]
        kw = _eval_kwargs(design)
        kw["experiment"] = fx.experiment_declaration(cases)
        with pytest.raises(ValueError, match="baseline_evidence"):
            evaluate(cases, **kw)

    def test_declared_partial_evidence_rejects(self):
        design = fx.underpowered_design()
        cases = design["cases"]
        evidence = _bound_evidence(cases)
        del evidence["climatology"]
        kw = _eval_kwargs(design)
        kw["experiment"] = fx.experiment_declaration(cases)
        kw["baseline_evidence"] = evidence
        with pytest.raises(ValueError, match="baseline_evidence"):
            evaluate(cases, **kw)

    def test_declared_unbound_evidence_rejects(self):
        """fit_provenance 'unbound_fixture' under a declaration is a
        hard reject — a declared experiment cannot carry unbound
        caller vectors."""
        design = fx.underpowered_design()
        cases = design["cases"]
        evidence = _bound_evidence(cases)
        evidence["null"]["fit_provenance"] = "unbound_fixture"
        kw = _eval_kwargs(design)
        kw["experiment"] = fx.experiment_declaration(cases)
        kw["baseline_evidence"] = evidence
        with pytest.raises(ValueError, match="bound"):
            evaluate(cases, **kw)

    def test_declared_bad_digest_still_rejects(self):
        design = fx.underpowered_design()
        cases = design["cases"]
        evidence = _bound_evidence(cases)
        evidence["rule"]["digest"] = "0" * 64
        kw = _eval_kwargs(design)
        kw["experiment"] = fx.experiment_declaration(cases)
        kw["baseline_evidence"] = evidence
        with pytest.raises(ValueError, match="recompute"):
            evaluate(cases, **kw)

    def test_declared_bound_evidence_passes(self):
        design = fx.underpowered_design()
        cases = design["cases"]
        kw = _eval_kwargs(design)
        kw["experiment"] = fx.experiment_declaration(cases)
        kw["baseline_evidence"] = _bound_evidence(cases)
        report = evaluate(cases, **kw)
        assert report.declaration["mode"] == "declared"
        for name, ev in report.baseline_provenance.items():
            assert ev["fit_provenance"] == "bound", name
        # powered gate unmet -> still capped, but cleanly evaluated
        assert report.status == "UNDERPOWERED_DESCRIPTIVE_ONLY"

    def test_fixture_mode_without_evidence_still_caps(self):
        """Regression guard: a fixture-only evaluate() keeps the
        degrade-and-cap behavior — unbound caller vectors degrade to
        'caller_supplied_fixture' and never raise."""
        design = fx.powered_design()
        report = evaluate(design["cases"], **_eval_kwargs(design))
        assert report.power["powered"] is True
        assert report.declaration["mode"] == "fixture_only"
        assert report.status == "UNDERPOWERED_DESCRIPTIVE_ONLY"
        for name, ev in report.baseline_provenance.items():
            assert ev["fit_provenance"] == \
                "caller_supplied_fixture", name


# ---------------------------------------------------------------------
# EVAL-05b — feature-row universe cross-verification
# ---------------------------------------------------------------------

class TestEval05bFeatureRowUniverse:
    def test_correct_universe_passes(self):
        design = fx.underpowered_design()
        cases = design["cases"]
        rows = fx.feature_row_keys_for(cases)
        kw = _eval_kwargs(design)
        kw["experiment"] = fx.experiment_declaration(
            cases, feature_row_keys=rows)
        kw["baseline_evidence"] = _bound_evidence(
            cases, row_keys=rows[:2])
        kw["feature_row_keys"] = rows
        report = evaluate(cases, **kw)
        assert report.declaration["feature_row_keys_bound"] is True

    def test_wrong_digest_rejects(self):
        design = fx.underpowered_design()
        cases = design["cases"]
        rows = fx.feature_row_keys_for(cases)
        payload = fx.experiment_declaration(
            cases, feature_row_keys=rows)
        payload["feature_row_keys_digest"] = "0" * 64
        kw = _eval_kwargs(design)
        kw["experiment"] = payload
        kw["baseline_evidence"] = _bound_evidence(cases)
        kw["feature_row_keys"] = rows
        with pytest.raises(ValueError, match="feature_row_keys"):
            evaluate(cases, **kw)

    def test_universe_without_declared_digest_rejects(self):
        """feature_row_keys supplied under a declaration that binds no
        feature_row_keys_digest cannot verify — fail closed."""
        design = fx.underpowered_design()
        cases = design["cases"]
        kw = _eval_kwargs(design)
        kw["experiment"] = fx.experiment_declaration(cases)
        kw["baseline_evidence"] = _bound_evidence(cases)
        kw["feature_row_keys"] = fx.feature_row_keys_for(cases)
        with pytest.raises(ValueError, match="feature_row_keys"):
            evaluate(cases, **kw)

    def test_baseline_row_outside_universe_rejects(self):
        """A baseline asserting a fit row absent from the declared
        feature-row universe can never have fit on real feature
        rows."""
        design = fx.underpowered_design()
        cases = design["cases"]
        rows = fx.feature_row_keys_for(cases)
        evidence = _bound_evidence(cases, row_keys=rows[:1])
        evidence["null"]["row_keys"] = (
            rows[0], "ghost-unit|1999-12-31")
        kw = _eval_kwargs(design)
        kw["experiment"] = fx.experiment_declaration(
            cases, feature_row_keys=rows)
        kw["baseline_evidence"] = evidence
        kw["feature_row_keys"] = rows
        with pytest.raises(ValueError, match="outside"):
            evaluate(cases, **kw)

    def test_declared_digest_without_universe_stays_capped(self):
        """Declaration binds the digest but no feature_row_keys are
        supplied: clean evaluation, never complete."""
        design = fx.powered_design()
        cases = design["cases"]
        rows = fx.feature_row_keys_for(cases)
        kw = _fully_bound_kwargs(design)
        del kw["feature_row_keys"]
        report = evaluate(cases, **kw)
        assert report.power["powered"] is True
        assert report.declaration["feature_row_keys_digest"]
        assert report.declaration["feature_row_keys_bound"] is False
        assert report.status == "UNDERPOWERED_DESCRIPTIVE_ONLY"

    def test_duplicate_and_malformed_keys_reject(self):
        design = fx.underpowered_design()
        cases = design["cases"]
        rows = list(fx.feature_row_keys_for(cases))
        for bad in (rows + [rows[0]], ["unit-without-pipe"], [""]):
            kw = _eval_kwargs(design)
            kw["feature_row_keys"] = bad
            with pytest.raises(ValueError, match="feature_row_keys"):
                evaluate(cases, **kw)

    def test_fixture_mode_universe_subset_still_enforced(self):
        """Even without a declaration the ⊆ check applies when the
        universe is supplied — fit rows must be real feature rows."""
        design = fx.underpowered_design()
        cases = design["cases"]
        rows = fx.feature_row_keys_for(cases)
        evidence = _bound_evidence(
            cases, row_keys=("other-unit|2020-01-01",))
        kw = _eval_kwargs(design)
        kw["baseline_evidence"] = evidence
        kw["feature_row_keys"] = rows
        with pytest.raises(ValueError, match="outside"):
            evaluate(cases, **kw)

    def test_fixture_mode_without_universe_still_caps(self):
        design = fx.powered_design()
        kw = _eval_kwargs(design)
        kw["baseline_evidence"] = _bound_evidence(design["cases"])
        report = evaluate(design["cases"], **kw)
        assert report.status == "UNDERPOWERED_DESCRIPTIVE_ONLY"
        assert report.declaration["mode"] == "fixture_only"
