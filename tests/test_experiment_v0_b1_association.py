"""Behavioral tests for the held-out event–regime association harness
(SWE-B1, ASSOCIATION_PROTOCOL_V0).

All fixtures are synthetic and deterministic — generated labels,
control windows, holdout plans, and regime-assignment rows with
planted (or null) correspondence.  Nothing here touches real data,
the network, or the wall clock.
"""
from __future__ import annotations

import dataclasses
import hashlib
from datetime import date as _date
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from nepal.experiment_v0 import association as _assoc
from nepal.experiment_v0.association import (
    ALLOWED_LOOKBACK_DAYS, MIN_EVENT_GROUPS, MIN_SPATIAL_SHIFTS,
    PLACEMENT_MODES, SPATIAL_SHIFT_OFFSETS, AssociationReport,
    RegimeAssignmentArtifact, association_report_text,
    event_group_bootstrap, run_association)
from nepal.research_v0._hashing import canonical_json, sha256_canonical
from nepal.research_v0.gates import (REQUIRED_REGIME_GATE_NAMES,
                                     scan_claims_text)
from nepal.research_v0.records import (NEUTRAL_RESEARCH_STATUSES,
                                     ControlWindowV0, EventLabelV0,
                                     HoldoutPlanV0,
                                     ObservationOpportunityV0)

# ---------------------------------------------------------------------
# Synthetic fixture builders (deterministic; never real data)
# ---------------------------------------------------------------------

BASINS = ("koshi", "gandaki", "karnali")
UNIT_BASINS = {
    "unit-koshi-0": "koshi", "unit-koshi-1": "koshi",
    "unit-gandaki-0": "gandaki", "unit-gandaki-1": "gandaki",
    "unit-karnali-0": "karnali", "unit-karnali-1": "karnali",
}
REGION_BASINS = {
    "karnali_eval": ("karnali",),
    "gandaki_eval": ("gandaki",),
    "koshi_eval": ("koshi",),
}
_BASIN_REGION = {"koshi": "koshi_eval", "gandaki": "gandaki_eval",
                 "karnali": "karnali_eval"}
PLANTED_REGIME = "R_PLANT"
_BASE_DATE = _date(2020, 5, 25)
_ASSIGN_END = _date(2020, 12, 15)
_PLANT_BACKGROUND_MOD = 37  # sparse background share of the planted regime


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _dates_between(a: _date, b: _date) -> list[_date]:
    out = []
    d = a
    while d <= b:
        out.append(d)
        d += timedelta(days=1)
    return out


