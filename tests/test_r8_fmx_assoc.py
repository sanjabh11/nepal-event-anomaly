"""Round-8 behavioral tests: codeable residual closures.

* NEW-FMX-01: the catalog_label/predictor separation is derived from
  the per-column audits themselves — a caller omitting
  ``catalog_label_columns`` can no longer bypass B17.
* NEW-FMX-02: every ColumnAudit must carry non-empty unit,
  value_domain, a two-ended parseable strict-UTC temporal_window, and
  a declared missingness_policy.
* NEW-ASSOC-01: the spatial-shift null counts only *supported*
  replicates — a shift landing every event group outside the
  artifact's assignment coverage is censored, never folded into the
  permutation denominator as a silent 0.0.
"""

from __future__ import annotations

import pytest

from nepal.science_v0.fmx_audit import ColumnAudit, audit_matrix
from nepal.experiment_v0 import association as _assoc
from nepal.experiment_v0.association import (
    MIN_USABLE_SPATIAL_SHIFTS, FAMILY_ALPHA)

from tests.test_experiment_v0_b1_association import (
    PLANTED_REGIME, UNIT_BASINS, make_controls, make_events,
    planted_artifact)


# ---------------------------------------------------------------------
# NEW-FMX-01: label/predictor separation is audit-derived
# ---------------------------------------------------------------------

def _audit(name, cls="meteorological_reforecast",
           window=("2020-05-01T00:00:00Z", "2020-06-01T00:00:00Z"),
           **kw):
    kw.setdefault("missingness_policy", "listwise")
    params = dict(
        column_name=name, declared_field_class=cls,
        source_lineage=("synthetic_inventory_v0", "0.0.0-synthetic",
                        "hourly->daily-mean"),
        availability_semantics="value available at window end",
        unit="K", value_domain="physically plausible",
        temporal_window=window)
    params.update(kw)
    return ColumnAudit(**params)


def test_catalog_label_rejected_without_caller_label_list():
    """A column whose own ColumnAudit declares catalog_label and that
    sits inside the feature digest set must fire LABEL-IN-PREDICTORS
    even when the caller passes catalog_label_columns=None — the
    declaration lives on the audit, not on a caller-supplied set."""
    out = audit_matrix(
        {"event_label": [0, 0, 1, 0]},
        [_audit("event_label", cls="catalog_label")],
        cutoff_iso="2020-06-05T00:00:00Z",
        catalog_label_columns=None,
        feature_digest_set={"event_label"})
    assert out[0].verdict == "reject"
    assert "LABEL-IN-PREDICTORS" in out[0].checks_fired


def test_catalog_label_class_in_predictor_matrix_rejected():
    """A catalog_label-classed column present inside the audited
    predictor matrix rejects even with no digest set at all — a label
    channel is never a predictor column."""
    out = audit_matrix(
        {"event_label": [0, 0, 1, 0]},
        [_audit("event_label", cls="catalog_label")],
        cutoff_iso="2020-06-05T00:00:00Z")
    assert out[0].verdict == "reject"
    assert "LABEL-CLASS-IN-MATRIX" in out[0].checks_fired


def test_declared_label_channel_in_digest_set_outside_columns():
    """A declared label channel named in the feature digest set
    rejects even when the channel is not among the audited columns —
    the digest set, not the column map, is the predictor surface."""
    out = audit_matrix(
        {"t2m_mean": [280.1, 281.2, 279.9, 280.4]},
        [_audit("t2m_mean")],
        cutoff_iso="2020-06-05T00:00:00Z",
        catalog_label_columns={"held_out_label"},
        feature_digest_set={"t2m_mean", "held_out_label"})
    labels = {v.column_name: v for v in out}
    assert labels["held_out_label"].verdict == "reject"
    assert "LABEL-IN-PREDICTORS" in \
        labels["held_out_label"].checks_fired


# ---------------------------------------------------------------------
# NEW-FMX-02: required per-column metadata is non-empty and well-formed
# ---------------------------------------------------------------------

class TestRequiredMetadata:
    @pytest.mark.parametrize("override,check", [
        ({"unit": ""}, "UNIT-MISSING"),
        ({"unit": "   "}, "UNIT-MISSING"),
        ({"value_domain": ""}, "DOMAIN-MISSING"),
        ({"temporal_window": ()}, "WINDOW-MISSING"),
        ({"temporal_window": ("2020-05-01T00:00:00Z",)},
         "WINDOW-MISSING"),
        ({"missingness_policy": ""}, "MISSINGNESS-MISSING"),
    ])
    def test_empty_required_metadata_rejected(self, override, check):
        a = _audit("t2m_mean", **override)
        out = audit_matrix({"t2m_mean": [280.0, 281.0]}, [a],
                           cutoff_iso="2020-06-05T00:00:00Z")
        assert out[0].verdict == "reject"
        assert check in out[0].checks_fired

    def test_temporal_window_order_rejected(self):
        a = _audit("t2m_mean",
                   window=("2020-06-10T00:00:00Z",
                           "2020-06-01T00:00:00Z"))
        out = audit_matrix({"t2m_mean": [280.0, 281.0]}, [a],
                           cutoff_iso="2020-07-01T00:00:00Z")
        assert out[0].verdict == "reject"
        assert "WINDOW-ORDER" in out[0].checks_fired

    @pytest.mark.parametrize("window", [
        ("2020-05-01T00:00:00Z", "not-a-timestamp"),
        ("not-a-timestamp", "2020-06-01T00:00:00Z"),
        # naive timestamps are not strict-UTC
        ("2020-05-01T00:00:00", "2020-06-01T00:00:00Z"),
    ])
    def test_unparseable_window_bounds_rejected(self, window):
        a = _audit("t2m_mean", window=window)
        out = audit_matrix({"t2m_mean": [280.0, 281.0]}, [a],
                           cutoff_iso="2020-07-01T00:00:00Z")
        assert out[0].verdict == "reject"
        assert "WINDOW-MISSING" in out[0].checks_fired

    def test_valid_window_still_cutoff_checked(self):
        """The POST-CUTOFF check survives the new window validation —
        a declared in-order window ending after the cutoff rejects."""
        a = _audit("sd_mean", cls="cryosphere_state",
                   window=("2020-06-01T00:00:00Z",
                           "2020-06-10T00:00:00Z"))
        out = audit_matrix({"sd_mean": [0.1, 0.2]}, [a],
                           cutoff_iso="2020-06-05T00:00:00Z")
        assert out[0].verdict == "reject"
        assert "POST-CUTOFF" in out[0].checks_fired

    def test_fully_declared_column_passes(self):
        out = audit_matrix(
            {"t2m_mean": [280.1, 281.2, 279.9, 280.4]},
            [_audit("t2m_mean")],
            cutoff_iso="2020-06-05T00:00:00Z")
        assert out[0].verdict == "pass"


