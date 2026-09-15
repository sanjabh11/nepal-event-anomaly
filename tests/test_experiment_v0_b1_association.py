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

from nepal.experiment_v0.association import (
    MIN_EVENT_GROUPS, PLACEMENT_MODES, AssociationReport,
    RegimeAssignmentArtifact, association_report_text,
    event_group_bootstrap, run_association)
from nepal.research_v0._hashing import canonical_json, sha256_canonical
from nepal.research_v0.gates import scan_claims_text
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
    """NEGATIVE controls: every unit gets five-day windows offset from
    the event grid (mid-gap, deterministic)."""
    controls = []
    i = 0
    for unit in sorted(UNIT_BASINS):
        for k in range(10):
            start = _BASE_DATE + timedelta(days=10 + k * 14)
            controls.append(make_control(f"ctl-{i:03d}", unit, start))
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
    a doctored registry pass ``opportunities=`` explicitly."""
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


def _rows_to_artifact(rows: dict[tuple[str, str], str],
                      artifact_id: str = "regime-syn-b1",
                      **kw) -> RegimeAssignmentArtifact:
    assignments = tuple(sorted(
        (u, d, r) for (u, d), r in rows.items()))
    return RegimeAssignmentArtifact(
        artifact_id=artifact_id,
        regime_digest=hashlib.sha256(
            b"synthetic-regime-partition").hexdigest(),
        assignments=assignments,
        seeds=(11, 23, 42), **kw)


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
    assert set(neg) == {"placebo", "impossible_regime",
                       "time_reversed"}
    for name, entry in neg.items():
        assert entry["flat"], f"{name} not flat: {entry}"
        for cell in entry["per_regime"].values():
            if cell["ci_low"] is not None:
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