def _h(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_event(event_id: str, basin: str, start: _date,
               n_days: int = 1, cascade_group_id: str = "",
               adjudication_state: str = "TWO_REVIEW_AGREE"
               ) -> EventLabelV0:
    """A contract-valid synthetic EventLabelV0 (day or <=7d interval
    precision, uncertainty covering the whole bracket)."""
    s = datetime(start.year, start.month, start.day,
                 tzinfo=timezone.utc)
    e = s + timedelta(days=n_days)
    unc = (e - s).total_seconds()
    precision = "day" if n_days <= 1 else "interval"
    label = EventLabelV0(
        event_id=event_id,
        vertical_id="snow_avalanche",
        source_id="synthetic_inventory_v0",
        source_version="0.0.0-synthetic",
        event_time_start=_iso(s),
        event_time_end=_iso(e),
        uncertainty_seconds=unc,
        event_time_precision=precision,
        event_time_basis="synthetic-interval-bound",
        geometry_role="slope_generalized",
        basin_id=basin,
        cascade_group_id=cascade_group_id,
        adjudication_state=adjudication_state,
        adjudication_notes="synthetic",
        reviewer_ids=("rev-syn-a", "rev-syn-b"))
    assert not label.problems(), label.problems()
    return label


def make_events(count: int = 21) -> list[EventLabelV0]:
    """``count`` adjudicated events cycling three basins, spaced 7
    days; interval width alternates 1–3 days so all three placement
    modes see multi-day brackets."""
    events = []
    for i in range(count):
        events.append(make_event(
            f"ev-{i:03d}", BASINS[i % 3],
            _BASE_DATE + timedelta(days=7 + i * 7),
            n_days=1 + (i % 3)))
    return events


def make_control(control_id: str, unit: str, start: _date,
                 n_days: int = 5) -> ControlWindowV0:
    s = datetime(start.year, start.month, start.day,
                 tzinfo=timezone.utc)
    ctl = ControlWindowV0(
        control_id=control_id,
        unit_id=unit,
        window_start=_iso(s),
        window_end=_iso(s + timedelta(days=n_days)),
        opportunity_id=f"opp-{control_id}",
        opportunity_state="OBSERVED_FULL",
        state="NEGATIVE",
        matched_covariates=("basin", "season"))
    assert not ctl.problems(), ctl.problems()
    return ctl


def make_controls() -> list[ControlWindowV0]:
    """NEGATIVE controls: five-day windows per unit that overlap no
    admitted event in the unit's basin — the same derivation the
    producer applies when emitting NEGATIVE state."""
    intervals: dict[str, list] = {}
    for ev in make_events(21):
        intervals.setdefault(ev.basin_id, []).append((
            _date.fromisoformat(ev.event_time_start[:10]),
            _date.fromisoformat(ev.event_time_end[:10])))
    controls = []
    i = 0
    for unit in sorted(UNIT_BASINS):
        taken = intervals.get(UNIT_BASINS[unit], [])
        placed, k = 0, 0
        while placed < 10:
            start = _BASE_DATE + timedelta(days=10 + k * 7)
            k += 1
            if any(start < ee and start + timedelta(days=5) > es
                   for es, ee in taken):
                continue
            controls.append(make_control(f"ctl-{i:03d}", unit, start))
            placed += 1
            i += 1
    return controls


def make_opportunity(control: ControlWindowV0,
                     **overrides) -> ObservationOpportunityV0:
    """A contract-valid ObservationOpportunityV0 matching one
    control's declared lineage — the registry entry's unit, window,
    and state exactly equal what the control asserts."""
    state = overrides.pop("state", control.opportunity_state)
    observed = state in ("OBSERVED_FULL", "OBSERVED_PARTIAL")
    coverage = {"OBSERVED_FULL": 1.0, "OBSERVED_PARTIAL": 0.5,
                "UNOBSERVED": 0.0}.get(state)
    opp = ObservationOpportunityV0(
        opportunity_id=control.opportunity_id,
        unit_id=control.unit_id,
        platform="syn-platform-0",
        window_start=control.window_start,
        window_end=control.window_end,
        coverage_fraction=coverage,
        coverage_quality="synthetic",
        detection_threshold="synthetic",
        state=state,
        source_id="synthetic_obs_v0" if observed else "",
        source_as_of="2020-12-31" if observed else "",
        frame_ids=((f"frame-{control.control_id}",) if observed
                   else ()),
        **overrides)
    assert not opp.problems(), opp.problems()
    return opp


def make_opportunities(controls: list[ControlWindowV0]
                       ) -> dict[str, ObservationOpportunityV0]:
    """The opportunity registry every control's lineage verifies
    against — one entry per declared ``opportunity_id``."""
    return {o.opportunity_id: o
            for o in (make_opportunity(c) for c in controls)}


def run_assoc(artifact, events, controls, unit_basins=UNIT_BASINS,
              **kw):
    """``run_association`` with the matching synthetic opportunity
    registry auto-derived from the passed controls — tests that need
    a doctored registry pass ``opportunities=`` explicitly.  Fixture
    artifacts built via ``_rows_to_artifact`` ride the verified
    producer-payload path automatically (PROV-04); pass
    ``producer_payload=None`` explicitly to exercise the unverified
    local-artifact lane."""
    kw.setdefault("producer_payload",
                  _ARTIFACT_PAYLOADS.get(
                      _artifact_payload_key(artifact)))
    kw.setdefault("opportunities", make_opportunities(controls))
    return run_association(artifact, events, controls, unit_basins,
                           **kw)


def make_holdout(events: list[EventLabelV0],
                 extra_assignments: dict[str, str] | None = None,
                 **kw) -> HoldoutPlanV0:
    """A contract-valid HoldoutPlanV0: every passed event maps to its
    basin's locked test region; ghost entries keep train/validation
    groups covered without ever being passed to the harness."""
    assignments = {e.event_id: _BASIN_REGION[e.basin_id]
                   for e in events}
    # The holdout's event universe is complete: train and validation
    # groups also carry assigned events — those ids are simply never
    # presented to the harness.
    assignments.setdefault("ghost-train-0", "north_train")
    assignments.setdefault("ghost-val-0", "central_val")
    if extra_assignments:
        assignments.update(extra_assignments)
    plan = HoldoutPlanV0(
        holdout_plan_id="holdout-syn-b1",
        assignment_rule="basin",
        train_groups=kw.get("train_groups", ("north_train",)),
        validation_groups=kw.get("validation_groups",
                                 ("central_val",)),
        test_groups=kw.get("test_groups",
                           ("karnali_eval", "gandaki_eval",
                            "koshi_eval")),
        event_assignments=assignments,
        evaluation_region_names=kw.get(
            "evaluation_region_names",
            ("karnali_eval", "gandaki_eval", "koshi_eval")),
        assigned_before_filtering=kw.get("assigned_before_filtering",
                                         True),
        test_locked=kw.get("test_locked", True),
        embargo_seconds=kw.get("embargo_seconds", 2592000.0))
    return plan


# PROV-04 (R6): fixture-built artifacts keep the verified
# producer-payload path — every ``_rows_to_artifact`` artifact is
# stamped from a minimal internally-consistent frozen producer
# payload whose ``freeze_digest`` binds the artifact; the payload is
# registered here so ``run_assoc`` can pass it through.
_ARTIFACT_PAYLOADS: dict = {}


def _artifact_payload_key(artifact) -> tuple:
    """The digest/provenance tuple a producer payload binds —
    row-order-canonical, so a ``dataclasses.replace`` permutation of
    the same partition finds the same registered payload."""
    return (artifact.regime_digest,
            artifact.producer_payload_digest,
            artifact.assignment_digest,
            artifact.fitted_on, artifact.label_blinding,
            tuple(artifact.seeds), artifact.mode)


def _producer_payload_for(assignments,
                          seeds=(11, 23, 42)) -> dict:
    """A frozen producer-shaped payload binding ``assignments``
    — carrying the FULL canonical producer schema (R8-C01: the shared
    ``validate_producer_payload`` floor now runs at the association
    binding boundary too, so a partial payload can no longer ride the
    verified path) plus the digest chain ``freeze_regime_artifact`` /
    ``regime_assignment_from_artifact`` recompute:
    ``assignment_digest`` over the raw sidecar,
    ``regime_artifact_digest`` over the payload minus
    ``{regime_artifact_digest, freeze_digest, frozen}``, and
    ``freeze_digest`` over the payload minus ``{freeze_digest,
    frozen}``."""
    rows = [list(r) for r in sorted(assignments)]
    feature_cols = ["synth_f1", "synth_f2"]
    n_rows = len(rows)
    groups_fit = sorted(set(UNIT_BASINS.values()))
    groups_held = sorted(REGION_BASINS)
    stability = {"seed_ari_min": 0.9, "modal_k_frequency": 1.0,
                 "required_gates": {
                     g: True for g in
                     sorted(REQUIRED_REGIME_GATE_NAMES)}}
    preprocessing = {"imputer_strategy": "median",
                     "imputer_statistics": [0.0, 0.0],
                     "scaler_mean": [0.0, 0.0],
                     "scaler_var": [1.0, 1.0],
                     "feature_order": list(feature_cols),
                     "row_keys_digest": _h("synth-b1-rowkeys"),
                     "train_mask_membership_digest":
                         _h("synth-b1-maskmembers")}
    config = {"seeds": list(seeds), "k_candidates": [1, 2, 3],
              "null_alpha": 0.05, "cadence": "1D",
              "gap_policy": "calendar", "bootstrap_block_len": 7,
              "missingness_policy": "listwise",
              "effort_split": "median",
              "mode": "RETROSPECTIVE_REGIME"}
    fit_partition = {
        "record_type": "fit_partition/v0",
        "train_groups": groups_fit,
        "heldout_groups": groups_held,
        "n_train_rows": n_rows, "n_rows": n_rows,
        "train_row_keys_digest": _h("synth-b1-trainkeys"),
        "cutoff_iso": "2020-12-15",
        "feature_matrix_digest": _h("synth-b1-fmx"),
        "feature_cols": list(feature_cols)}
    run_manifest = {
        "run_id": "synth-b1-run-001", "worker_id": "synthetic-fixture",
        "created_at": "2020-12-15T00:00:00Z",
        "environment_digest": _h("synth-b1-env"), "seed": 11,
        "input_digests": [_h("synth-b1-input")],
        "output_digests": [_h("synth-b1-assignments")],
        "checkpoint_policy": "atomic_publish_or_quarantine",
        "status": "COMPLETED"}
    payload = {
        "record_type": "FrozenRegimeArtifactV0",
        "assignments": rows,
        "frozen": True,
        "mode": "RETROSPECTIVE_REGIME",
        "fitted_on": "TRAIN_ONLY",
        "label_blinding": True,
        "data_class": "REANALYSIS",
        "status": "DESCRIPTIVE_REGIME_ONLY",
        "terminal": True,
        "associable": True,
        "seeds": list(seeds),
        "seeds_declared": list(seeds),
        "seed_coverage": {str(s): "converged" for s in seeds},
        "k": 3,
        "per_seed_best_k": {str(s): 3 for s in seeds},
        "modal_k_frequency": 1.0,
        "occupancy": [0.5, 0.3, 0.2],
        "feature_cols": list(feature_cols),
        "feature_matrix_digest": _h("synth-b1-fmx"),
        "input_bytes_digest": _h("synth-b1-inputbytes"),
        "input_schema": {"feature_cols": list(feature_cols),
                         "n_rows": n_rows,
                         "dtypes": {c: "float64"
                                    for c in feature_cols},
                         "shape": [n_rows, len(feature_cols)]},
        "model": {"weights": [0.5, 0.3, 0.2],
                  "means": [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]],
                  "covariances": [[[0.25, 0.0], [0.0, 0.25]],
                                  [[0.25, 0.0], [0.0, 0.25]],
                                  [[0.25, 0.0], [0.0, 0.25]]]},
        "config": config,
        "config_digest": sha256_canonical(config),
        "fit_groups": groups_fit,
        "heldout_groups_declared": groups_held,
        "fit_partition": fit_partition,
        "unit_basin_map": sorted(UNIT_BASINS.items()),
        "run_manifest": run_manifest,
        "n_train_rows": n_rows,
        "n_rows": n_rows,
        "train_mask_digest": _h("synth-b1-mask"),
        "stability": stability,
        "nulls": {"shuffled_js": 0.31, "season_matched_js": 0.008},
        "preprocessing": preprocessing,
        "preprocessing_digest": sha256_canonical(preprocessing),
        "k_selection_digest": _h("synth-b1-ksel"),
        "stability_report_digest": sha256_canonical(stability),
        "null_model_digest": _h("synth-b1-nullmodel"),
        "source_manifest": {"fixture": True},
        "missingness_applied": {"policy": "listwise",
                                "train_rows_total": n_rows,
                                "train_rows_fitted": n_rows,
                                "train_rows_dropped": 0},
        "environment_digest": _h("synth-b1-env"),
        "disclaimer": "synthetic fixture — interface evidence only",
    }
    payload["fit_partition_digest"] = sha256_canonical(fit_partition)
    payload["run_manifest_digest"] = sha256_canonical(run_manifest)
    payload["assignment_digest"] = sha256_canonical(rows)
    payload["regime_artifact_digest"] = sha256_canonical(
        {k: v for k, v in payload.items() if k != "frozen"})
    payload["freeze_digest"] = sha256_canonical(
        {k: v for k, v in payload.items() if k != "frozen"})
    return payload


def _rows_to_artifact(rows: dict[tuple[str, str], str],
                      artifact_id: str = "regime-syn-b1",
                      **kw) -> RegimeAssignmentArtifact:
    assignments = tuple(sorted(
        (u, d, r) for (u, d), r in rows.items()))
    seeds = kw.pop("seeds", (11, 23, 42))
    payload = _producer_payload_for(assignments, seeds=seeds)
    kw.setdefault("regime_digest", payload["freeze_digest"])
    kw.setdefault("producer_payload_digest",
                  payload["freeze_digest"])
    artifact = RegimeAssignmentArtifact(
        artifact_id=artifact_id,
        assignments=assignments,
        seeds=tuple(seeds), **kw)
    if artifact.regime_digest == payload["freeze_digest"] and \
            artifact.producer_payload_digest == \
            payload["freeze_digest"]:
        _ARTIFACT_PAYLOADS[_artifact_payload_key(artifact)] = \
            payload
    return artifact


def planted_artifact(events: list[EventLabelV0]
                     ) -> RegimeAssignmentArtifact:
    """A frozen assignment table where ``R_PLANT`` is planted on every
    date intersecting an event interval for that event's basin units,
    plus a sparse (~1/37) background share everywhere else.  Baseline
    regimes R0/R1/R2 are hash-driven per (unit, date) so neighbouring
    dates share no systematic structure with the event grid."""
    rows: dict[tuple[str, str], str] = {}
    all_dates = _dates_between(_BASE_DATE, _ASSIGN_END)
    for unit in sorted(UNIT_BASINS):
        for d in all_dates:
            doy = d.timetuple().tm_yday
            if doy % _PLANT_BACKGROUND_MOD == 0:
                rows[(unit, d.isoformat())] = PLANTED_REGIME
            elif doy % 53 == 0:
                # A sparse "rare" regime exercises the novelty slice.
                rows[(unit, d.isoformat())] = "R_RARE"
            else:
                rows[(unit, d.isoformat())] = "R%d" % (
                    int(_h(f"base|{unit}|{d.isoformat()}")[:8], 16)
                    % 3)
    basin_units = {}
    for u, b in UNIT_BASINS.items():
        basin_units.setdefault(b, []).append(u)
    for ev in events:
        s = datetime.strptime(ev.event_time_start,
                              "%Y-%m-%dT%H:%M:%SZ").date()
        e = datetime.strptime(ev.event_time_end,
                              "%Y-%m-%dT%H:%M:%SZ").date()
        for d in _dates_between(s, e):
            for u in basin_units[ev.basin_id]:
                rows[(u, d.isoformat())] = PLANTED_REGIME
    return _rows_to_artifact(rows)


