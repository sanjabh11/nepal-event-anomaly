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
import math
from datetime import date as _date
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

import numpy as np

from nepal.experiment_v0.audit import replay_vintage_id
from nepal.experiment_v0.association import RegimeAssignmentArtifact
from nepal.experiment_v0.evaluation import ForecastCase
from nepal.experiment_v0.vintages import VintageRequest, build_vintage
from nepal.research_v0._hashing import sha256_canonical
from nepal.research_v0.producer_validation import (
    canonical_unit_basin_pairs, row_key,
    semantic_feature_matrix_digest, sorted_row_key_digest)
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


#: The planted fit partition: koshi + gandaki rows are the fit
#: surface, karnali rows are the declared holdout — the
#: fit/heldout split the serialized payload must recompute.
FIT_GROUPS = ("gandaki", "koshi")
HELDOUT_GROUPS = ("karnali",)


def _null_family(family: str, seeds, stats) -> dict:
    """A producer-shaped serialized null-family record whose
    ``family_digest`` is computed exactly as ``run_regimes`` binds
    it: sha256_canonical over the documented material dict."""
    replicates = [
        {"i": i, "gen_seed": int(seeds[0]) + 1000003 * (i + 1),
         "fit_seed": int(seeds[i % len(seeds)]),
         "k": 5, "stat": s, "ok": True,
         "input_digest": _sha(f"{family}-rep-{i}")}
        for i, s in enumerate(stats)]
    rec = {"statistic": "silhouette", "observed": 0.6,
           "n_replicates": len(replicates),
           "n_succeeded": len(replicates), "n_failed": 0,
           "p_value": 0.02, "alpha": 0.05, "status": "PASS",
           "reason": None,
           "selection": "bic_sweep_declared_candidates",
           "null_k_distribution": {"5": len(replicates)},
           "null_stat_min": min(stats), "null_stat_max": max(stats),
           "replicates": replicates}
    rec["family_digest"] = sha256_canonical({
        "family": family, "seed_cycle": list(seeds),
        "n_replicates": rec["n_replicates"],
        "statistic": rec["statistic"], "p_value": rec["p_value"],
        "observed": rec.get("observed"),
        "alpha": rec.get("alpha"),
        "n_succeeded": rec.get("n_succeeded"),
        "n_failed": rec.get("n_failed"),
        "status": rec.get("status"),
        "reason": rec.get("reason"),
        "selection": rec.get("selection"),
        "null_stat_min": rec["null_stat_min"],
        "null_stat_max": rec["null_stat_max"],
        "null_k_distribution": rec["null_k_distribution"],
        "replicates": rec["replicates"]})
    return rec


