"""Synthetic end-to-end: science_v0 -> serialized payloads ->
experiment_v0 adapters -> contract records.

Exercises the full declared integration path with fabricated values
only — no real data, no network:

    source rows -> normalized events
    -> observation opportunities -> derived controls
    -> holdout plan (groups before filtering)
    -> multi-region regime run (label-blind) -> frozen artifact
    -> serialized payloads -> strict adapters -> V0 records
    -> association report -> admitted vintage -> evaluation report

Every boundary crossing is a plain ``dataclasses.asdict`` / dict
payload — the same shape a serialized artifact would carry.
"""
from __future__ import annotations

from dataclasses import asdict

import numpy as np
import pandas as pd
import pytest

from nepal.science_v0.events import (ObservationOpportunity,
                                     SourceRow, assign_holdouts,
                                     build_controls, normalize_event)
from nepal.science_v0.regimes import (RegimeRunConfig,
                                      freeze_regime_artifact,
                                      run_regimes)
from nepal.experiment_v0.adapters import (
    control_from_science, event_label_from_identity,
    holdout_plan_from_assignment, opportunity_from_science,
    regime_assignment_from_artifact, vintage_from_request)
from nepal.experiment_v0.association import run_association
from nepal.experiment_v0.baselines import (ThresholdRule,
                                           climatology_probs,
                                           null_probs, rule_probs)
from nepal.experiment_v0.evaluation import ForecastCase, evaluate
from nepal.experiment_v0.vintages import VintageRequest
from nepal.research_v0._hashing import sha256_canonical

_BASINS = ("koshi", "bagmati", "gandaki", "seti", "karnali",
           "mahakali")
_GROUP = {"koshi": "grp_east", "bagmati": "grp_east",
          "gandaki": "grp_central", "seti": "grp_north",
          "karnali": "grp_west", "mahakali": "grp_farwest"}