def null_artifact() -> RegimeAssignmentArtifact:
    """A frozen assignment table driven by a hash of (unit, date) —
    statistically independent of any event placement."""
    rows: dict[tuple[str, str], str] = {}
    for unit in sorted(UNIT_BASINS):
        for d in _dates_between(_BASE_DATE, _ASSIGN_END):
            rows[(unit, d.isoformat())] = "R%d" % (
                int(_h(f"null|{unit}|{d.isoformat()}")[:8], 16) % 3)
    return _rows_to_artifact(rows)


def single_regime_artifact() -> RegimeAssignmentArtifact:
    """K=1-trivial degenerate artifact: one regime everywhere."""
    rows = {(u, d.isoformat()): "R0"
            for u in sorted(UNIT_BASINS)
            for d in _dates_between(_BASE_DATE, _ASSIGN_END)}
    return _rows_to_artifact(rows)


@pytest.fixture()
def planted():
    events = make_events(21)
    holdout = make_holdout(events)
    return (planted_artifact(events), events, make_controls(),
            holdout)


# ---------------------------------------------------------------------
# Artifact record behavior
# ---------------------------------------------------------------------

def test_artifact_roundtrip_and_lookup():
    artifact = planted_artifact(make_events(6))
    clone = RegimeAssignmentArtifact.from_dict(artifact.to_dict())
    assert clone == artifact
    assert not artifact.problems()
    unit = "unit-koshi-0"
    day = "2020-06-01"
    assert artifact.regime_for(unit, day) is not None
    assert artifact.regime_for(unit, "1999-01-01") is None


def test_artifact_problems_reject_bad_inputs():
    good = planted_artifact(make_events(3))
    bad_digest = dataclasses.replace(good, regime_digest="zz")
    assert bad_digest.problems()
    bad_blind = dataclasses.replace(good, label_blinding=False)
    assert bad_blind.problems()
    bad_seeds = dataclasses.replace(good, seeds=(1, 1, 2))
    assert bad_seeds.problems()
    dup = dataclasses.replace(
        good, assignments=good.assignments + good.assignments[:1])
    assert dup.problems()


def test_from_dict_strict_reconstruction():
    artifact = planted_artifact(make_events(3))
    payload = artifact.to_dict()
    # Unknown fields reject.
    with pytest.raises(ValueError):
        RegimeAssignmentArtifact.from_dict({**payload, "surprise": 1})
    # Missing required fields reject.
    with pytest.raises(ValueError):
        RegimeAssignmentArtifact.from_dict(
            {k: v for k, v in payload.items() if k != "assignments"})
    # A string "false" must not become a boolean.
    with pytest.raises(ValueError):
        RegimeAssignmentArtifact.from_dict(
            {**payload, "label_blinding": "false"})
    # Wrong primitive types reject.
    with pytest.raises(ValueError):
        RegimeAssignmentArtifact.from_dict(
            {**payload, "artifact_id": 7})
    with pytest.raises(ValueError):
        RegimeAssignmentArtifact.from_dict(
            {**payload, "seeds": (1, "x", 3)})
    with pytest.raises(ValueError):
        RegimeAssignmentArtifact.from_dict(
            {**payload, "assignments": [("u", "2020-13-40", "R0")]})
    with pytest.raises(ValueError):
        RegimeAssignmentArtifact.from_dict(
            {**payload, "assignments": [("u", "2020-01-01")]})
    with pytest.raises(ValueError):
        RegimeAssignmentArtifact.from_dict(
            {**payload, "record_type": "EventLabelV0"})


# ---------------------------------------------------------------------
# 1. Planted enrichment across >=3 basins
# ---------------------------------------------------------------------

def test_planted_enrichment_detected(planted):
    artifact, events, controls, holdout = planted
    report = run_assoc(artifact, events, controls, UNIT_BASINS,
                             holdout=holdout, region_basins=REGION_BASINS)
    assert isinstance(report, AssociationReport)
    assert report.n_event_groups >= MIN_EVENT_GROUPS
    cell = report.enrichment[PLANTED_REGIME]
    assert cell["ratio"] is not None and cell["ratio"] > 1.0
    assert cell["ci_low"] is not None and cell["ci_low"] > 1.0
    assert cell["n_events"] > 0 and cell["n_controls"] > 0
    # All three negative controls flat and all placements agree on
    # direction -> the top supported status is reachable.
    assert report.status == "REGIME_ASSOCIATION_SUPPORTED"


# ---------------------------------------------------------------------
# 2. Frozen-regime blinding / label-mutation immunity
# ---------------------------------------------------------------------

def test_frozen_artifact_immune_to_label_permutation(planted):
    artifact, events, controls, holdout = planted
    digest_before = sha256_canonical(artifact.to_dict())
    # Permuting and relabeling the event set cannot touch the artifact.
    permuted = [dataclasses.replace(e, event_id=f"x-{e.event_id}")
                for e in reversed(events)]
    assert permuted != events
    digest_after = sha256_canonical(artifact.to_dict())
    assert digest_before == digest_after
    # The record exposes no fitting or mutation surface at all.
    for name in ("fit", "update", "refit", "fit_partial", "set_labels"):
        assert not hasattr(artifact, name)
    with pytest.raises(dataclasses.FrozenInstanceError):
        artifact.regime_digest = "0" * 64  # type: ignore[misc]


def test_label_mutation_cannot_move_enrichment(planted):
    artifact, events, controls, holdout = planted
    base = run_assoc(artifact, events, controls, UNIT_BASINS,
                           holdout=holdout,
                           region_basins=REGION_BASINS)
    # Reordering the label set changes nothing — grouping and strata
    # are order-canonical.
    reordered = list(reversed(events))
    reran = run_assoc(artifact, reordered, controls, UNIT_BASINS,
                            holdout=holdout,
                            region_basins=REGION_BASINS)
    assert sha256_canonical(reran.to_dict()) == \
        sha256_canonical(base.to_dict())
    # Mutating label content cannot alter the artifact or the
    # enrichment computed against it.
    artifact_digest = sha256_canonical(artifact.to_dict())
    mutated = [dataclasses.replace(
        e, adjudication_notes="attempted influence")
        for e in events]
    reran2 = run_assoc(artifact, mutated, controls, UNIT_BASINS,
                             holdout=holdout,
                             region_basins=REGION_BASINS)
    assert sha256_canonical(artifact.to_dict()) == artifact_digest
    assert reran2.enrichment == base.enrichment
    assert mutated != events


# ---------------------------------------------------------------------
# 3. Cascade atomicity
# ---------------------------------------------------------------------

def test_cascade_group_counts_once(planted):
    _artifact, _ev, controls, _holdout = planted
    events = make_events(24)
    # Three members share one cascade -> one atomic group.
    events[0] = dataclasses.replace(events[0], cascade_group_id="casc-0")
    events[1] = dataclasses.replace(events[1], cascade_group_id="casc-0")
    events[2] = dataclasses.replace(events[2], cascade_group_id="casc-0")
    artifact = planted_artifact(events)
    holdout = make_holdout(events)
    report = run_assoc(artifact, events, controls, UNIT_BASINS,
                             holdout=holdout, region_basins=REGION_BASINS)
    assert report.n_event_windows == 24
    assert report.n_event_groups == 22  # 24 events - 2 merged members


# ---------------------------------------------------------------------
# 4. Interval-uncertainty sensitivity runs all three placements
# ---------------------------------------------------------------------

def test_interval_sensitivity_three_placements(planted):
    artifact, events, controls, holdout = planted
    report = run_assoc(artifact, events, controls, UNIT_BASINS,
                             holdout=holdout, region_basins=REGION_BASINS)
    sens = report.interval_sensitivity
    assert set(sens) == set(PLACEMENT_MODES)
    for mode in PLACEMENT_MODES:
        assert PLANTED_REGIME in sens[mode]
        assert sens[mode][PLANTED_REGIME] is not None
        assert sens[mode][PLANTED_REGIME] > 1.0


# ---------------------------------------------------------------------
# 5. Negative controls present and flat under a null fixture
# ---------------------------------------------------------------------

