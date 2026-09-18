"""Real-FMX lane tests — value-level audit on a real-shaped matrix
(Lane B).

Uses ONLY the existing ``ColumnAudit`` / ``Verdict`` / ``audit_matrix``
interfaces — no new FMX schema, no edit to
``nepal/framework_v1/feature_matrix_contract.py``.  Every malformed or
contaminated feature case fails closed with an explicit check code; a
valid descriptive reanalysis matrix produces a complete, all-pass
audit report.  Forecast-only features are rejected in retrospective
(REANALYSIS) mode per the existing class registry.

All fixtures are synthetic; no real bytes are involved and no source
status changes.  ``P5_BLOCKED_NO_AUTHORIZATION`` stands.
"""
from __future__ import annotations

import pytest

from nepal.science_v0.fmx_audit import (
    ColumnAudit, OCCURRENCE_FIELD_CLASSES, Verdict, audit_matrix)

_CUTOFF = "2020-12-31T00:00:00Z"

_FEATURES = ("t2m_anom", "precip_acc", "snowmelt_flux", "slope_deg")


def _audit(name, cls="hydrology_state", unit="K anomaly",
           domain="[-5, 5]", window=("2019-01-01T00:00:00Z",
                                     "2020-06-30T00:00:00Z"),
           sentinels=(), missing="allow_nan_declared",
           lineage=("hmaglofdb-adjacent-reanalysis", "v1")):
    return ColumnAudit(
        column_name=name, declared_field_class=cls,
        source_lineage=lineage,
        availability_semantics="lead-time-safe reanalysis record",
        unit=unit, value_domain=domain, temporal_window=window,
        declared_sentinels=tuple(sentinels),
        missingness_policy=missing)


def _clean_columns():
    return {
        "t2m_anom": [0.2, -0.4, 0.9, 1.1, -1.3, 0.0, 0.4],
        "precip_acc": [12.0, 0.0, 44.2, 3.1, 9.9, 21.0, 7.4],
        "snowmelt_flux": [0.01, 0.0, 0.4, 0.2, 0.09, 0.31, 0.02],
        "slope_deg": [21.0, 33.5, 18.2, 40.0, 27.7, 12.9, 35.3]}


def _clean_audits():
    return [
        _audit("t2m_anom", "meteorological_reforecast", "K anomaly"),
        _audit("precip_acc", "meteorological_archived_operational",
               "mm", "[0, 500)"),
        _audit("snowmelt_flux", "hydrology_state", "m3/s",
               "[0, inf)"),
        _audit("slope_deg", "terrain_static", "degrees", "[0, 90]"),
    ]


def _verdict_map(verdicts):
    return {v.column_name: v for v in verdicts}

# ------------------------------------------------ 1. valid matrix audit

class TestValidDescriptiveMatrix:
    def test_complete_audit_report_all_pass(self):
        verdicts = audit_matrix(
            _clean_columns(), _clean_audits(), cutoff_iso=_CUTOFF,
            feature_digest_set=set(_FEATURES), train_row_count=7,
            preprocessing_fit_rows=7,
            preprocessing_provenance={
                c: "train_only" for c in _FEATURES})
        assert len(verdicts) == len(_FEATURES)
        by_name = _verdict_map(verdicts)
        for name in _FEATURES:
            v = by_name[name]
            assert isinstance(v, Verdict)
            assert v.verdict == "pass", (name, v.reasons)
            assert v.checks_fired == ("ALL-CLEAN",)
        assert not any(v.verdict == "reject" for v in verdicts)

    def test_forecast_class_is_registry_legal(self):
        # The class registry admits reforecast + archived-operational
        # channels; the RETROSPECTIVE-vs-FORECAST mode restriction is
        # the regime config's contract (FCST-01, tested in the
        # coordinator lane) — this audit never invents a new schema.
        assert "meteorological_reforecast" in OCCURRENCE_FIELD_CLASSES
        assert ("meteorological_archived_operational"
                in OCCURRENCE_FIELD_CLASSES)

    def test_every_column_must_have_an_audit(self):
        verdicts = audit_matrix(
            _clean_columns(), _clean_audits()[:-1],
            cutoff_iso=_CUTOFF)
        m = _verdict_map(verdicts)
        assert m["slope_deg"].verdict == "reject"
        assert m["slope_deg"].checks_fired == ("A-FIELDS",)

# ------------------------------------------- 2. name/class/value leakage