def planted_artifact_payload(events: Sequence[EventLabelV0]) -> dict:
    """A serialized frozen producer-shaped regime artifact — real
    canonical digests, int regime ids — consistent with the planted
    synthetic partition.  Replay must adapt it through
    ``regime_assignment_from_artifact``; the payload exists so the
    replay boundary is exercised, not bypassed.

    R9: every bound section the shared floor recomputes is REAL here
    — input_values decode to the bound bytes, the feature-matrix and
    row-key digests recompute over the assignment universe, the
    run_manifest is a canonical RunManifestV0 serialization, and the
    config block mirrors the flat declared fields."""
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
    n_rows = len(assignments)
    feature_cols = ["synth_f1", "synth_f2"]
    seeds = [11, 23, 42]
    unit_basin_map = sorted((u, b) for u, b in UNIT_BASINS.items())
    group_of = dict(unit_basin_map)
    train_keys = [row_key(u, d) for u, d, _ in assignments
                  if group_of[u] in FIT_GROUPS]
    n_train_rows = len(train_keys)
    row_universe_digest = sorted_row_key_digest(
        [row_key(u, d) for u, d, _ in assignments])
    # A small deterministic synthetic feature matrix — one encoded
    # row per frame row, honest float64 bytes + semantic digest.
    input_values = [
        [round(math.sin(0.31 * i + c) + 0.001 * i * (c + 1), 6)
         for c in range(len(feature_cols))]
        for i in range(n_rows)]
    input_bytes_digest = hashlib.sha256(
        np.ascontiguousarray(
            np.asarray(input_values, dtype=np.float64))
        .tobytes()).hexdigest()
    feature_matrix_digest = semantic_feature_matrix_digest(
        input_values)
    env_digest = _sha("synthetic-env-b4")
    assignment_digest = sha256_canonical(assignments)
    # The canonical producer schema: exact input-byte binding, the
    # serialized input schema, the fitted model parameters, the
    # declared seed set with per-seed coverage, and the flat
    # required-gates map inside ``stability`` — everything a replay
    # needs to verify the artifact without re-predicting.
    required_gates = {
        "seed_policy": True,
        "modal_k_unanimous": True,
        "seed_coverage": True,
        "seed_ari": True,
        "loro": True,
        "temporal_bootstrap": True,
        "season_refits": True,
        "elevation": True,
        "missingness": True,
        "era_drift": True,
        "shuffled_null": True,
        "season_matched_null": True,
        "effort": True,
    }
    stability = {
        "seed_ari_min": 0.91,
        "seed_ari_max": 0.97,
        "modal_k_frequency": 1.0,
        "k_instability": False,
        "n_bootstrap": 200,
        "required_gates": required_gates,
    }
    config = {
        "seeds": list(seeds), "k_candidates": [1, 2, 3, 4, 5],
        "null_alpha": 0.05, "cadence": "1D",
        "gap_policy": "calendar", "bootstrap_block_len": 7,
        "missingness_policy": "listwise",
        "effort_split": "median",
        "mode": "RETROSPECTIVE_REGIME",
        "train_groups": list(FIT_GROUPS),
        "heldout_groups": list(HELDOUT_GROUPS),
        "forecast_feature_set": [],
        "forecast_vintage_digests": [],
        "source_manifest": {"fixture": True},
    }
    k1_bic = [1010.5, 1020.25, 1030.75]
    nulls = {"statistic": "silhouette", "observed": 0.6,
             "alpha": 0.05, "n_replicates": 4,
             "season_era_stratified": False,
             "k1_bic": k1_bic,
             "shuffled": _null_family(
                 "shuffled", seeds, [0.10, 0.20, 0.15, 0.05]),
             "season_matched": _null_family(
                 "season_matched", seeds,
                 [0.30, 0.25, 0.20, 0.10])}
    run_manifest = {
        "record_type": "RunManifestV0",
        "run_id": "synthetic-b4-run-001",
        "worker_id": "synthetic-fixture",
        "created_at": "2020-12-15T00:00:00Z",
        "environment_digest": env_digest,
        "seed": seeds[0],
        "input_digests": [input_bytes_digest],
        "output_digests": [assignment_digest],
        "checkpoint_policy": "atomic_publish_or_quarantine",
        "status": "COMPLETED"}
    art = {
        "mode": "RETROSPECTIVE_REGIME",
        "data_class": "REANALYSIS",
        "fitted_on": "TRAIN_ONLY",
        "label_blinding": True,
        "k": 5,
        "seeds": list(seeds),
        "seeds_declared": list(seeds),
        "seed_coverage": {"11": "converged", "23": "converged",
                          "42": "converged"},
        "per_seed_best_k": {"11": 5, "23": 5, "42": 5},
        "modal_k_frequency": 1.0,
        "occupancy": [0.40, 0.25, 0.15, 0.12, 0.08],
        "assignments": assignments,
        "assignment_digest": assignment_digest,
        "feature_cols": feature_cols,
        "feature_matrix_digest": feature_matrix_digest,
        "input_values": input_values,
        "input_bytes_digest": input_bytes_digest,
        "input_schema": {
            "feature_cols": list(feature_cols),
            "n_rows": n_rows,
            "dtypes": {c: "float64" for c in feature_cols},
            "shape": [n_rows, len(feature_cols)],
        },
        "model": {
            "weights": [0.40, 0.25, 0.15, 0.12, 0.08],
            "means": [[4.0, -3.0], [3.2, -2.4], [0.0, 0.0],
                      [-3.0, 4.0], [-4.0, 3.0]],
            "covariances": [
                [[0.16, 0.0], [0.0, 0.16]] for _ in range(5)],
        },
        "config": config,
        "config_digest": sha256_canonical(config),
        "fit_groups": sorted(FIT_GROUPS),
        "heldout_groups_declared": sorted(HELDOUT_GROUPS),
        # R8-C09: the typed fit-partition record — agrees with the
        # flat fit/heldout declarations above; its digest is bound
        # into the envelope below.
        "fit_partition": {
            "record_type": "fit_partition/v0",
            "train_groups": sorted(FIT_GROUPS),
            "heldout_groups": sorted(HELDOUT_GROUPS),
            "n_train_rows": n_train_rows,
            "n_rows": n_rows,
            "train_row_keys_digest": sorted_row_key_digest(
                train_keys),
            "cutoff_iso": "2020-12-15",
            "feature_matrix_digest": feature_matrix_digest,
            "feature_cols": list(feature_cols)},
        # C03: the unit -> basin partition, bound into the artifact.
        "unit_basin_map": unit_basin_map,
        "unit_basin_map_digest": sha256_canonical(
            canonical_unit_basin_pairs(unit_basin_map)),
        # C15: the typed run-manifest record (digest bound below).
        "run_manifest": run_manifest,
        "n_train_rows": n_train_rows,
        "n_rows": n_rows,
        "train_mask_digest": _sha("synthetic-mask-b4"),
        "stability": stability,
        "nulls": nulls,
        "preprocessing_digest": None,  # bound below over the section
        "k_selection_digest": _sha("synthetic-ksel-b4"),
        "stability_report_digest": sha256_canonical(stability),
        "null_model_digest": sha256_canonical({
            "k1_bic": k1_bic,
            "null_families": {
                "shuffled": nulls["shuffled"]["family_digest"],
                "season_matched":
                    nulls["season_matched"]["family_digest"]}}),
        "status": "DESCRIPTIVE_REGIME_ONLY",
        "terminal": True,
        "associable": True,
        "source_manifest": {"fixture": True},
        "missingness_applied": {"policy": "listwise",
                                "train_rows_total": n_train_rows,
                                "train_rows_fitted": n_train_rows,
                                "train_rows_dropped": 0},
        "preprocessing": {
            "imputer_strategy": "median",
            "imputer_statistics": [0.0, 0.0],
            "scaler_mean": [0.0, 0.0],
            "scaler_var": [1.0, 1.0],
            "feature_order": list(feature_cols),
            "row_keys_digest": row_universe_digest,
            "train_mask_membership_digest": _sha(
                "synthetic-maskmembers-b4")},
        "environment_digest": env_digest,
        "run_manifest_digest": None,  # bound below over the record
        "disclaimer": "synthetic fixture — interface evidence only",
    }
    art["preprocessing_digest"] = sha256_canonical(
        art["preprocessing"])
    art["fit_partition_digest"] = sha256_canonical(
        art["fit_partition"])
    art["run_manifest_digest"] = sha256_canonical(
        art["run_manifest"])
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