def test_negative_controls_flat_under_null():
    events = make_events(21)
    holdout = make_holdout(events)
    report = run_assoc(null_artifact(), events, make_controls(),
                             UNIT_BASINS, holdout=holdout,
                             region_basins=REGION_BASINS)
    neg = report.negative_controls
    assert {"placebo", "impossible_regime", "time_reversed",
            "label_shuffle"} <= set(neg)
    # the flat controls must stay flat under a null artifact; the
    # label-shuffle null IS expected to stay flat here because the
    # null artifact's partition is itself arbitrary
    for name in ("placebo", "impossible_regime", "time_reversed"):
        entry = neg[name]
        assert entry["flat"], f"{name} not flat: {entry}"
        for cell in entry["per_regime"].values():
            if cell.get("ci_low") is not None:
                assert cell["ci_low"] <= 1.0 <= cell["ci_high"], (
                    name, cell)
    assert report.status == "DESCRIPTIVE_REGIME_ONLY"


# ---------------------------------------------------------------------
# 6. Underpowered / degenerate paths
# ---------------------------------------------------------------------

def test_underpowered_when_few_event_groups():
    events = make_events(9)  # 9 atomic groups < MIN_EVENT_GROUPS
    holdout = make_holdout(events)
    report = run_assoc(planted_artifact(events), events,
                             make_controls(), UNIT_BASINS,
                             holdout=holdout,
                             region_basins=REGION_BASINS)
    assert report.n_event_groups < MIN_EVENT_GROUPS
    assert report.status == "UNDERPOWERED_DESCRIPTIVE_ONLY"


def test_degenerate_artifact_not_supported():
    events = make_events(21)
    holdout = make_holdout(events)
    report = run_assoc(single_regime_artifact(), events,
                             make_controls(), UNIT_BASINS,
                             holdout=holdout,
                             region_basins=REGION_BASINS)
    assert report.status == "UNSUPERVISED_PATH_NOT_SUPPORTED"
    assert report.enrichment == {}


# ---------------------------------------------------------------------
# 7. Determinism
# ---------------------------------------------------------------------

def test_identical_runs_are_byte_identical(planted):
    artifact, events, controls, holdout = planted
    kw = dict(holdout=holdout, region_basins=REGION_BASINS)
    r1 = run_assoc(artifact, events, controls, UNIT_BASINS, **kw)
    r2 = run_assoc(artifact, events, controls, UNIT_BASINS, **kw)
    assert canonical_json(r1.to_dict()) == canonical_json(r2.to_dict())
    assert sha256_canonical(r1.to_dict()) == sha256_canonical(
        r2.to_dict())
    # Input order of assignments does not matter either.
    shuffled = dataclasses.replace(
        artifact, assignments=tuple(reversed(artifact.assignments)))
    r3 = run_assoc(shuffled, events, controls, UNIT_BASINS, **kw)
    assert sha256_canonical(r3.to_dict()) == sha256_canonical(
        r1.to_dict())


def test_bootstrap_is_deterministic(planted):
    artifact, events, controls, holdout = planted
    kw = dict(holdout=holdout, region_basins=REGION_BASINS)
    r1 = run_assoc(artifact, events, controls, UNIT_BASINS,
                         n_boot=64, seed=9, **kw)
    r2 = run_assoc(artifact, events, controls, UNIT_BASINS,
                         n_boot=64, seed=9, **kw)
    assert r1.enrichment == r2.enrichment
    # Standalone bootstrap on an empty table returns {} deterministically.
    assert event_group_bootstrap({}, [], n_boot=8, seed=1) == {}


# ---------------------------------------------------------------------
# 8. Claim-scan cleanliness
# ---------------------------------------------------------------------

def test_claim_scan_clean_on_text_and_source(planted):
    artifact, events, controls, holdout = planted
    report = run_assoc(artifact, events, controls, UNIT_BASINS,
                             holdout=holdout,
                             region_basins=REGION_BASINS)
    assert scan_claims_text(association_report_text(report)) == []
    source = Path(
        __import__("nepal.experiment_v0.association",
                   fromlist=["x"]).__file__).read_text(
        encoding="utf-8")
    assert scan_claims_text(source) == []
    # Canonical JSON of the report is also claim-clean.
    assert scan_claims_text(canonical_json(report.to_dict())) == []


def test_report_text_is_associational_language(planted):
    artifact, events, controls, holdout = planted
    report = run_assoc(artifact, events, controls, UNIT_BASINS,
                             holdout=holdout,
                             region_basins=REGION_BASINS)
    text = association_report_text(report)
    assert "co-occur" in text or "enriched" in text
    assert report.status in NEUTRAL_RESEARCH_STATUSES
    assert report.status in text
    assert not report.problems()


# ---------------------------------------------------------------------
# 9. Slice report
# ---------------------------------------------------------------------

def test_slices_pooled_basin_season(planted):
    artifact, events, controls, holdout = planted
    report = run_assoc(artifact, events, controls, UNIT_BASINS,
                             holdout=holdout, region_basins=REGION_BASINS)
    assert set(report.slices) == {"pooled", "per_basin", "per_season"}
    assert report.slices["pooled"] == report.enrichment
    assert set(report.slices["per_basin"]) == set(BASINS)
    for basin, cells in report.slices["per_basin"].items():
        assert isinstance(cells, dict)
    assert report.slices["per_season"]  # at least one season slice
    for season, cells in report.slices["per_season"].items():
        assert season in ("DJF", "MAM", "JJA", "SON")
        assert isinstance(cells, dict)
    # Novelty slice: the sparse R_RARE regime is reported under the
    # rarity threshold with a full enrichment cell.
    assert "R_RARE" in report.novelty["rare_regimes"]
    assert report.novelty["enrichment"]["R_RARE"]["control_share"] \
        <= report.novelty["rare_threshold"]
    assert report.transitions  # transition-pair rows present


# ---------------------------------------------------------------------
# Three-valued censoring discipline
# ---------------------------------------------------------------------

def test_censored_rows_never_enter_denominators(planted):
    artifact, events, controls, _holdout = planted
    # One unadjudicated label and one non-NEGATIVE control: both are
    # censored, never folded into positive or negative cells.  Both
    # still satisfy the holdout binding (in-region, mapped, test).
    extra_event = dataclasses.replace(
        events[0], event_id="ev-pending", basin_id="karnali",
        adjudication_state="UNADJUDICATED")
    extra_control = dataclasses.replace(
        controls[0], control_id="ctl-censored",
        opportunity_id="opp-ctl-censored",
        opportunity_state="OBSERVED_PARTIAL",
        state="CENSORED_OR_AMBIGUOUS")
    holdout = make_holdout(
        events, extra_assignments={"ev-pending": "karnali_eval"})
    report = run_assoc(
        artifact, events + [extra_event], controls + [extra_control],
        UNIT_BASINS, holdout=holdout, region_basins=REGION_BASINS)
    assert report.n_event_windows == len(events)
    assert report.n_control_windows == len(controls)
    assert report.n_event_groups >= MIN_EVENT_GROUPS


# ---------------------------------------------------------------------
# Holdout binding: fail-closed admission (all violations -> ValueError)
# ---------------------------------------------------------------------

def test_train_or_validation_event_rejected(planted):
    artifact, events, controls, _holdout = planted
    # A train-group-assigned label is never held-out evidence.
    holdout = make_holdout(
        events, extra_assignments={"ev-leak": "north_train"})
    leaky = list(events) + [make_event("ev-leak", "koshi",
                                       _BASE_DATE + timedelta(days=3))]
    with pytest.raises(ValueError, match="never"):
        run_assoc(artifact, leaky, controls, UNIT_BASINS,
                        holdout=holdout, region_basins=REGION_BASINS)


def test_event_outside_eval_regions_rejected(planted):
    artifact, events, controls, _holdout = planted
    outside = make_event("ev-out", "mahakali",
                         _BASE_DATE + timedelta(days=4))
    holdout = make_holdout(
        events, extra_assignments={"ev-out": "karnali_eval"})
    with pytest.raises(ValueError, match="outside the locked"):
        run_assoc(artifact, events + [outside], controls,
                        UNIT_BASINS, holdout=holdout,
                        region_basins=REGION_BASINS)


def test_event_absent_from_assignments_rejected(planted):
    artifact, events, controls, _holdout = planted
    ghost = make_event("ev-ghost", "koshi",
                       _BASE_DATE + timedelta(days=5))
    holdout = make_holdout(events)  # 'ev-ghost' never mapped
    with pytest.raises(ValueError, match="absent from"):
        run_assoc(artifact, events + [ghost], controls,
                        UNIT_BASINS, holdout=holdout,
                        region_basins=REGION_BASINS)


def test_control_outside_eval_regions_rejected(planted):
    artifact, events, controls, holdout = planted
    unit_basins = dict(UNIT_BASINS, **{"unit-out-0": "mahakali"})
    outside_ctl = make_control("ctl-out", "unit-out-0",
                               _BASE_DATE + timedelta(days=11))
    with pytest.raises(ValueError, match="outside the locked"):
        run_assoc(artifact, events, controls + [outside_ctl],
                        unit_basins, holdout=holdout,
                        region_basins=REGION_BASINS)