_SPLIT = {"grp_east": "train", "grp_central": "val",
          "grp_north": "train",
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


def _opportunity(unit_id, basin, w0, w1):
    return ObservationOpportunity(
        opportunity_id=f"opp-{unit_id}-{w0[:10]}", unit_id=unit_id,
        source_id="syn_inv", basin=basin, window_start=w0,
        window_end=w1, state="OBSERVED_FULL", platform="SYNTHETIC",
        coverage_fraction=1.0, coverage_quality="complete",
        detection_threshold="syn", source_as_of="2020-08-01",
        frame_ids=(f"frame-{unit_id}-{w0[:10]}",))


def _controls(events):
    """Controls in test basins on windows with NO event overlap."""
    windows = [("2020-07-01T00:00:00Z", "2020-07-08T00:00:00Z"),
               ("2020-07-15T00:00:00Z", "2020-07-22T00:00:00Z")]
    controls, opps = [], {}
    for basin in _EVAL_REGIONS:
        basin_opps = [_opportunity(basin, basin, w0, w1)
                      for w0, w1 in windows]
        for o in basin_opps:
            opps[o.opportunity_id] = o
        controls.extend(build_controls(basin, "syn_inv", basin,
                                       windows, basin_opps, events))
    return controls, opps


def _holdout(events):
    return assign_holdouts(
        events, _GROUP, _SPLIT, _EVAL_REGIONS, embargo_seconds=86400)


def _feature_frame():
    """Two planted regimes over 5 units x 90 days."""
    rng = np.random.default_rng(7)
    rows = []
    for unit in _BASINS:
        cluster = 0 if unit in ("koshi", "bagmati", "gandaki",
                            "seti") else 1
        for day in range(90):
            f1 = rng.normal(cluster * 4.0, 0.4)
            f2 = rng.normal(-cluster * 3.0, 0.4)
            rows.append({"unit_id": unit,
                         "date": (pd.Timestamp("2020-06-01")
                                  + pd.Timedelta(days=day)
                                  ).strftime("%Y-%m-%d"),
                         "f1": f1, "f2": f2,
                         "basin_group": _GROUP[unit],
                         "season": "JJA", "era": "e1"})
    return pd.DataFrame(rows)


def _regime_artifact(df):
    train_mask = df["basin_group"].isin(
        ["grp_east", "grp_central", "grp_north"]).to_numpy()
    cfg = RegimeRunConfig(
        train_groups=("grp_east", "grp_central", "grp_north"),
        heldout_groups=("grp_west", "grp_farwest"))
    art = run_regimes(df, ["f1", "f2"], train_mask, cfg)
    assert art.get("status") != "RUN_ERROR", art.get("reason")
    assert art["assignments"]
    return freeze_regime_artifact(art)


def test_synthetic_end_to_end_path():
    events = _events()
    controls, opps = _controls(events)
    assert controls, "no controls emitted"
    holdout = _holdout(events)
    artifact = _regime_artifact(_feature_frame())

    # --- serialized payloads -> strict adapters -> V0 records ---
    labels = [event_label_from_identity(
        asdict(e), vertical_id="snow_avalanche",
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

    opps_v0 = {oid: opportunity_from_science(asdict(o))
               for oid, o in opps.items()}
    controls_v0 = [control_from_science(asdict(c)) for c in controls]
    assert all(c.state == "NEGATIVE" for c in controls_v0), \
        [c.state for c in controls_v0]
    # Every control's linked opportunity exists as an admitted record.
    for c in controls_v0:
        assert c.opportunity_id in opps_v0

    holdout_v0 = holdout_plan_from_assignment(
        asdict(holdout), holdout_plan_id="e2e-holdout",
        split_of_group=_SPLIT)
    assert holdout_v0.problems() == [], holdout_v0.problems()

    regime_art = regime_assignment_from_artifact(
        artifact, artifact_id="e2e-regimes")
    assert regime_art.problems() == [], regime_art.problems()

    units = sorted({r[0] for r in artifact["assignments"]})
    unit_basins = {u: u for u in units}          # unit axis == basin
    region_basins = {g: {b for b, gg in _GROUP.items() if gg == g}
                     for g in holdout_v0.evaluation_region_names}
    report = run_association(
        regime_art, held_out_labels, controls_v0,
        unit_basins, holdout=holdout_v0,
        region_basins=region_basins, opportunities=opps_v0,
        n_boot=50, seed=3)
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
    vintage = vintage_from_request(req, vintage_id="vint-e2e")
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
                vintage_digest=sha256_canonical(vintage.to_dict()),
                opportunity_id=f"syn-opp-{basin}-{j}",
                outcome_source_id="synthetic-outcome-src",
                cutoff_time="2020-06-01T06:00:00Z"))
    baseline_probs = {
        "climatology": climatology_probs(
            cases, {(r, "JJA"): 1 / 3 for r in _EVAL_REGIONS}),
        "rule": rule_probs(cases, [ThresholdRule(
            feature="unused", threshold=1e9,
            low_prob=0.2, high_prob=0.9)]),
        "null": null_probs(len(cases), base_rate=1 / 3),
        "regularized_supervised": [c.y_prob for c in cases],
    }
    admitted = {sha256_canonical(vintage.to_dict()): vintage}
    eval_report = evaluate(
        cases, holdout=holdout_v0, baseline_probs=baseline_probs,
        admitted_vintages=admitted, n_opportunities=len(cases),
        n_boot=50, seed=5)
    assert set(eval_report.metrics) >= {"model", "null"}
    assert eval_report is not None


def test_regime_adapter_rejects_summary_only_artifact():
    """A regime artifact without per-unit-day assignments cannot be
    adapted — the association contract is unit-day-bound."""
    with pytest.raises(ValueError, match="assignment"):
        regime_assignment_from_artifact(
            {"regime_artifact_digest": _SHA, "assignments": []},
            artifact_id="x")


def test_opportunity_without_frames_rejects():
    """Claimed OBSERVED_FULL without frame_ids must not adapt — the
    contract validator rejects, it is never silently degraded."""
    opp = _opportunity("koshi", "koshi", "2020-01-01T00:00:00Z",
                       "2020-01-31T00:00:00Z")
    p = asdict(opp)
    p["frame_ids"] = ()
    with pytest.raises(ValueError, match="problems"):
        opportunity_from_science(p)