class TestLeakageFailsClosed:
    def _one(self, name, values, audit):
        verdicts = audit_matrix({name: values}, [audit],
                                cutoff_iso=_CUTOFF)
        return verdicts[0]

    def test_illegal_class_rejected(self):
        v = self._one("x1", [0.1, 0.2, 0.3],
                      _audit("x1", cls="not_a_class"))
        assert v.verdict == "reject"
        assert "CLASS-ILLEGAL" in v.checks_fired

    def test_exposure_class_rejected(self):
        v = self._one("population_exposed", [100.0, 200.0, 300.0],
                      _audit("population_exposed", cls="exposure",
                             unit="persons", domain="[0, inf)"))
        assert v.verdict == "reject"
        assert "CLASS-EXPOSURE" in v.checks_fired
        assert "NAME-EXPOSURE" in v.checks_fired

    def test_impact_name_rejected(self):
        v = self._one("fatalities", [0.0, 1.0, 2.0],
                      _audit("fatalities", cls="hydrology_state",
                             unit="count", domain="[0, inf)"))
        assert v.verdict == "reject"
        assert "NAME-EXPOSURE" in v.checks_fired

    def test_b_series_name_rejected(self):
        v = self._one("priority_rank", [1.0, 2.0, 3.0],
                      _audit("priority_rank", cls="terrain_static",
                             unit="rank", domain="[1, inf)"))
        assert v.verdict == "reject"
        assert "NAME-B" in v.checks_fired

    def test_renamed_exposure_rejected(self):
        v = self._one("population_density", [10.0, 20.0, 30.0],
                      _audit("population_density",
                             cls="terrain_static",
                             unit="persons/km2", domain="[0, inf)"))
        assert v.verdict == "reject"
        assert "RENAMED-EXPOSURE" in v.checks_fired

    def test_renamed_b_series_rejected(self):
        v = self._one("quality_score", [3.0, 1.0, 2.0],
                      _audit("quality_score", cls="terrain_static",
                             unit="score", domain="[0, inf)"))
        assert v.verdict == "reject"
        assert "RENAMED-B" in v.checks_fired

    def test_exposure_proxy_correlation_rejected(self):
        vals = [1.0, 5.5, 2.0, 4.0, 3.0]   # rank-shape-safe values
        v = self._one("f_clean", vals, _audit("f_clean"))
        v2 = audit_matrix(
            {"f_clean": vals}, [_audit("f_clean")], cutoff_iso=_CUTOFF,
            exposure_proxies={"pop_proxy": vals})[0]
        assert v.verdict == "pass"
        assert v2.verdict == "reject"
        assert "VALUE-EXPOSURE" in v2.checks_fired

    def test_b_ordering_reproduction_rejected(self):
        vals = [10.0, 20.0, 30.0, 40.0]
        v2 = audit_matrix(
            {"f_clean": vals}, [_audit("f_clean")], cutoff_iso=_CUTOFF,
            b_orderings={"b_rank": [1, 2, 3, 4]})[0]
        assert v2.verdict == "reject"
        assert "VALUE-B-ORDER" in v2.checks_fired

    def test_rank_shaped_column_rejected(self):
        v = self._one("suspicious", [1.0, 2.0, 3.0, 4.0],
                      _audit("suspicious", cls="hydrology_state",
                             unit="index", domain="[1, inf)"))
        assert v.verdict == "reject"
        assert "VALUE-RANK-SHAPE" in v.checks_fired

    def test_catalog_label_channel_rejected(self):
        v = self._one("event_label", [0.0, 1.0, 0.0],
                      _audit("event_label", cls="catalog_label",
                             unit="binary", domain="{0, 1}"))
        assert v.verdict == "reject"
        assert "LABEL-CLASS-IN-MATRIX" in v.checks_fired

    def test_label_inside_digest_set_rejected(self):
        verdicts = audit_matrix(
            {"f1": [0.1, 0.2, 0.3]}, [_audit("f1")],
            cutoff_iso=_CUTOFF,
            catalog_label_columns={"event_label"},
            feature_digest_set={"f1", "event_label"})
        fired = [c for v in verdicts for c in v.checks_fired]
        assert "LABEL-IN-PREDICTORS" in fired

    def test_label_channel_named_only_in_digest_set_rejected(self):
        # NEW-FMX-01: the digest set is the predictor surface — a
        # label channel named there rejects even when absent from
        # the audited columns.
        verdicts = audit_matrix(
            {"f1": [0.1, 0.2, 0.3]}, [_audit("f1")],
            cutoff_iso=_CUTOFF,
            catalog_label_columns={"event_label"},
            feature_digest_set={"f1", "event_label"})
        stray = [v for v in verdicts
                 if v.column_name == "event_label"]
        assert stray and stray[0].verdict == "reject"
        assert "LABEL-IN-PREDICTORS" in stray[0].checks_fired

# --------------------------------- 3. metadata, temporal, split, integrity