def test_incomplete_unit_basins_rejected(planted):
    artifact, events, controls, holdout = planted
    incomplete = {k: v for k, v in UNIT_BASINS.items()
                  if k != "unit-koshi-1"}
    with pytest.raises(ValueError, match="missing from"):
        run_assoc(artifact, events, controls, incomplete,
                        holdout=holdout, region_basins=REGION_BASINS)


def test_evaluated_unit_without_assignments_rejected(planted):
    artifact, events, controls, holdout = planted
    # A unit inside an evaluation region with zero assignment rows.
    unit_basins = dict(UNIT_BASINS, **{"unit-koshi-9": "koshi"})
    with pytest.raises(ValueError, match="zero regime assignments"):
        run_assoc(artifact, events, controls, unit_basins,
                        holdout=holdout, region_basins=REGION_BASINS)


def test_unlocked_or_underregioned_holdout_rejected(planted):
    artifact, events, controls, _holdout = planted
    unlocked = make_holdout(events, test_locked=False)
    with pytest.raises(ValueError):
        run_assoc(artifact, events, controls, UNIT_BASINS,
                        holdout=unlocked,
                        region_basins=REGION_BASINS)
    # Fewer than two evaluation regions cannot support held-out scope.
    one_region = make_holdout(
        events, test_groups=("karnali_eval", "gandaki_eval",
                             "koshi_eval"),
        evaluation_region_names=("karnali_eval",))
    with pytest.raises(ValueError):
        run_assoc(artifact, events, controls, UNIT_BASINS,
                        holdout=one_region,
                        region_basins={"karnali_eval": ("karnali",)})


def test_region_basins_must_match_eval_region_names(planted):
    artifact, events, controls, holdout = planted
    with pytest.raises(ValueError, match="region_basins keys"):
        run_assoc(
            artifact, events, controls, UNIT_BASINS, holdout=holdout,
            region_basins={"karnali_eval": ("karnali",)})


def test_binding_violation_lists_all_problems(planted):
    artifact, events, controls, _holdout = planted
    outside = make_event("ev-out", "mahakali",
                         _BASE_DATE + timedelta(days=4))
    ghost = make_event("ev-ghost", "koshi",
                       _BASE_DATE + timedelta(days=5))
    holdout = make_holdout(
        events, extra_assignments={"ev-out": "karnali_eval"})
    with pytest.raises(ValueError) as excinfo:
        run_assoc(artifact, events + [outside, ghost], controls,
                        UNIT_BASINS, holdout=holdout,
                        region_basins=REGION_BASINS)
    message = str(excinfo.value)
    assert "outside the locked" in message
    assert "absent from" in message  # all problems are reported


def test_report_statuses_are_neutral(planted):
    artifact, events, controls, holdout = planted
    report = run_assoc(artifact, events, controls, UNIT_BASINS,
                             holdout=holdout,
                             region_basins=REGION_BASINS)
    assert report.status in NEUTRAL_RESEARCH_STATUSES


# ---------------------------------------------------------------------
# Opportunity-registry binding (I-08): every control's lineage is
# verified against the registry — never taken on faith
# ---------------------------------------------------------------------

def test_opportunities_argument_is_required(planted):
    artifact, events, controls, holdout = planted
    with pytest.raises(TypeError):
        run_association(artifact, events, controls, UNIT_BASINS,
                        holdout=holdout,
                        region_basins=REGION_BASINS)


def test_non_mapping_opportunities_rejected(planted):
    artifact, events, controls, holdout = planted
    with pytest.raises(ValueError, match="must be a mapping"):
        run_assoc(artifact, events, controls, UNIT_BASINS,
                  holdout=holdout, region_basins=REGION_BASINS,
                  opportunities=None)


def test_control_linking_missing_opportunity_rejected(planted):
    artifact, events, controls, holdout = planted
    opportunities = make_opportunities(controls)
    del opportunities[controls[0].opportunity_id]
    with pytest.raises(ValueError, match="absent from the "
                                         "opportunity registry"):
        run_assoc(artifact, events, controls, UNIT_BASINS,
                  holdout=holdout, region_basins=REGION_BASINS,
                  opportunities=opportunities)


def test_control_opportunity_unit_mismatch_rejected(planted):
    artifact, events, controls, holdout = planted
    opportunities = make_opportunities(controls)
    oid = controls[0].opportunity_id
    opportunities[oid] = dataclasses.replace(
        opportunities[oid], unit_id="unit-gandaki-1")
    with pytest.raises(ValueError, match="does not match"):
        run_assoc(artifact, events, controls, UNIT_BASINS,
                  holdout=holdout, region_basins=REGION_BASINS,
                  opportunities=opportunities)


def test_control_opportunity_window_mismatch_rejected(planted):
    artifact, events, controls, holdout = planted
    opportunities = make_opportunities(controls)
    oid = controls[0].opportunity_id
    opportunities[oid] = dataclasses.replace(
        opportunities[oid], window_end="2020-08-02T00:00:00Z")
    with pytest.raises(ValueError, match="window does not equal"):
        run_assoc(artifact, events, controls, UNIT_BASINS,
                  holdout=holdout, region_basins=REGION_BASINS,
                  opportunities=opportunities)


def test_control_opportunity_state_mismatch_rejected(planted):
    artifact, events, controls, holdout = planted
    # Registry holds the OBSERVED_FULL record; the control asserts a
    # merely-partial linkage — assertion and registry disagree.
    opportunities = make_opportunities(controls)
    tampered = dataclasses.replace(
        controls[0], opportunity_state="OBSERVED_PARTIAL",
        state="CENSORED_OR_AMBIGUOUS")
    with pytest.raises(ValueError, match="does not match "
                                         "opportunity"):
        run_assoc(artifact, events, [tampered] + controls[1:],
                  UNIT_BASINS, holdout=holdout,
                  region_basins=REGION_BASINS,
                  opportunities=opportunities)


def test_negative_control_without_observed_full_rejected(planted):
    artifact, events, controls, holdout = planted
    # A NEGATIVE control whose linked registry opportunity is only
    # OBSERVED_PARTIAL can never enter a control denominator — the
    # registry, not the control's word for it, is the source of truth.
    tampered = dataclasses.replace(
        controls[0], opportunity_state="OBSERVED_PARTIAL")
    controls = [tampered] + controls[1:]
    with pytest.raises(ValueError, match="OBSERVED_FULL"):
        run_assoc(artifact, events, controls, UNIT_BASINS,
                  holdout=holdout, region_basins=REGION_BASINS,
                  opportunities=make_opportunities(controls))


def test_problematic_registry_opportunity_rejected(planted):
    artifact, events, controls, holdout = planted
    opportunities = make_opportunities(controls)
    oid = controls[0].opportunity_id
    # An OBSERVED_FULL record without source frames is not a valid
    # observation — binding it must fail closed.
    opportunities[oid] = dataclasses.replace(
        opportunities[oid], frame_ids=())
    with pytest.raises(ValueError, match="requires actual"):
        run_assoc(artifact, events, controls, UNIT_BASINS,
                  holdout=holdout, region_basins=REGION_BASINS,
                  opportunities=opportunities)


# --------------------------------------------------------------
# Adversarial binding probes (post-audit residuals)
# --------------------------------------------------------------

def _bound(artifact, events, controls, **kw):
    kw.setdefault("holdout", make_holdout(events))
    kw.setdefault("region_basins", REGION_BASINS)
    return run_assoc(artifact, events, controls, UNIT_BASINS, **kw)


def test_duplicate_control_id_rejected():
    events = make_events(21)
    controls = make_controls()
    controls = controls + [dataclasses.replace(controls[0])]
    artifact = planted_artifact(events)
    with pytest.raises(ValueError, match="duplicate control_id"):
        _bound(artifact, events, controls)


def test_duplicate_event_id_rejected():
    events = make_events(21)
    events = events + [dataclasses.replace(
        events[0], cascade_group_id=None)]
    artifact = planted_artifact(events)
    with pytest.raises(ValueError, match="duplicate event_id"):
        _bound(artifact, events, make_controls())


def test_negative_control_overlapping_event_rejected():
    """A control window colliding with an admitted event in the same
    basin cannot assert NEGATIVE — state is derived."""
    events = make_events(21)
    ev = events[0]
    unit = next(u for u, b in UNIT_BASINS.items()
                if b == ev.basin_id)
    bad = make_control("ctl-overlap", unit,
                       _date.fromisoformat(ev.event_time_start[:10]))
    controls = make_controls() + [bad]
    artifact = planted_artifact(events)
    with pytest.raises(ValueError, match="overlaps"):
        _bound(artifact, events, controls)


def test_registry_key_must_match_record_id():
    """The cited key must equal the record's own opportunity_id —
    an aliased registry entry cannot stand in."""
    controls = make_controls()
    opps = make_opportunities(controls)
    aliased = {k: dataclasses.replace(v, opportunity_id=k + "-fake")
               for k, v in opps.items()}
    events = make_events(21)
    artifact = planted_artifact(events)
    with pytest.raises(ValueError, match="does not match"):
        _bound(artifact, events, controls, opportunities=aliased)


