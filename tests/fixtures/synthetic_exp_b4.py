"""Deterministic synthetic replay-bundle fixtures for the B4 audit.

Every value is fabricated: no timestamp corresponds to a real issue,
retrieval, or hazard event; no digest binds real bytes; no basin or
region asserts anything about real geography.  These payloads exercise
the serialized replay contract only — they are never a substitute for
governed evidence.

The bundle shape is ``experiment_v0.replay_bundle/v0``: an
``association`` section (frozen regime artifact + event labels +
control windows + holdout + region/basin maps) and a ``forecast``
section (vintage requests + scored cases + holdout + baseline
probability vectors).  Case ``vintage_digest`` values are the
canonical digests of the vintages the replay path admits — the same
derivation ``nepal.experiment_v0.audit.replay_vintage_id`` uses.
"""
from __future__ import annotations

import hashlib
from datetime import date as _date
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from nepal.experiment_v0.audit import replay_vintage_id
from nepal.experiment_v0.association import RegimeAssignmentArtifact
from nepal.experiment_v0.evaluation import ForecastCase
from nepal.experiment_v0.vintages import VintageRequest, build_vintage
from nepal.research_v0._hashing import sha256_canonical
from nepal.research_v0.records import (ControlWindowV0, EventLabelV0,
                                      HoldoutPlanV0,
                                      ObservationOpportunityV0)

from tests.fixtures.synthetic_exp_b2 import synthetic_vintage_request

# ---------------------------------------------------------------------
# Association lane (mirrors the B1 synthetic geometry)
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


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _dates_between(a: _date, b: _date) -> list[_date]:
    out = []
    d = a
    while d <= b:
        out.append(d)
        d += timedelta(days=1)
    return out


def make_event(event_id: str, basin: str, start: _date,
               n_days: int = 1, cascade_group_id: str = "",
               adjudication_state: str = "TWO_REVIEW_AGREE"
               ) -> EventLabelV0:
    """A contract-valid synthetic EventLabelV0."""
    s = datetime(start.year, start.month, start.day,
                 tzinfo=timezone.utc)
    e = s + timedelta(days=n_days)
    label = EventLabelV0(
        event_id=event_id,
        vertical_id="snow_avalanche",
        source_id="synthetic_inventory_v0",
        source_version="0.0.0-synthetic",
        event_time_start=_iso(s),
        event_time_end=_iso(e),
        uncertainty_seconds=(e - s).total_seconds(),
        event_time_precision="day" if n_days <= 1 else "interval",
        event_time_basis="synthetic-interval-bound",
        geometry_role="slope_generalized",
        basin_id=basin,
        cascade_group_id=cascade_group_id,
        adjudication_state=adjudication_state,
        adjudication_notes="synthetic",
        reviewer_ids=("rev-b4-a", "rev-b4-b"))
    assert not label.problems(), label.problems()
    return label


def make_events(count: int = 21) -> list[EventLabelV0]:
    events = []
    for i in range(count):
        events.append(make_event(
            f"ev-{i:03d}", BASINS[i % 3],
            _BASE_DATE + timedelta(days=7 + i * 7),
            n_days=1 + (i % 3)))
    return events


def _event_intervals_by_basin() -> dict[str, list[tuple]]:
    """(start, end) date intervals per basin for the synthetic event
    grid — controls may never overlap these (the same derivation the
    producer applies when emitting NEGATIVE state)."""
    from datetime import date
    intervals: dict[str, list] = {}
    for ev in make_events(21):
        s = date.fromisoformat(ev.event_time_start[:10])
        e = date.fromisoformat(ev.event_time_end[:10])
        intervals.setdefault(ev.basin_id, []).append((s, e))
    return intervals