def planted_artifact_and_payload(
        events: Sequence[EventLabelV0]
        ) -> tuple[RegimeAssignmentArtifact, dict]:
    """The ``(artifact, producer_payload)`` verified-binding pair —
    PROV-04 (R6): ``run_association`` reaches the supported verdict
    only when the serialized frozen producer payload rides along and
    verifies; this helper keeps fixture-built artifacts on the
    verified path."""
    payload = planted_artifact_payload(events)
    from nepal.experiment_v0.adapters import (
        regime_assignment_from_artifact)
    artifact = regime_assignment_from_artifact(
        payload, artifact_id="regime-syn-b4")
    return artifact, payload


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
            "opportunities": {
                oid: o.to_dict() for oid, o in
                forecast_opportunities(cases).items()},
            "unit_basins": forecast_unit_basins(cases),
            "region_basins": {k: list(v) for k, v in
                              forecast_region_basins(cases).items()},
            "n_boot": n_boot,
            "seed": seed,
        },
    }


def forecast_opportunities(
        cases: Sequence[ForecastCase]
        ) -> dict[str, ObservationOpportunityV0]:
    """The verified opportunity registry backing the forecast
    evaluation — one OBSERVED_FULL record per case opportunity, on
    the case's own unit and valid window."""
    out: dict[str, ObservationOpportunityV0] = {}
    for c in cases:
        out[c.opportunity_id] = ObservationOpportunityV0(
            opportunity_id=c.opportunity_id,
            unit_id=c.unit_id,
            platform="SYNTHETIC",
            window_start=c.valid_start,
            window_end=c.valid_end,
            coverage_fraction=1.0,
            coverage_quality="synthetic-complete",
            detection_threshold="synthetic",
            state="OBSERVED_FULL",
            source_id="synthetic_forecast_archive_v0",
            source_as_of="2021-02-01",
            frame_ids=(f"frame-{c.opportunity_id}-a",))
    return out


def forecast_unit_basins(
        cases: Sequence[ForecastCase]) -> dict[str, str]:
    """unit_id -> basin for the forecast scope: one synthetic basin
    per evaluation region."""
    return {c.unit_id: f"{c.region}-basin" for c in cases}


def forecast_region_basins(
        cases: Sequence[ForecastCase]) -> dict[str, tuple[str, ...]]:
    """region -> basins — keys equal the holdout's declared
    evaluation regions exactly."""
    regions = sorted({c.region for c in cases})
    return {r: (f"{r}-basin",) for r in regions}


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