def test_basin_claimed_by_two_regions_rejected():
    events = make_events(21)
    controls = make_controls()
    artifact = planted_artifact(events)
    region_basins = {"karnali_eval": {"karnali"},
                     "gandaki_eval": {"gandaki", "koshi"},
                     "koshi_eval": {"koshi"}}
    holdout = make_holdout(events)
    with pytest.raises(ValueError, match="claimed by both"):
        run_association(
            artifact, events, controls, UNIT_BASINS,
            holdout=holdout, region_basins=region_basins,
            opportunities=make_opportunities(controls),
            n_boot=8, seed=0)


# ---------------------------------------------------------------------
# Family / multiplicity / null-completeness adversarial checks
# ---------------------------------------------------------------------

def test_undeclared_horizon_mode_rejects(planted):
    artifact, events, controls, holdout = planted
    with pytest.raises(ValueError, match="horizon_family"):
        run_assoc(artifact, events, controls, UNIT_BASINS,
                  holdout=holdout, region_basins=REGION_BASINS,
                  horizon_family=("midpoint", "post_hoc_mode"))


def test_empty_horizon_family_rejects(planted):
    artifact, events, controls, holdout = planted
    with pytest.raises(ValueError, match="horizon_family"):
        run_assoc(artifact, events, controls, UNIT_BASINS,
                  holdout=holdout, region_basins=REGION_BASINS,
                  horizon_family=())


def test_multiplicity_and_nulls_bound_in_report(planted):
    artifact, events, controls, holdout = planted
    rep = run_assoc(artifact, events, controls, UNIT_BASINS,
                    holdout=holdout, region_basins=REGION_BASINS)
    # the declared family and Holm correction are bound into the
    # report — a post-hoc cell cannot be appended to the verdict
    assert rep.horizon_family == ("midpoint", "uniform",
                                "worst_case")
    assert rep.lookback_horizons == ("0d",)
    assert rep.multiplicity["method"] == "holm"
    assert rep.multiplicity["alpha"] == 0.05
    assert "label_shuffle" in rep.negative_controls
    assert rep.negative_controls["label_shuffle"]["digest"]
    # every mandatory sensitivity axis is dispositioned explicitly
    for axis in ("interval_placement", "precision",
                 "observation_effort", "era_boundary",
                 "feature_subset", "missingness", "mechanism"):
        assert rep.sensitivities[axis]["status"] in (
            "PASS", "FAIL", "NOT_APPLICABLE")
        assert rep.sensitivities[axis]["reason"]


def test_label_shuffle_null_is_deterministic(planted):
    artifact, events, controls, holdout = planted
    kw = dict(holdout=holdout, region_basins=REGION_BASINS)
    r1 = run_assoc(artifact, events, controls, UNIT_BASINS, **kw)
    r2 = run_assoc(artifact, events, controls, UNIT_BASINS, **kw)
    assert (r1.negative_controls["label_shuffle"]["digest"]
            == r2.negative_controls["label_shuffle"]["digest"])

# ---------------------------------------------------------------------
# ASSOC-C01: real look-back horizons vs placement modes
# ---------------------------------------------------------------------

_SEASON_BY_MONTH = {
    12: "DJF", 1: "DJF", 2: "DJF", 3: "MAM", 4: "MAM", 5: "MAM",
    6: "JJA", 7: "JJA", 8: "JJA", 9: "SON", 10: "SON", 11: "SON",
}


def _ev_dates(ev) -> list:
    s = _date.fromisoformat(ev.event_time_start[:10])
    e = _date.fromisoformat(ev.event_time_end[:10])
    return _dates_between(s, e)


def _basin_units() -> dict:
    out: dict[str, list] = {}
    for u, b in UNIT_BASINS.items():
        out.setdefault(b, []).append(u)
    return out


def _midpoint_date(ev) -> _date:
    s = datetime.strptime(ev.event_time_start, "%Y-%m-%dT%H:%M:%SZ")
    e = datetime.strptime(ev.event_time_end, "%Y-%m-%dT%H:%M:%SZ")
    return (s + (e - s) / 2).date()


def _rows_with(plant_cells: set, event_cells: set,
               background_mod: int = 41) -> dict:
    """Assignment rows: R_PLANT on ``plant_cells``, a sparse R_PLANT
    background (~1/background_mod) on non-event cells only, and
    hash-driven baselines elsewhere."""
    rows: dict[tuple[str, str], str] = {}
    for unit in sorted(UNIT_BASINS):
        for d in _dates_between(_BASE_DATE, _ASSIGN_END):
            key = (unit, d.isoformat())
            if key in plant_cells:
                rows[key] = PLANTED_REGIME
            elif key in event_cells:
                rows[key] = "R%d" % (
                    int(_h(f"base|{unit}|{d.isoformat()}")[:8], 16)
                    % 3)
            elif d.timetuple().tm_yday % background_mod == 0:
                rows[key] = PLANTED_REGIME
            else:
                rows[key] = "R%d" % (
                    int(_h(f"base|{unit}|{d.isoformat()}")[:8], 16)
                    % 3)
    return rows


def test_lookback_horizon_extends_event_window():
    """A regime planted only in the 3 days *preceding* each event is
    invisible at the identity horizon and enriched at '3d'."""
    events = make_events(21)
    holdout = make_holdout(events)
    bu = _basin_units()
    event_cells = {(u, d.isoformat()) for ev in events
                   for d in _ev_dates(ev)
                   for u in bu[ev.basin_id]}
    plant_cells = set()
    for ev in events:
        s = _date.fromisoformat(ev.event_time_start[:10])
        for k in (1, 2, 3):
            day = (s - timedelta(days=k)).isoformat()
            for u in bu[ev.basin_id]:
                plant_cells.add((u, day))
    plant_cells -= event_cells
    artifact = _rows_to_artifact(_rows_with(plant_cells, event_cells))
    kw = dict(holdout=holdout, region_basins=REGION_BASINS,
              horizon_family=("uniform",))
    rep0 = run_assoc(artifact, events, make_controls(), UNIT_BASINS,
                     lookback_horizons=("0d",), **kw)
    rep3 = run_assoc(artifact, events, make_controls(), UNIT_BASINS,
                     lookback_horizons=("3d",), **kw)
    assert rep0.lookback_horizons == ("0d",)
    assert rep3.lookback_horizons == ("3d",)
    # At 0d the event window touches no planted date at all.
    assert rep0.enrichment[PLANTED_REGIME]["event_share"] == 0.0
    assert rep0.enrichment[PLANTED_REGIME]["ratio"] is not None
    assert rep0.enrichment[PLANTED_REGIME]["ratio"] < 1.0
    # At 3d the look-back window preceding the anchor covers the
    # planted days.
    assert rep3.enrichment[PLANTED_REGIME]["ratio"] > 1.0
    assert f"3d|uniform|{PLANTED_REGIME}" in \
        rep3.multiplicity["holm_rejected"]
    assert f"0d|uniform|{PLANTED_REGIME}" not in \
        rep0.multiplicity["holm_rejected"]


def test_undeclared_lookback_horizon_rejects(planted):
    artifact, events, controls, holdout = planted
    kw = dict(holdout=holdout, region_basins=REGION_BASINS)
    for bad in (("3x",), ("week",), ("-1d",), (3,), ("3.5d",), ()):
        with pytest.raises(ValueError, match="lookback"):
            run_assoc(artifact, events, controls, UNIT_BASINS,
                      lookback_horizons=bad, **kw)


def test_holm_family_spans_lookback_and_placement(planted):
    artifact, events, controls, holdout = planted
    rep = run_assoc(artifact, events, controls, UNIT_BASINS,
                    holdout=holdout, region_basins=REGION_BASINS,
                    lookback_horizons=("0d", "3d"),
                    horizon_family=("midpoint", "uniform"))
    pvals = rep.multiplicity["family_pvals"]
    assert pvals
    # every p_enrich feeding Holm is a regime x horizon x placement
    # cell — and only cells inside the declared family appear
    assert all(k.count("|") == 2 for k in pvals)
    for h in ("0d", "3d"):
        for m in ("midpoint", "uniform"):
            assert any(k.startswith(f"{h}|{m}|") for k in pvals)
    assert not any(k.startswith("7d|") for k in pvals)
    assert not any(k.startswith("0d|worst_case|") for k in pvals)
    assert rep.lookback_horizons == ("0d", "3d")


def test_single_placement_hit_cannot_promote():
    """R_PLANT planted only on each event's midpoint date is enriched
    under midpoint and uniform but collapses under worst_case — the
    all-cells rule keeps it out of the supported verdict."""
    events = make_events(21)
    holdout = make_holdout(events)
    bu = _basin_units()
    event_cells = {(u, d.isoformat()) for ev in events
                   for d in _ev_dates(ev)
                   for u in bu[ev.basin_id]}
    mid_cells = {(u, _midpoint_date(ev).isoformat())
                 for ev in events for u in bu[ev.basin_id]}
    artifact = _rows_to_artifact(
        _rows_with(mid_cells & event_cells, event_cells))
    rep = run_assoc(artifact, events, make_controls(), UNIT_BASINS,
                    holdout=holdout, region_basins=REGION_BASINS)
    assert rep.n_event_groups >= MIN_EVENT_GROUPS
    assert rep.status == "DESCRIPTIVE_REGIME_ONLY"
    assert f"0d|midpoint|{PLANTED_REGIME}" in \
        rep.multiplicity["holm_rejected"]
    assert f"0d|worst_case|{PLANTED_REGIME}" not in \
        rep.multiplicity["holm_rejected"]