def make_controls() -> list[ControlWindowV0]:
    """NEGATIVE controls: five-day windows chosen to overlap no
    admitted event in the control's basin."""
    intervals = _event_intervals_by_basin()
    controls = []
    i = 0
    for unit in sorted(UNIT_BASINS):
        basin = UNIT_BASINS[unit]
        taken = intervals.get(basin, [])
        placed, k = 0, 0
        while placed < 10:
            s = _BASE_DATE + timedelta(days=10 + k * 7)
            e = s + timedelta(days=5)
            k += 1
            if any(s < ev_end and e > ev_start
                   for ev_start, ev_end in taken):
                continue
            placed += 1
            ws = datetime(s.year, s.month, s.day, tzinfo=timezone.utc)
            ctl = ControlWindowV0(
                control_id=f"ctl-{i:03d}",
                unit_id=unit,
                window_start=_iso(ws),
                window_end=_iso(ws + timedelta(days=5)),
                opportunity_id=f"opp-ctl-{i:03d}",
                opportunity_state="OBSERVED_FULL",
                state="NEGATIVE",
                matched_covariates=("basin", "season"))
            assert not ctl.problems(), ctl.problems()
            controls.append(ctl)
            i += 1
    return controls


def make_opportunities() -> dict:
    """The verified opportunity registry backing make_controls —
    one OBSERVED_FULL record per control, unit/window/state aligned."""
    out = {}
    for ctl in make_controls():
        opp = ObservationOpportunityV0(
            opportunity_id=ctl.opportunity_id,
            unit_id=ctl.unit_id,
            platform="SYNTHETIC",
            window_start=ctl.window_start,
            window_end=ctl.window_end,
            coverage_fraction=1.0,
            coverage_quality="synthetic-complete",
            detection_threshold="synthetic",
            state="OBSERVED_FULL",
            source_id="synthetic_inventory_v0",
            source_as_of="2020-08-01",
            frame_ids=(f"frame-{ctl.opportunity_id}",))
        assert not opp.problems(), opp.problems()
        out[opp.opportunity_id] = opp
    return out