class TestMetadataTemporalSplitIntegrity:
    def _one(self, audit, values=(0.4, -0.2, 0.8, 0.1)):
        return audit_matrix(
            {audit.column_name: list(values)}, [audit],
            cutoff_iso=_CUTOFF)[0]

    def test_lineage_missing_rejected(self):
        a = _audit("f1")
        a = ColumnAudit(
            column_name=a.column_name,
            declared_field_class=a.declared_field_class,
            source_lineage=(),
            availability_semantics=a.availability_semantics,
            unit=a.unit, value_domain=a.value_domain,
            temporal_window=a.temporal_window,
            declared_sentinels=a.declared_sentinels,
            missingness_policy=a.missingness_policy)
        v = self._one(a)
        assert v.verdict == "reject"
        assert "LINEAGE-MISSING" in v.checks_fired

    def test_availability_missing_rejected(self):
        a = _audit("f1", )
        a = ColumnAudit(
            column_name=a.column_name,
            declared_field_class=a.declared_field_class,
            source_lineage=a.source_lineage,
            availability_semantics="",
            unit=a.unit, value_domain=a.value_domain,
            temporal_window=a.temporal_window,
            declared_sentinels=a.declared_sentinels,
            missingness_policy=a.missingness_policy)
        v = self._one(a)
        assert v.verdict == "reject"
        assert "AVAIL-MISSING" in v.checks_fired

    @pytest.mark.parametrize("kw,code", (
        ({"unit": ""}, "UNIT-MISSING"),
        ({"domain": ""}, "DOMAIN-MISSING"),
        ({"missing": ""}, "MISSINGNESS-MISSING"),))
    def test_declared_metadata_required(self, kw, code):
        a = _audit("f1", **kw)
        v = self._one(a)
        assert v.verdict == "reject"
        assert code in v.checks_fired

    def test_bad_window_rejected(self):
        a = _audit("f1", window=("2020-01-01T00:00:00Z",))
        v = self._one(a)
        assert "WINDOW-MISSING" in v.checks_fired

    def test_inverted_window_rejected(self):
        a = _audit("f1", window=("2020-06-30T00:00:00Z",
                                 "2019-01-01T00:00:00Z"))
        v = self._one(a)
        assert "WINDOW-ORDER" in v.checks_fired

    def test_post_cutoff_availability_rejected(self):
        a = _audit("f1", window=("2019-01-01T00:00:00Z",
                                 "2021-06-30T00:00:00Z"))
        v = self._one(a)
        assert v.verdict == "reject"
        assert "POST-CUTOFF" in v.checks_fired

    def test_post_issue_availability_rejected(self):
        a = _audit("f1", window=("2019-01-01T00:00:00Z",
                                 "2020-06-30T00:00:00Z"))
        v = audit_matrix(
            {a.column_name: [0.1, 0.2, 0.3]}, [a], cutoff_iso=_CUTOFF,
            forecast_issue_iso="2020-01-01T00:00:00Z")[0]
        assert v.verdict == "reject"
        assert "POST-ISSUE" in v.checks_fired

    def test_test_tuned_preprocessing_rejected(self):
        v = audit_matrix(
            {"f1": [0.1, 0.2, 0.3]}, [_audit("f1")],
            cutoff_iso=_CUTOFF,
            preprocessing_provenance={"f1": "includes_test"})[0]
        assert v.verdict == "reject"
        assert "TEST-TUNED" in v.checks_fired

    def test_prep_fit_exceeding_train_rows_rejected(self):
        v = audit_matrix(
            {"f1": [0.1, 0.2, 0.3]}, [_audit("f1")],
            cutoff_iso=_CUTOFF, train_row_count=10,
            preprocessing_fit_rows=11)[0]
        assert v.verdict == "reject"
        assert "PREP-FIT-LEAK" in v.checks_fired

    def test_undeclared_sentinel_rejected(self):
        v = self._one(_audit("f1"), values=(0.4, -999.0, 0.8, 0.1))
        assert v.verdict == "reject"
        assert "SENTINEL-UNDECLARED" in v.checks_fired

    def test_declared_sentinel_accepted(self):
        v = self._one(_audit("f1", sentinels=(-999.0,)),
                      values=(0.4, -999.0, 0.8, 0.1))
        assert v.verdict == "pass", v.reasons

    def test_nan_under_forbid_nan_rejected(self):
        a = _audit("f1", missing="forbid_nan")
        v = audit_matrix(
            {"f1": [0.1, float("nan"), 0.3]}, [a],
            cutoff_iso=_CUTOFF)[0]
        assert v.verdict == "reject"
        assert "NAN-UNDECLARED" in v.checks_fired

    def test_naive_window_bound_rejected(self):
        a = _audit("f1", window=("2019-01-01T00:00:00",
                                 "2020-06-30T00:00:00"))
        v = self._one(a)
        assert "WINDOW-MISSING" in v.checks_fired



