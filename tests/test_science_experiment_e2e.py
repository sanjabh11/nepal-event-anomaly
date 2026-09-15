"""Synthetic end-to-end: science_v0 -> adapters -> experiment_v0.

Exercises the full declared integration path with fabricated values
only — no real data, no network:

    source rows -> normalized events
    -> observation opportunities -> derived controls
    -> holdout plan (groups before filtering)
    -> multi-region regime run (label-blind) -> frozen artifact
    -> RegimeAssignmentArtifact adapter -> association report
    -> admitted vintage metadata -> forecast evaluation report
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from nepal.science_v0.events import (ObservationOpportunity,
                                     SourceRow, assign_holdouts,
                                     build_controls, normalize_event)
from nepal.science_v0.regimes import (RegimeRunConfig,
                                      freeze_regime_artifact,
                                      run_regimes)
from nepal.experiment_v0.adapters import (control_to_v0, event_to_label,
                                         holdout_to_v0,
                                         opportunity_to_v0,
                                         regime_to_assignment_artifact,
                                         region_basins, unit_basins)
from nepal.experiment_v0.association import run_association
from nepal.experiment_v0.baselines import (ThresholdRule,
                                           climatology_probs,
                                           null_probs, rule_probs)
from nepal.experiment_v0.evaluation import ForecastCase, evaluate
from nepal.experiment_v0.vintages import (VintageRequest,
                                         build_vintage)

_BASINS = ("koshi", "bagmati", "gandaki", "karnali", "mahakali")
_GROUP = {"koshi": "grp_east", "bagmati": "grp_east",
          "gandaki": "grp_central", "karnali": "grp_west",
          "mahakali": "grp_farwest"}
_SPLIT = {"grp_east": "train", "grp_central": "val",
          "grp_west": "test", "grp_farwest": "test"}
_EVAL_REGIONS = ("karnali", "mahakali")  # basins in distinct test groups
_SHA = "a" * 64


def _events():
    rows = [
        SourceRow(source_id="syn_inv", source_version="v0",
                  source_row_key=f"ev-{i}", mechanism="snow_avalanche",
                  interval_start=f"2020-06-{5+i:02d}T00:00:00Z",
                  interval_end=f"2020-06-{5+i:02d}T06:00:00Z",
                  declared_precision="day", basin=b)
        for i, b in enumerate(_BASINS)]
    return [normalize_event(r) for r in rows]


def _opportunity(basin, w0, w1):
    return ObservationOpportunity(
        source_id="syn_inv", basin=basin, window_start=w0,
        window_end=w1, coverage_class="OBSERVED_FULL",
        opportunity_id=f"opp-{basin}-{w0[:10]}")


def _controls(events):
    """Controls in test basins on windows with NO event overlap."""
    windows = [("2020-07-01T00:00:00Z", "2020-07-08T00:00:00Z"),
               ("2020-07-15T00:00:00Z", "2020-07-22T00:00:00Z")]
    out = []
    for basin in _EVAL_REGIONS:
        opps = [_opportunity(basin, w0, w1) for w0, w1 in windows]
        out.extend(build_controls("syn_inv", basin, windows, opps,
                                  events))
    return out, windows


def _holdout(events):
    return assign_holdouts(
        events, _GROUP, _SPLIT, _EVAL_REGIONS, embargo_seconds=86400)


def _feature_frame():
    """Two planted regimes over 5 units x 90 days."""
    rng = np.random.default_rng(7)
    rows = []
    for unit in _BASINS:
        cluster = 0 if unit in ("koshi", "bagmati", "gandaki") else 1
        for day in range(90):
            f1 = rng.normal(cluster * 4.0, 0.4)
            f2 = rng.normal(-cluster * 3.0, 0.4)
            rows.append({"unit_id": unit,
                         "date": pd.Timestamp("2020-06-01")
                                 + pd.Timedelta(days=day),
                         "f1": f1, "f2": f2,
                         "basin_group": _GROUP[unit],
                         "season": "JJA", "era": "e1"})
    return pd.DataFrame(rows)


def _regime_artifact(df):
    train_mask = df["basin_group"].isin(
        ["grp_east", "grp_central"]).to_numpy()
    cfg = RegimeRunConfig(unit_col="unit_id", date_col="date")
    art = run_regimes(df, ["f1", "f2"], train_mask, cfg)
    assert art.get("status") != "RUN_ERROR", art.get("reason")
    assert art["assignments_emitted"] and art["assignments"]
    return freeze_regime_artifact(art)


def test_synthetic_end_to_end_path():
    events = _events()
    controls, windows = _controls(events)
    assert controls, "no controls emitted"
    holdout = _holdout(events)
    artifact = _regime_artifact(_feature_frame())

    # --- adapters: science_v0 -> governed V0 records ---
    labels = [event_to_label(
        e, vertical_id="snow_avalanche",
        event_time_basis="synthetic inventory row",
        geometry_role="source_point",
        adjudication_state="TWO_REVIEW_AGREE",
        reviewer_ids=("r1", "r2")) for e in events]
    for lab in labels:
        assert lab.problems() == [], lab.problems()
    # The association harness receives ONLY held-out labels — train/
    # validation events are never evidence under the binding rules.
    held_out_labels = [l for l in labels
                       if l.basin_id in _EVAL_REGIONS]
    assert held_out_labels

    # per-window opportunities matching each control's window exactly
    # (derive_control_state_typed requires window equality)
    opp_by_id = {}
    for c in controls:
        opp = _opportunity(c.basin, c.window_start, c.window_end)
        opp_by_id[opp.opportunity_id] = opportunity_to_v0(
            opp, unit_id=c.basin, platform="SYNTHETIC",
            coverage_fraction=1.0, source_as_of="2020-08-01",
            frame_ids=("frame-a",))
    controls_v0 = [control_to_v0(c, opp_by_id[c.opportunity_id],
                                 held_out_labels) for c in controls]
    assert all(c.state == "NEGATIVE" for c in controls_v0), \
        [c.state for c in controls_v0]

    holdout_v0 = holdout_to_v0(holdout, holdout_plan_id="e2e-holdout")
    assert holdout_v0.problems() == [], holdout_v0.problems()

    regime_art = regime_to_assignment_artifact(
        artifact, artifact_id="e2e-regimes")
    assert regime_art.problems() == [], regime_art.problems()

    units = sorted({r[0] for r in artifact["assignments"]})
    report = run_association(
        regime_art, held_out_labels, controls_v0,
        unit_basins(units), holdout=holdout_v0,
        region_basins=region_basins(holdout), n_boot=50, seed=3)
    assert report is not None

    # --- vintage admission (metadata-first) ---
    req = VintageRequest(
        provider="tigge", centre="ecmwf",
        data_class="ARCHIVED_OPERATIONAL",
        model_version="syn-v0", cycle="00",
        initialization_time="2020-06-01T00:00:00Z",
        issue_time="2020-06-01T06:00:00Z",
        valid_start="2020-06-01T12:00:00Z",
        valid_end="2020-06-02T12:00:00Z",
        archive_availability="2020-06-03T06:00:00Z",
        local_retrieval_time="2020-06-03T07:00:00Z",
        license_id="syn-licence", archive_mechanism="syn-portal",
        archive_payload_sha256=_SHA,
        retrieval_record_sha256="b" * 64,
        archive_payload_path="vintages/a.bin",
        retrieval_record_path="vintages/a.json",
        declared_delay_seconds=48 * 3600.0)
    vintage = build_vintage(req, "vint-e2e")
    assert vintage.problems() == []

    # --- forecast evaluation on locked regions ---
    cases = []
    for i, basin in enumerate(_EVAL_REGIONS):
        region = _GROUP[basin]      # V0 evaluation region = group name
        for j in range(12):
            pos = j % 3 == 0
            cases.append(ForecastCase(
                case_id=f"case-{basin}-{j}", unit_id=basin,
                region=region, season="JJA",
                mechanism="snow_release",
                issue_time="2020-06-01T06:00:00Z",
                valid_start="2020-06-01T12:00:00Z",
                valid_end="2020-06-02T12:00:00Z",
                horizon="24h", lead_seconds=21600.0,
                y_prob=0.8 if pos else 0.15,
                y_state="POSITIVE" if pos else "NEGATIVE",
                vintage_digest=vintage.archive_payload_sha256))
    baseline_probs = {
        "climatology": climatology_probs(
            cases, {r: 1 / 3 for r in _EVAL_REGIONS}
            and {(r, "JJA"): 1 / 3 for r in _EVAL_REGIONS}),
        "rule": rule_probs(cases, [ThresholdRule(
            feature="unused", threshold=1e9,
            low_prob=0.2, high_prob=0.9)]),
        "null": null_probs(len(cases), base_rate=1 / 3),
        "regularized_supervised": [c.y_prob for c in cases],
    }
    admitted = {vintage.archive_payload_sha256: vintage}
    eval_report = evaluate(
        cases, holdout=holdout_v0, baseline_probs=baseline_probs,
        admitted_vintages=admitted, n_opportunities=len(cases),
        n_boot=50, seed=5)
    assert set(eval_report.metrics) >= {"model", "null"}
    assert eval_report is not None


def test_regime_adapter_rejects_summary_only_artifact():
    """A regime artifact without per-unit-day assignments cannot be
    adapted — the association contract is unit-day-bound."""
    with pytest.raises(ValueError, match="assignments"):
        regime_to_assignment_artifact(
            {"regime_artifact_digest": _SHA, "assignments": []},
            artifact_id="x")


def test_opportunity_degrades_without_frames():
    """Claimed OBSERVED_FULL without frame_ids must not emit a record
    the contract would reject — degrades to UNKNOWN."""
    opp = _opportunity("koshi", "2020-01-01T00:00:00Z",
                       "2020-01-31T00:00:00Z")
    v0 = opportunity_to_v0(opp, unit_id="koshi", platform="SYN",
                           coverage_fraction=1.0, frame_ids=())
    assert v0.state == "UNKNOWN"