# ---------------------------------------------------------------------
# ASSOC-C02: stratified label shuffle
# ---------------------------------------------------------------------

def test_label_shuffle_is_stratified_by_season_and_basin():
    """When every (season, basin) stratum carries a single distinct
    label, a within-stratum permutation is the identity — every null
    replicate reproduces the observed ratio exactly (p == 1.0).  A
    global shuffle would leak labels across strata and could not."""
    events = make_events(21)
    holdout = make_holdout(events)
    rows = {}
    for unit in sorted(UNIT_BASINS):
        for d in _dates_between(_BASE_DATE, _ASSIGN_END):
            rows[(unit, d.isoformat())] = (
                f"R-{UNIT_BASINS[unit]}-{_SEASON_BY_MONTH[d.month]}")
    artifact = _rows_to_artifact(rows)
    rep = run_assoc(artifact, events, make_controls(), UNIT_BASINS,
                    holdout=holdout, region_basins=REGION_BASINS)
    null = rep.negative_controls["label_shuffle"]
    assert null["flat"] is True
    assert null["per_regime"]
    for rid, entry in null["per_regime"].items():
        assert entry["p"] is None or entry["p"] == 1.0, (rid, entry)
        if entry["p"] == 1.0:
            assert entry["null_p95"] == entry["observed_ratio"]


def test_label_shuffle_stratification_is_order_invariant(planted):
    artifact, events, controls, holdout = planted
    kw = dict(holdout=holdout, region_basins=REGION_BASINS)
    rev = dataclasses.replace(
        artifact, assignments=tuple(reversed(artifact.assignments)))
    r1 = run_assoc(artifact, events, controls, UNIT_BASINS, **kw)
    r2 = run_assoc(rev, events, controls, UNIT_BASINS, **kw)
    assert (r1.negative_controls["label_shuffle"]["digest"]
            == r2.negative_controls["label_shuffle"]["digest"])


# ---------------------------------------------------------------------
# ASSOC-C03: mandatory sensitivity registry
# ---------------------------------------------------------------------

def test_sensitivity_registry_complete(planted):
    artifact, events, controls, holdout = planted
    rep = run_assoc(artifact, events, controls, UNIT_BASINS,
                    holdout=holdout, region_basins=REGION_BASINS)
    expected = {"interval_placement", "precision",
                "observation_effort", "era_boundary",
                "feature_subset", "missingness", "mechanism"}
    assert expected <= set(rep.sensitivities)
    assert "era" not in rep.sensitivities  # renamed to era_boundary
    for axis in expected:
        entry = rep.sensitivities[axis]
        assert entry["status"] in ("PASS", "FAIL", "NOT_APPLICABLE")
        assert isinstance(entry["reason"], str) and entry["reason"]
    fs = rep.sensitivities["feature_subset"]
    assert fs["status"] == "NOT_APPLICABLE"
    assert fs["reason"] == ("regime artifact frozen — feature "
                            "ablation is a producer-side axis")
    # single-calendar-year fixture: no era boundary exists to split
    assert rep.sensitivities["era_boundary"]["status"] == \
        "NOT_APPLICABLE"
    # day-precision events are planted too -> direction survives
    assert rep.sensitivities["precision"]["status"] == "PASS"
    assert rep.sensitivities["mechanism"]["status"] == "PASS"


def test_precision_sensitivity_fail_blocks_supported():
    """Day-precision events sit on unplanted dates; only
    interval-precision events are planted.  The precise-only
    recomputation reverses the enrichment -> FAIL -> not supported."""
    events = make_events(21)
    holdout = make_holdout(events)
    bu = _basin_units()
    event_cells = {(u, d.isoformat()) for ev in events
                   for d in _ev_dates(ev)
                   for u in bu[ev.basin_id]}
    plant_cells = {(u, d.isoformat()) for ev in events
                   if ev.event_time_precision != "day"
                   for d in _ev_dates(ev)
                   for u in bu[ev.basin_id]}
    artifact = _rows_to_artifact(
        _rows_with(plant_cells, event_cells))
    rep = run_assoc(artifact, events, make_controls(), UNIT_BASINS,
                    holdout=holdout, region_basins=REGION_BASINS)
    assert rep.n_event_groups >= MIN_EVENT_GROUPS
    assert rep.sensitivities["precision"]["status"] == "FAIL"
    assert rep.status == "DESCRIPTIVE_REGIME_ONLY"


def test_mechanism_sensitivity_fail_blocks_supported():
    """Two mechanism slices (via vertical_id): the planted regime
    holds only for one mechanism — the other slice has >=3 groups
    and reverses -> mechanism FAIL blocks the supported verdict."""
    events = make_events(21)
    events = [dataclasses.replace(e, vertical_id="glof")
              if i % 2 else e for i, e in enumerate(events)]
    holdout = make_holdout(events)
    bu = _basin_units()
    event_cells = {(u, d.isoformat()) for ev in events
                   for d in _ev_dates(ev)
                   for u in bu[ev.basin_id]}
    plant_cells = {(u, d.isoformat()) for i, ev in enumerate(events)
                   if i % 2 == 0
                   for d in _ev_dates(ev)
                   for u in bu[ev.basin_id]}
    artifact = _rows_to_artifact(
        _rows_with(plant_cells, event_cells))
    rep = run_assoc(artifact, events, make_controls(), UNIT_BASINS,
                    holdout=holdout, region_basins=REGION_BASINS)
    assert rep.n_event_groups >= MIN_EVENT_GROUPS
    mech = rep.sensitivities["mechanism"]
    assert mech["status"] == "FAIL"
    assert "glof" in mech["per_mechanism_ratios"]
    assert rep.status == "DESCRIPTIVE_REGIME_ONLY"


# ---------------------------------------------------------------------
# ASSOC-C04: report problems() + artifact provenance floor
# ---------------------------------------------------------------------

def test_report_problems_block_hollow_supported(planted):
    artifact, events, controls, holdout = planted
    rep = run_assoc(artifact, events, controls, UNIT_BASINS,
                    holdout=holdout, region_basins=REGION_BASINS)
    assert rep.status == "REGIME_ASSOCIATION_SUPPORTED"
    assert rep.problems() == []
    # A supported verdict cannot stand on empty enrichment.
    assert dataclasses.replace(rep, enrichment={}).problems()
    # ... or with a required null family missing.
    neg = dict(rep.negative_controls)
    neg.pop("label_shuffle")
    assert dataclasses.replace(rep, negative_controls=neg).problems()
    # ... or a missing sensitivity disposition.
    sens = dict(rep.sensitivities)
    sens.pop("mechanism")
    assert dataclasses.replace(rep, sensitivities=sens).problems()
    # ... or an empty declared horizon family.
    assert dataclasses.replace(rep, horizon_family=()).problems()
    assert dataclasses.replace(rep, lookback_horizons=()).problems()
    # ... or a FAIL disposition (any FAIL blocks supported).
    bad = dict(rep.sensitivities)
    bad["precision"] = {"status": "FAIL", "reason": "reversed"}
    assert dataclasses.replace(rep, sensitivities=bad).problems()


def test_artifact_provenance_floor_rejected(planted):
    artifact, events, controls, holdout = planted
    kw = dict(holdout=holdout, region_basins=REGION_BASINS)
    # Locally-self-digested minimal artifacts — empty assignments,
    # non-64-hex digest, or missing producer provenance fields —
    # are rejected at the door; synthetic fixtures built on the same
    # record satisfy the floor because they carry those fields.
    with pytest.raises(ValueError, match="artifact"):
        run_assoc(dataclasses.replace(artifact, assignments=()),
                  events, controls, UNIT_BASINS, **kw)
    with pytest.raises(ValueError, match="artifact"):
        run_assoc(dataclasses.replace(
            artifact, regime_digest="self-digested-locally"),
            events, controls, UNIT_BASINS, **kw)
    with pytest.raises(ValueError, match="artifact"):
        run_assoc(dataclasses.replace(artifact, seeds=()),
                  events, controls, UNIT_BASINS, **kw)
    with pytest.raises(ValueError, match="RegimeAssignmentArtifact"):
        run_association(object(), events, controls, UNIT_BASINS,
                        holdout=holdout, region_basins=REGION_BASINS,
                        opportunities=make_opportunities(controls))


# ---- Round-4: calibrated inference + spatial null + coverage ----