# ---------------------------------------------------------------------
# NEW-ASSOC-01: spatial-shift eligibility accounting
# ---------------------------------------------------------------------

def _spatial_inputs():
    events = make_events(21)
    artifact = planted_artifact(events)
    groups = _assoc._group_events(events)
    basin_units = _assoc._basin_units(UNIT_BASINS)
    return artifact, groups, make_controls(), basin_units


def test_spatial_shift_counts_eligible_groups():
    """A far-out shift whose moved event windows all land outside the
    artifact's assignment coverage reports n_eligible_groups == 0 and
    is censored from the permutation distribution — never counted as
    a 0.0 replicate."""
    artifact, groups, controls, basin_units = _spatial_inputs()
    offsets = tuple(range(-9, 0)) + tuple(range(1, 12)) + (3650,)
    rec = _assoc._spatial_shift_null(
        artifact, groups, controls, basin_units, UNIT_BASINS,
        mode="midpoint", lookback_days=0, offsets=offsets)
    support = rec["offset_support"]
    assert support["3650"]["n_eligible_groups"] == 0
    assert support["3650"]["n_censored_groups"] == len(groups)
    # every in-coverage offset still carries eligible groups
    for off in offsets:
        if off != 3650:
            assert support[str(off)]["n_eligible_groups"] > 0
    entry = rec["per_regime"][PLANTED_REGIME]
    assert entry["n_offsets_used"] == len(offsets) - 1
    assert entry["n_offsets_censored"] == 1
    # the censored replicate stays visible in shifted_ratios —
    # accounted for, never silently folded into the denominator
    assert "3650" in entry["shifted_ratios"]
    assert len(rec["digest"]) == 64


def test_out_of_range_shifts_fail_closed():
    """When no shifted replicate carries any eligible group the null
    fails closed: p is None with INSUFFICIENT_SHIFT_SUPPORT, never a
    confident small p."""
    artifact, groups, controls, basin_units = _spatial_inputs()
    offsets = tuple(range(400, 420))  # every event lands in 2021+
    rec = _assoc._spatial_shift_null(
        artifact, groups, controls, basin_units, UNIT_BASINS,
        mode="midpoint", lookback_days=0, offsets=offsets)
    assert rec["n_offsets_usable"] == 0
    assert rec["per_regime"]
    for rid, entry in rec["per_regime"].items():
        assert entry["p"] is None
        assert entry["status"] == "INSUFFICIENT_SHIFT_SUPPORT"
        assert entry["n_offsets_used"] \
            < MIN_USABLE_SPATIAL_SHIFTS
    assert len(rec["digest"]) == 64


def test_spatial_permutation_p_never_uses_unsupported_zeroes():
    """A shift landing outside coverage must not dilute the null
    denominator: the same regime under a 20-offset all-supported
    family and under a 19-supported + 1-unsupported family must get
    different p values — the unsupported replicate contributes
    nothing, not even a zero."""
    artifact, groups, controls, basin_units = _spatial_inputs()
    full = tuple(range(-9, 0)) + tuple(range(1, 12))      # 20 usable
    mixed = tuple(range(-9, 0)) + tuple(range(1, 11)) + (3650,)
    rec_full = _assoc._spatial_shift_null(
        artifact, groups, controls, basin_units, UNIT_BASINS,
        mode="midpoint", lookback_days=0, offsets=full)
    rec_mixed = _assoc._spatial_shift_null(
        artifact, groups, controls, basin_units, UNIT_BASINS,
        mode="midpoint", lookback_days=0, offsets=mixed)
    a = rec_full["per_regime"][PLANTED_REGIME]
    b = rec_mixed["per_regime"][PLANTED_REGIME]
    assert a["n_offsets_used"] == 20
    assert b["n_offsets_used"] == 19
    assert b["p"] is not None and a["p"] is not None
    # the planted regime beats every supported replicate, so the p
    # difference is purely the denominator: 1/21 vs 1/20 — the
    # unsupported shift is excluded, not a silent zero
    assert a["p"] != b["p"]
    # the usable floor is exactly the n for which the smallest
    # achievable p = 1/(n+1) reaches the family alpha
    assert 1.0 / (MIN_USABLE_SPATIAL_SHIFTS + 1) <= FAMILY_ALPHA
    assert 1.0 / MIN_USABLE_SPATIAL_SHIFTS > FAMILY_ALPHA