def make_holdout(events: Sequence[EventLabelV0],
                 extra_assignments: dict[str, str] | None = None,
                 **kw: Any) -> HoldoutPlanV0:
    """A contract-valid HoldoutPlanV0: every event maps to its basin's
    locked test region; ghost entries cover train/validation groups."""
    assignments = {e.event_id: _BASIN_REGION[e.basin_id]
                   for e in events}
    assignments.setdefault("ghost-train-0", "north_train")
    assignments.setdefault("ghost-val-0", "central_val")
    if extra_assignments:
        assignments.update(extra_assignments)
    return HoldoutPlanV0(
        holdout_plan_id="holdout-syn-b4",
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


_REGIME_IDS = {"R0": 0, "R1": 1, "R2": 2,
             PLANTED_REGIME: 3, "R_RARE": 4}


def planted_artifact_payload(events: Sequence[EventLabelV0]) -> dict:
    """A serialized frozen producer-shaped regime artifact — real
    canonical digests, int regime ids — consistent with the planted
    synthetic partition.  Replay must adapt it through
    ``regime_assignment_from_artifact``; the payload exists so the
    replay boundary is exercised, not bypassed."""
    rows: dict[tuple[str, str], int] = {}
    for unit in sorted(UNIT_BASINS):
        for d in _dates_between(_BASE_DATE, _ASSIGN_END):
            doy = d.timetuple().tm_yday
            if doy % 37 == 0:
                rows[(unit, d.isoformat())] = _REGIME_IDS[
                    PLANTED_REGIME]
            elif doy % 53 == 0:
                rows[(unit, d.isoformat())] = _REGIME_IDS["R_RARE"]
            else:
                rows[(unit, d.isoformat())] = int(
                    _sha(f"base|{unit}|{d.isoformat()}")[:8], 16) % 3
    basin_units: dict[str, list[str]] = {}
    for u, b in UNIT_BASINS.items():
        basin_units.setdefault(b, []).append(u)
    for ev in events:
        s = datetime.strptime(ev.event_time_start,
                              "%Y-%m-%dT%H:%M:%SZ").date()
        e = datetime.strptime(ev.event_time_end,
                              "%Y-%m-%dT%H:%M:%SZ").date()
        for d in _dates_between(s, e):
            for u in basin_units[ev.basin_id]:
                rows[(u, d.isoformat())] = _REGIME_IDS[PLANTED_REGIME]
    assignments = sorted(
        (u, d, r) for (u, d), r in rows.items())
    art = {
        "mode": "RETROSPECTIVE_REGIME",
        "data_class": "REANALYSIS",
        "fitted_on": "TRAIN_ONLY",
        "label_blinding": True,
        "k": 5,
        "seeds": [11, 23, 42],
        "assignments": assignments,
        "assignment_digest": sha256_canonical(assignments),
        "feature_cols": ["synth_f1", "synth_f2"],
        "feature_matrix_digest": _sha("synthetic-fmx-b4"),
        "input_bytes_digest": _sha("synthetic-input-bytes-b4"),
        "config_digest": _sha("synthetic-config-b4"),
        "fit_groups": sorted({b.split("_")[0] for b in
                              set(UNIT_BASINS.values())}),
        "heldout_groups_declared": ["gandaki_eval", "karnali_eval",
                                    "koshi_eval"],
        "train_mask_digest": _sha("synthetic-mask-b4"),
        "status": "CANDIDATE_ONLY",
        "disclaimer": "synthetic fixture — interface evidence only",
    }
    art["regime_artifact_digest"] = sha256_canonical(art)
    art["freeze_digest"] = sha256_canonical(dict(art))
    art["frozen"] = True
    return art


def planted_artifact(events: Sequence[EventLabelV0]
                     ) -> RegimeAssignmentArtifact:
    """The contract artifact derived from the frozen producer payload
    through the canonical adapter — identical to what replay
    reconstructs."""
    from nepal.experiment_v0.adapters import (
        regime_assignment_from_artifact)
    return regime_assignment_from_artifact(
        planted_artifact_payload(events), artifact_id="regime-syn-b4")


# ---------------------------------------------------------------------
# Forecast lane
# ---------------------------------------------------------------------

FORECAST_REGIONS = ("region_east", "region_west")
FORECAST_SEASONS = ("season_a", "season_b")


def synthetic_forecast_holdout() -> HoldoutPlanV0:
    """A locked HoldoutPlanV0 whose test groups are the two named
    evaluation regions."""
    groups = ("train_basin_a", "validation_basin_b") + FORECAST_REGIONS
    return HoldoutPlanV0(
        holdout_plan_id="holdout-syn-b4-forecast",
        assignment_rule="basin",
        train_groups=("train_basin_a",),
        validation_groups=("validation_basin_b",),
        test_groups=FORECAST_REGIONS,
        event_assignments={f"event-{g}": g for g in groups},
        evaluation_region_names=FORECAST_REGIONS,
        assigned_before_filtering=True,
        test_locked=True,
        embargo_seconds=2592000.0)


def synthetic_vintage_requests(
        regions: Sequence[str] = FORECAST_REGIONS
        ) -> list[dict[str, Any]]:
    """One admissible TIGGE-shaped VintageRequest payload per region
    (issue + 48 h archive satisfies the registry floor exactly)."""
    requests = []
    for region in regions:
        payload = synthetic_vintage_request(
            provider="tigge",
            model_version=f"synthetic-model-{region}",
            archive_payload_sha256=_sha(f"payload:{region}"),
            retrieval_record_sha256=_sha(f"retrieval:{region}"),
            archive_payload_path=f"evidence/tigge/{region}/payload.bin",
            retrieval_record_path=
            f"evidence/tigge/{region}/retrieval.json")
        requests.append(VintageRequest(**payload).to_dict())
    return requests


def admitted_vintage_for(request: dict[str, Any]):
    """The ForecastVintageV0 the replay path admits for ``request``."""
    req = VintageRequest.from_dict(request)
    return build_vintage(req, replay_vintage_id(req))


def synthetic_forecast_cases(
        regions: Sequence[str] = FORECAST_REGIONS,
        seasons: Sequence[str] = FORECAST_SEASONS,
        per_cell: int = 4) -> list[ForecastCase]:
    """Scored cases bound to the replay-admitted vintage digests.

    All cases use the 6h horizon and sit exactly inside the vintage's
    declared valid window; ``lead_seconds == valid_start - issue``.
    """
    requests = synthetic_vintage_requests(regions)
    vintage_of = {r: admitted_vintage_for(req)
                  for r, req in zip(regions, requests)}
    digest_of = {r: sha256_canonical(v.to_dict())
                 for r, v in vintage_of.items()}
    cases: list[ForecastCase] = []
    i = 0
    for region in regions:
        v = vintage_of[region]
        lead = (datetime.strptime(v.valid_start, "%Y-%m-%dT%H:%M:%SZ")
                - datetime.strptime(v.issue_time,
                                    "%Y-%m-%dT%H:%M:%SZ")
                ).total_seconds()
        for season in seasons:
            for k in range(per_cell):
                positive = (i % 3 == 0)
                cases.append(ForecastCase(
                    case_id=f"case-{i:05d}",
                    unit_id=f"{region}-unit-{k % 2}",
                    region=region,
                    season=season,
                    mechanism="snow_release",
                    issue_time=v.issue_time,
                    valid_start=v.valid_start,
                    valid_end=v.valid_end,
                    horizon="6h",
                    lead_seconds=lead,
                    y_prob=0.70 if positive else 0.30,
                    y_state="POSITIVE" if positive else "NEGATIVE",
                    vintage_digest=digest_of[region],
                    opportunity_id=f"synth-opp-{i:05d}",
                    outcome_source_id=f"synth-outcome-{region}",
                    cutoff_time=v.issue_time,
                    features={"synth_precip": 5.0 + 0.1 * i,
                              "synth_temp": -1.0 - 0.05 * i}))
                i += 1
    return cases


def synthetic_baseline_probs(
        cases: Sequence[ForecastCase]) -> dict[str, list[float]]:
    """All four mandatory baseline vectors, aligned one-per-case."""
    n = len(cases)
    rate = (sum(1 for c in cases if c.y_state == "POSITIVE") / n
            if n else 0.0)
    return {
        "climatology": [rate] * n,
        "rule": [0.75 if c.features.get("synth_precip", 0.0) >= 5.2
                 else 0.25 for c in cases],
        "null": [rate] * n,
        "regularized_supervised": [
            0.65 if c.y_state == "POSITIVE" else 0.35
            for c in cases],
    }


# ---------------------------------------------------------------------
# Bundle assembly
# ---------------------------------------------------------------------

def synthetic_bundle(n_events: int = 21, n_boot: int = 32,
                     seed: int = 7) -> dict[str, Any]:
    """A complete, contract-valid ``experiment_v0.replay_bundle/v0``
    mapping — JSON-serializable throughout."""
    events = make_events(n_events)
    artifact = planted_artifact(events)
    controls = make_controls()
    a_holdout = make_holdout(events)
    f_holdout = synthetic_forecast_holdout()
    cases = synthetic_forecast_cases()
    return {
        "schema": "experiment_v0.replay_bundle/v0",
        "association": {
            "artifact_id": artifact.artifact_id,
            "artifact_payload": planted_artifact_payload(events),
            "artifact": artifact.to_dict(),
            "events": [e.to_dict() for e in events],
            "controls": [c.to_dict() for c in controls],
            "opportunities": {oid: o.to_dict()
                              for oid, o in make_opportunities()
                              .items()},
            "unit_basins": dict(UNIT_BASINS),
            "holdout": a_holdout.to_dict(),
            "region_basins": {k: list(v)
                              for k, v in REGION_BASINS.items()},
            "n_boot": n_boot,
            "seed": seed,
        },
        "forecast": {
            "vintage_requests": synthetic_vintage_requests(),
            "cases": [c.to_dict() for c in cases],
            "holdout": f_holdout.to_dict(),
            "baseline_probs": synthetic_baseline_probs(cases),
            "n_opportunities": len(cases),
            "n_boot": n_boot,
            "seed": seed,
        },
    }


__all__ = [
    "BASINS", "FORECAST_REGIONS", "FORECAST_SEASONS", "PLANTED_REGIME",
    "REGION_BASINS", "UNIT_BASINS",
    "admitted_vintage_for", "make_controls", "make_event",
    "make_opportunities",
    "make_events", "make_holdout", "planted_artifact",
    "planted_artifact_payload",
    "synthetic_baseline_probs", "synthetic_bundle",
    "synthetic_forecast_cases", "synthetic_forecast_holdout",
    "synthetic_vintage_requests",
]
