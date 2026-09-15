"""Adversarial behavioral tests for nepal.science_v0.fmx_audit."""

import pytest

from nepal.science_v0.fmx_audit import (
    ColumnAudit,
    audit_matrix,
)


def _audit(name, cls="meteorological_reforecast",
           window=("2020-05-01T00:00:00Z", "2020-06-01T00:00:00Z"),
           **kw):
    return ColumnAudit(
        column_name=name, declared_field_class=cls,
        source_lineage=("synthetic_inventory_v0", "0.0.0-synthetic",
                        "hourly->daily-mean"),
        availability_semantics="value available at window end",
        unit="K", value_domain="physically plausible",
        temporal_window=window, **kw)


class TestClassAndName:
    def test_clean_column_passes(self):
        cols = {"t2m_mean": [280.1, 281.2, 279.9, 280.4]}
        out = audit_matrix(cols, [_audit("t2m_mean")],
                           cutoff_iso="2020-06-05T00:00:00Z")
        assert out[0].verdict == "pass"

    def test_missing_audit_fails_closed(self):
        out = audit_matrix({"mystery": [1, 2, 3, 4]}, [],
                           cutoff_iso="2020-06-05T00:00:00Z")
        assert out[0].verdict == "reject"
        assert "A-FIELDS" in out[0].checks_fired

    def test_exposure_name_rejected(self):
        out = audit_matrix(
            {"population_density": [10, 20, 30, 40]},
            [_audit("population_density")],
            cutoff_iso="2020-06-05T00:00:00Z")
        assert out[0].verdict == "reject"
        assert "NAME-EXPOSURE" in out[0].checks_fired

    def test_b_series_name_rejected(self):
        out = audit_matrix(
            {"screen_rank": [1, 2, 3, 4]},
            [_audit("screen_rank")],
            cutoff_iso="2020-06-05T00:00:00Z")
        assert out[0].verdict == "reject"
        assert "NAME-B" in out[0].checks_fired


class TestRenamedLeakage:
    def test_renamed_exposure_by_value_identity(self):
        # 't2m_daily_mean' header, actually exposure values correlated
        # with a bound proxy — the adversarial rename case.
        proxy = [5.0, 8.0, 13.0, 21.0, 34.0, 55.0]
        disguised = [5.0, 8.1, 13.0, 21.0, 33.9, 55.0]
        out = audit_matrix(
            {"t2m_daily_mean": disguised},
            [_audit("t2m_daily_mean")],
            cutoff_iso="2020-06-05T00:00:00Z",
            exposure_proxies={"bound_pop": proxy})
        assert out[0].verdict == "reject"
        assert "VALUE-EXPOSURE" in out[0].checks_fired

    def test_disguised_b_rank_by_ordering(self):
        ordering = [3, 1, 4, 1, 5, 9]
        rank_col = [10, 5, 20, 6, 30, 40]  # reproduces ordering
        out = audit_matrix(
            {"snow_index_x": rank_col},
            [_audit("snow_index_x", cls="cryosphere_state")],
            cutoff_iso="2020-06-05T00:00:00Z",
            b_orderings={"b_screen": ordering})
        assert out[0].verdict == "reject"
        assert "VALUE-B-ORDER" in out[0].checks_fired

    def test_synonym_hint_flags_mismatch(self):
        out = audit_matrix(
            {"settlement_frac": [0.1, 0.2, 0.3, 0.4]},
            [_audit("settlement_frac")],
            cutoff_iso="2020-06-05T00:00:00Z")
        assert out[0].verdict == "reject"
        assert "NAME-EXPOSURE" in out[0].checks_fired

    def test_rank_shape_in_physical_class(self):
        out = audit_matrix(
            {"glacier_metric": [1, 2, 3, 4, 5]},
            [_audit("glacier_metric", cls="terrain_static")],
            cutoff_iso="2020-06-05T00:00:00Z")
        assert out[0].verdict == "reject"
        assert "VALUE-RANK-SHAPE" in out[0].checks_fired