def test_spatial_shift_null_present(planted):
    """ASSOC-04: the geography-preserving shift null must execute —
    dates shifted, basin geography fixed."""
    artifact, events, controls, _h = planted
    holdout = _h if _h is not None else make_holdout(events)
    rep = run_assoc(artifact, events, controls,
                    holdout=holdout, region_basins=REGION_BASINS)
    shift = rep.negative_controls["spatial_shift"]
    assert shift["name"] == "spatial_shift"
    assert shift["strata"] == "basin_fixed_date_shift"
    assert len(shift["offsets"]) >= 4
    assert len(shift["digest"]) == 64
    for rid, r in shift["per_regime"].items():
        assert "p" in r and "shifted_ratios" in r


def test_family_pvals_are_permutation_not_bootstrap(planted):
    """ASSOC-02/03: the Holm family runs on stratified permutation
    p-values — the bootstrap p_enrich is never the inferential
    surface."""
    artifact, events, controls, _h = planted
    holdout = _h if _h is not None else make_holdout(events)
    rep = run_assoc(artifact, events, controls,
                    holdout=holdout, region_basins=REGION_BASINS)
    mult = rep.multiplicity
    assert mult["inference"] == "stratified_permutation_p"
    assert mult["family_pvals"]
    # every declared family cell carries null coverage
    assert mult["null_coverage"]
    assert all(v == "executed"
               for v in mult["null_coverage"].values())
    # family keys cover horizon x placement x regime
    for h in mult["lookback_horizons"]:
        for m in mult["placement_modes"]:
            assert mult["null_coverage"].get(f"{h}|{m}") \
                == "executed"


def test_hollow_supported_lacks_null_coverage(planted):
    """ASSOC-07: a fabricated SUPPORTED report without per-cell null
    coverage or permutation p-values fails problems()."""
    artifact, events, controls, _h = planted
    holdout = _h if _h is not None else make_holdout(events)
    rep = run_assoc(artifact, events, controls,
                    holdout=holdout, region_basins=REGION_BASINS)
    d = rep.to_dict()
    d["status"] = "REGIME_ASSOCIATION_SUPPORTED"
    d["multiplicity"] = {"method": "holm", "family_pvals": {},
                         "null_coverage": {}}
    import dataclasses
    from nepal.experiment_v0.association import AssociationReport
    bad = AssociationReport(**{
        f.name: d[f.name]
        for f in dataclasses.fields(AssociationReport)})
    probs = bad.problems()
    assert probs
    assert any("null_coverage" in p or "family_pvals" in p
               or "Holm" in p for p in probs)


def test_precision_exclusion_recorded_for_short_lookback(planted):
    """ASSOC-01: a look-back horizon shorter than an event's own
    interval width excludes that event — the exclusion is counted
    per cell."""
    artifact, events, controls, _h = planted
    holdout = _h if _h is not None else make_holdout(events)
    rep = run_assoc(artifact, events, controls,
                    holdout=holdout, region_basins=REGION_BASINS,
                    lookback_horizons=("0d", "2d"))
    excl = rep.multiplicity.get("precision_exclusions", {})
    assert excl
    assert all(isinstance(v, int) for v in excl.values())
    # the 2d cell excludes events whose declared interval is
    # coarser than the horizon (3-day-wide fixture members)
    assert excl["2d|midpoint"] > 0

# ---------------------------------------------------------------------
# Round-5: spatial-null resolution, horizon allowlist, digest
# revalidation
# ---------------------------------------------------------------------

def test_spatial_shift_null_resolution_reaches_alpha(planted):
    """ASSOC-01 (R5): the default spatial-shift null carries >=20
    preregistered nonzero offsets — the p-grid resolves below the
    family alpha and a regime beating every shifted replicate
    achieves p < 0.05."""
    artifact, events, controls, holdout = planted
    rep = run_assoc(artifact, events, controls,
                    holdout=holdout, region_basins=REGION_BASINS)
    shift = rep.negative_controls["spatial_shift"]
    assert len(SPATIAL_SHIFT_OFFSETS) >= MIN_SPATIAL_SHIFTS >= 20
    assert all(int(o) != 0 for o in SPATIAL_SHIFT_OFFSETS)
    assert len(set(SPATIAL_SHIFT_OFFSETS)) == \
        len(SPATIAL_SHIFT_OFFSETS)
    assert shift["n_shifts"] == len(shift["offsets"]) \
        == len(SPATIAL_SHIFT_OFFSETS)
    assert 1.0 / shift["n_shifts"] < 0.05
    # the planted regime beats every shifted replicate -> p == 0,
    # reachable only because the grid resolves below alpha
    p = shift["per_regime"][PLANTED_REGIME]["p"]
    assert p is not None and p < 0.05
    # shifted_ratios keys stay canonical-JSON strings, never ints
    for rid, r in shift["per_regime"].items():
        assert all(type(k) is str
                   for k in r.get("shifted_ratios", {})), rid


def test_spatial_shift_null_short_offsets_rejected(planted):
    """A caller-declared offset family under MIN_SPATIAL_SHIFTS
    nonzero offsets fails closed — a low-resolution null can never
    support a verdict."""
    artifact, events, controls, _h = planted
    groups = _assoc._group_events(events)
    basin_units = _assoc._basin_units(UNIT_BASINS)
    for bad in ((), (-90, -30, 30, 90), tuple(range(-9, 10)),
                tuple(SPATIAL_SHIFT_OFFSETS[:19])):
        with pytest.raises(ValueError, match="nonzero day offsets"):
            _assoc._spatial_shift_null(
                artifact, groups, controls, basin_units, UNIT_BASINS,
                mode="midpoint", lookback_days=0, offsets=bad)
    # exactly at the floor admits; the default family runs clean
    rec = _assoc._spatial_shift_null(
        artifact, groups, controls, basin_units, UNIT_BASINS,
        mode="midpoint", lookback_days=0)
    assert rec["n_shifts"] == len(SPATIAL_SHIFT_OFFSETS)
    assert len(rec["digest"]) == 64


def test_lookback_horizon_outside_allowlist_rejected(planted):
    """ASSOC-02 (R5): a syntactically valid horizon whose day count
    sits outside the declared policy allowlist rejects."""
    artifact, events, controls, holdout = planted
    kw = dict(holdout=holdout, region_basins=REGION_BASINS)
    for bad in (("1d",), ("4d",), ("45d",), ("0d", "99d")):
        with pytest.raises(ValueError, match="lookback"):
            run_assoc(artifact, events, controls, UNIT_BASINS,
                      lookback_horizons=bad, **kw)
    # the declared allowlist itself admits — every named member
    rep = run_assoc(artifact, events, controls, UNIT_BASINS,
                    lookback_horizons=tuple(
                        f"{d}d" for d in ALLOWED_LOOKBACK_DAYS),
                    **kw)
    assert rep.lookback_horizons == tuple(
        f"{d}d" for d in ALLOWED_LOOKBACK_DAYS)


def test_negative_control_digest_tamper_flagged(planted):
    """ASSOC-03 (R5): problems() recomputes every negative-control
    digest over the exact creation payload — a record altered after
    signing can never stand behind a supported verdict."""
    artifact, events, controls, holdout = planted
    rep = run_assoc(artifact, events, controls,
                    holdout=holdout, region_basins=REGION_BASINS)
    assert rep.status == "REGIME_ASSOCIATION_SUPPORTED"
    assert rep.problems() == []
    # tamper the spatial-shift payload without re-signing
    neg = dict(rep.negative_controls)
    bad_shift = dict(neg["spatial_shift"])
    bad_shift["per_regime"] = dict(bad_shift["per_regime"])
    bad_shift["per_regime"][PLANTED_REGIME] = {
        "observed_ratio": 99.0, "p": 0.0, "shifted_ratios": {}}
    neg["spatial_shift"] = bad_shift
    probs = dataclasses.replace(
        rep, negative_controls=neg).problems()
    assert any("spatial_shift" in p and "digest" in p
               for p in probs), probs
    # tamper the label-shuffle digest-bound payload
    neg = dict(rep.negative_controls)
    bad_ls = dict(neg["label_shuffle"])
    bad_ls["per_regime"] = {PLANTED_REGIME: {"p": 0.0}}
    neg["label_shuffle"] = bad_ls
    probs = dataclasses.replace(
        rep, negative_controls=neg).problems()
    assert any("label_shuffle" in p and "digest" in p
               for p in probs), probs
    # a replaced-but-well-formed digest string still mismatches
    neg = dict(rep.negative_controls)
    bad_ls = dict(neg["label_shuffle"])
    bad_ls["digest"] = "0" * 64
    neg["label_shuffle"] = bad_ls
    probs = dataclasses.replace(
        rep, negative_controls=neg).problems()
    assert any("label_shuffle" in p and "digest" in p
               for p in probs), probs
    # a malformed digest shape is flagged outright
    neg = dict(rep.negative_controls)
    bad_shift = dict(neg["spatial_shift"])
    bad_shift["digest"] = "not-a-digest"
    neg["spatial_shift"] = bad_shift
    probs = dataclasses.replace(
        rep, negative_controls=neg).problems()
    assert any("malformed digest" in p for p in probs), probs