class TestTemporal:
    def test_post_cutoff_window_rejected(self):
        out = audit_matrix(
            {"sd_mean": [0.1, 0.2]},
            [_audit("sd_mean", cls="cryosphere_state",
                    window=("2020-06-01T00:00:00Z",
                            "2020-06-10T00:00:00Z"))],
            cutoff_iso="2020-06-05T00:00:00Z")
        assert out[0].verdict == "reject"
        assert "POST-CUTOFF" in out[0].checks_fired

    def test_post_issue_rejected(self):
        out = audit_matrix(
            {"sd_mean": [0.1, 0.2]},
            [_audit("sd_mean", cls="cryosphere_state",
                    window=("2020-06-01T00:00:00Z",
                            "2020-06-10T00:00:00Z"))],
            cutoff_iso="2020-06-15T00:00:00Z",
            forecast_issue_iso="2020-06-05T00:00:00Z")
        assert out[0].verdict == "reject"
        assert "POST-ISSUE" in out[0].checks_fired


class TestLeakage:
    def test_test_tuned_column_rejected(self):
        out = audit_matrix(
            {"t2m_z": [0.1, -0.2, 0.0, 0.1]},
            [_audit("t2m_z")],
            cutoff_iso="2020-06-05T00:00:00Z",
            preprocessing_provenance={"t2m_z": "includes_test"})
        assert out[0].verdict == "reject"
        assert "TEST-TUNED" in out[0].checks_fired

    def test_prep_fit_rows_exceeding_train_rejected(self):
        out = audit_matrix(
            {"t2m_z": [0.1, -0.2, 0.0, 0.1]},
            [_audit("t2m_z")],
            cutoff_iso="2020-06-05T00:00:00Z",
            train_row_count=3, preprocessing_fit_rows=4)
        assert out[0].verdict == "reject"
        assert "PREP-FIT-LEAK" in out[0].checks_fired

    def test_catalog_label_in_digest_set_rejected(self):
        out = audit_matrix(
            {"event_label": [0, 0, 1, 0]},
            [_audit("event_label", cls="catalog_label")],
            cutoff_iso="2020-06-05T00:00:00Z",
            catalog_label_columns={"event_label"},
            feature_digest_set={"event_label"})
        assert out[0].verdict == "reject"
        assert "LABEL-IN-PREDICTORS" in out[0].checks_fired


class TestIntegrity:
    def test_undeclared_sentinel_rejected(self):
        out = audit_matrix(
            {"tp_sum": [1.0, -999.0, 2.0, 3.0]},
            [_audit("tp_sum")],
            cutoff_iso="2020-06-05T00:00:00Z")
        assert out[0].verdict == "reject"
        assert "SENTINEL-UNDECLARED" in out[0].checks_fired

    def test_declared_sentinel_passes(self):
        out = audit_matrix(
            {"tp_sum": [1.0, -999.0, 2.0, 3.0]},
            [_audit("tp_sum", declared_sentinels=(-999.0,))],
            cutoff_iso="2020-06-05T00:00:00Z")
        assert out[0].verdict == "pass"

    def test_nan_forbidden_policy(self):
        out = audit_matrix(
            {"t2m_mean": [280.0, float("nan"), 281.0]},
            [_audit("t2m_mean", missingness_policy="forbid_nan")],
            cutoff_iso="2020-06-05T00:00:00Z")
        assert out[0].verdict == "reject"
        assert "NAN-UNDECLARED" in out[0].checks_fired

    def test_missing_availability_semantics_rejected(self):
        a = _audit("t2m_mean")
        a = ColumnAudit(**{**a.__dict__, "availability_semantics": ""})
        out = audit_matrix({"t2m_mean": [280.0, 281.0]}, [a],
                           cutoff_iso="2020-06-05T00:00:00Z")
        assert out[0].verdict == "reject"
        assert "AVAIL-MISSING" in out[0].checks_fired
