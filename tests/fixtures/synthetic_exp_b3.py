"""Deterministic synthetic fixtures for the B3 evaluation tests.

Planted-signal case generators for the forecast-evaluation scaffold:
predicted probabilities are generated from a hidden logistic signal so
the candidate scorer provably separates POSITIVE from NEGATIVE cases,
while ``null`` stays at the marginal rate.  Each evaluation region is
bound to one synthetic ``ForecastVintageV0`` whose metadata is
consistent with every case it backs (shared issue_time, contained
valid window, lead = valid_start - issue, window width = horizon).

Everything is a pure function of ``seed`` — no wall clock, no
network, no real data.  No value here corresponds to any observed
event, location, or measurement.
"""
from __future__ import annotations

import hashlib
import math
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from nepal.experiment_v0.baselines import (
    FitPartition, ThresholdRule, climatology_probs,
    fit_climatology_rates, fit_marginal_rate,
    fit_regularized_supervised, null_probs, rule_probs)
from nepal.experiment_v0.evaluation import ForecastCase
from nepal.research_v0._hashing import sha256_canonical
from nepal.research_v0.policy import HORIZON_SECONDS
from nepal.research_v0.records import (ForecastVintageV0,
                                      HoldoutPlanV0,
                                      ObservationOpportunityV0)

_BASE = datetime(2021, 1, 1, tzinfo=timezone.utc)
LEAD_SECONDS = 21600.0          # fixed issue -> valid_start lead (6 h)
_MAX_HORIZON = max(HORIZON_SECONDS.values())


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


def synthetic_holdout(
        test_groups: Sequence[str] = ("region_east", "region_west"),
        *, test_locked: bool = True) -> HoldoutPlanV0:
    """A HoldoutPlanV0 whose test split is the named evaluation
    regions; groups are basin-assigned, embargoed, and (by default)
    locked."""
    groups = tuple(test_groups)
    all_groups = ("train_basin_a", "validation_basin_b") + groups
    return HoldoutPlanV0(
        holdout_plan_id="holdout-synth-b3",
        assignment_rule="basin",
        train_groups=("train_basin_a",),
        validation_groups=("validation_basin_b",),
        test_groups=groups,
        event_assignments={f"event-{g}": g for g in all_groups},
        evaluation_region_names=groups,
        assigned_before_filtering=True,
        test_locked=test_locked,
        embargo_seconds=2592000.0)


def synthetic_vintage(region: str,
                      issue_dt: datetime = _BASE) -> ForecastVintageV0:
    """One synthetic ARCHIVED_OPERATIONAL vintage covering every
    horizon from ``issue + LEAD_SECONDS`` out to the widest policy
    horizon."""
    issue_s = _iso(issue_dt)
    return ForecastVintageV0(
        vintage_id=f"vintage-{region}",
        provider="synthetic_archive",
        data_class="ARCHIVED_OPERATIONAL",
        initialization_time=_iso(issue_dt - timedelta(hours=6)),
        issue_time=issue_s,
        valid_start=_iso(issue_dt + timedelta(seconds=LEAD_SECONDS)),
        valid_end=_iso(issue_dt + timedelta(
            seconds=LEAD_SECONDS + _MAX_HORIZON)),
        archive_availability=_iso(issue_dt + timedelta(hours=48)),
        archive_payload_sha256=_sha(f"payload:{region}"),
        retrieval_record_sha256=_sha(f"retrieval:{region}"),
        archive_payload_path=f"vintages/{region}/payload.bin",
        retrieval_record_path=f"vintages/{region}/retrieval.json",
        model_version="synthetic-model-v0",
        license_id="synthetic-licence",
        archive_mechanism="synthetic-portal")


def vintage_digest(vintage: ForecastVintageV0) -> str:
    """The admitted-set key: canonical digest of the vintage record."""
    return sha256_canonical(vintage.to_dict())


def admitted_vintages(
        regions: Sequence[str],
        issue_dt: datetime = _BASE) -> dict[str, ForecastVintageV0]:
    """digest -> ForecastVintageV0 map for the given regions."""
    out: dict[str, ForecastVintageV0] = {}
    for region in regions:
        v = synthetic_vintage(region, issue_dt)
        out[vintage_digest(v)] = v
    return out


def admitted_for_cases(
        cases: Sequence[ForecastCase],
        issue_dt: datetime = _BASE) -> dict[str, ForecastVintageV0]:
    return admitted_vintages(sorted({c.region for c in cases}),
                             issue_dt)


def planted_cases(
        seed: int = 0, *,
        regions: Sequence[str] = ("region_east", "region_west"),
        seasons: Sequence[str] = ("season_a", "season_b"),
        mechanisms: Sequence[str] = ("snow_release",),
        horizons: Sequence[str] = ("24h", "48h", "7d"),
        per_cluster: int = 6,
        censored_every: int = 0,
        signal: float = 1.8,
        base: float = -0.6,
        issue_dt: datetime = _BASE,
        event_groups: bool = True) -> list[ForecastCase]:
    """Planted-signal synthetic cases over (region, season, mechanism)
    clusters.

    ``y_prob`` is the generating logistic probability plus tiny uniform
    noise, so the model ordering tracks the true rate.  ``features``
    carries ``synth_precip`` (signal-correlated) and ``synth_temp`` so
    the transparent rule baseline has something to threshold.  Every
    ``censored_every``-th case (when > 0) is CENSORED_OR_AMBIGUOUS.

    When ``event_groups`` is true every case carries the atomic event
    group of its (region, season, mechanism) cluster — the power
    report clusters on it; when false ``event_group_id`` is empty and
    clustering falls back to (unit_id, season) cells.

    All cases in a region share the region's vintage issue_time;
    ``valid_start = issue + LEAD_SECONDS`` and
    ``valid_end - valid_start = HORIZON_SECONDS[horizon]``.
    """
    rng = random.Random(int(seed))
    vintages = admitted_vintages(regions, issue_dt)
    cases: list[ForecastCase] = []
    i = 0
    for region in regions:
        digest = next(d for d, v in vintages.items()
                      if v.vintage_id == f"vintage-{region}")
        for season in seasons:
            for mechanism in mechanisms:
                group = f"ev-{region}-{season}-{mechanism}" \
                    if event_groups else ""
                for k in range(per_cluster):
                    s = rng.gauss(0.0, 1.0)
                    p_true = _sigmoid(base + signal * s)
                    positive = rng.random() < p_true
                    y_prob = min(1.0, max(
                        0.0, p_true + rng.uniform(-0.02, 0.02)))
                    horizon = horizons[i % len(horizons)]
                    vs = issue_dt + timedelta(seconds=LEAD_SECONDS)
                    ve = vs + timedelta(seconds=HORIZON_SECONDS[horizon])
                    if censored_every and i % censored_every == \
                            censored_every - 1:
                        y_state = "CENSORED_OR_AMBIGUOUS"
                    elif positive:
                        y_state = "POSITIVE"
                    else:
                        y_state = "NEGATIVE"
                    cases.append(ForecastCase(
                        case_id=f"case-{i:05d}",
                        unit_id=f"{region}-unit-{k % 3}",
                        region=region,
                        season=season,
                        mechanism=mechanism,
                        issue_time=_iso(issue_dt),
                        valid_start=_iso(vs),
                        valid_end=_iso(ve),
                        horizon=horizon,
                        lead_seconds=LEAD_SECONDS,
                        y_prob=y_prob,
                        y_state=y_state,
                        vintage_digest=digest,
                        opportunity_id=f"synth-opp-{i:05d}",
                        outcome_source_id=
                        f"synth-outcome-{region}",
                        cutoff_time=_iso(issue_dt),
                        event_group_id=group,
                        features={
                            "synth_precip": 4.0 + 3.0 * s
                            + rng.gauss(0.0, 0.1),
                            "synth_temp": -2.0 + s,
                        }))
                    i += 1
    return cases


def unambiguous_rate(cases: Sequence[ForecastCase]) -> float:
    sub = [c for c in cases if c.y_state != "CENSORED_OR_AMBIGUOUS"]
    if not sub:
        return 0.0
    return sum(1 for c in sub if c.y_state == "POSITIVE") / len(sub)


def seasons_of(cases: Sequence[ForecastCase]) -> tuple[str, ...]:
    return tuple(sorted({c.season for c in cases}))


def mechanisms_of(cases: Sequence[ForecastCase]) -> tuple[str, ...]:
    return tuple(sorted({c.mechanism for c in cases}))


def synthetic_opportunity(
        opportunity_id: str, unit_id: str,
        window_start: str, window_end: str) -> ObservationOpportunityV0:
    """One problem-free OBSERVED_FULL opportunity record covering a
    case's valid window on the case's own unit."""
    return ObservationOpportunityV0(
        opportunity_id=opportunity_id,
        unit_id=unit_id,
        platform="synthetic_platform",
        window_start=window_start,
        window_end=window_end,
        coverage_fraction=1.0,
        coverage_quality="synthetic-complete",
        detection_threshold="synthetic",
        state="OBSERVED_FULL",
        source_id="synthetic_inventory_v0",
        source_as_of="2021-02-01",
        frame_ids=(f"frame-{opportunity_id}-a",
                   f"frame-{opportunity_id}-b"))


def opportunity_registry(
        cases: Sequence[ForecastCase]
        ) -> dict[str, ObservationOpportunityV0]:
    """opportunity_id -> verified opportunity record: one per case,
    bound to the case's unit and valid window."""
    return {c.opportunity_id: synthetic_opportunity(
                c.opportunity_id, c.unit_id,
                c.valid_start, c.valid_end)
            for c in cases}


def degraded_opportunity(
        opportunity_id: str, unit_id: str,
        window_start: str, window_end: str,
        *, state: str = "OBSERVED_PARTIAL") -> ObservationOpportunityV0:
    """A structurally valid registry entry that is NOT OBSERVED_FULL —
    problem-free, in scope, but never a verified denominator member.

    ``OBSERVED_PARTIAL`` carries in-(0,1) coverage and observed
    frames; ``UNOBSERVED`` carries zero coverage and no frames;
    ``UNKNOWN`` carries no coverage claim.
    """
    if state == "OBSERVED_PARTIAL":
        coverage, frames = 0.4, (f"frame-{opportunity_id}-a",)
        source_id, source_as_of = "synthetic_inventory_v0", \
            "2021-02-01"
    elif state == "UNOBSERVED":
        coverage, frames = 0.0, ()
        source_id, source_as_of = "", ""
    else:  # UNKNOWN
        coverage, frames = None, ()
        source_id, source_as_of = "", ""
    return ObservationOpportunityV0(
        opportunity_id=opportunity_id,
        unit_id=unit_id,
        platform="synthetic_platform",
        window_start=window_start,
        window_end=window_end,
        coverage_fraction=coverage,
        coverage_quality="synthetic-partial",
        detection_threshold="synthetic",
        state=state,
        source_id=source_id,
        source_as_of=source_as_of,
        frame_ids=frames)


def defective_opportunity(
        opportunity_id: str, unit_id: str,
        window_start: str, window_end: str) -> ObservationOpportunityV0:
    """An OBSERVED_FULL-claimed record with no frame binding — its
    ``problems()`` is non-empty, so it is excluded from every
    denominator and counted as a censored opportunity."""
    return ObservationOpportunityV0(
        opportunity_id=opportunity_id,
        unit_id=unit_id,
        platform="synthetic_platform",
        window_start=window_start,
        window_end=window_end,
        coverage_fraction=1.0,
        coverage_quality="synthetic-complete",
        detection_threshold="synthetic",
        state="OBSERVED_FULL",
        source_id="synthetic_inventory_v0",
        source_as_of="2021-02-01",
        frame_ids=())          # OBSERVED_FULL requires real frames


def unlinked_opportunities(
        units: Sequence[str], n: int = 2,
        prefix: str = "synth-opp-unlinked",
        ) -> dict[str, ObservationOpportunityV0]:
    """Extra verified OBSERVED_FULL opportunities bound to no case —
    evidence that denominators come from the registry, not case
    counts."""
    out: dict[str, ObservationOpportunityV0] = {}
    i = 0
    for unit in units:
        for j in range(n):
            oid = f"{prefix}-{i:04d}"
            out[oid] = synthetic_opportunity(
                oid, unit, "2021-01-01T06:00:00Z",
                "2021-01-02T06:00:00Z")
            i += 1
    return out


def unit_basins_for(cases: Sequence[ForecastCase]) -> dict[str, str]:
    """unit_id -> basin: one synthetic basin per case region, so a
    case's region is exactly the basin group its unit belongs to."""
    return {c.unit_id: f"{c.region}_basin" for c in cases}


def region_basins_for(
        regions: Sequence[str]) -> dict[str, tuple[str, ...]]:
    """evaluation-region name -> declared basin set."""
    return {r: (f"{r}_basin",) for r in regions}


def train_partition(cases: Sequence[ForecastCase], *,
                    seed: int = 0,
                    bound: bool = False) -> FitPartition:
    """A declared TRAIN_ONLY FitPartition from a disjoint synthetic
    training stream — never the evaluation cases themselves.

    ``bound=True`` attaches synthetic holdout/train-group provenance
    (holdout digest, train groups, feature digest, cutoff) so the
    partition binds provenance rather than reading as an unbound
    fixture surface."""
    train = planted_cases(seed + 7919, regions=("train_basin_a",),
                          seasons=seasons_of(cases),
                          mechanisms=mechanisms_of(cases),
                          per_cluster=8)
    train_sub = [c for c in train
                 if c.y_state != "CENSORED_OR_AMBIGUOUS"]
    provenance = {}
    if bound:
        provenance = {
            "holdout_digest": _sha(f"holdout:{seed}"),
            "train_groups": ("train_basin_a",),
            "feature_digest": _sha(f"features:{seed}"),
            "cutoff_time": "2020-12-31T00:00:00Z",
        }
    return FitPartition(
        partition="TRAIN_ONLY",
        rows=[c.features for c in train_sub],
        labels=[1 if c.y_state == "POSITIVE" else 0
                for c in train_sub],
        cell_keys=[(c.unit_id, c.season) for c in train_sub],
        **provenance)


def feature_row_keys_for(
        cases: Sequence[ForecastCase],
        *, extra: Sequence[str] = ()) -> tuple[str, ...]:
    """The canonical ``"unit_id|date"`` feature-row universe covering
    ``cases`` — one key per (unit, valid-start date) pair, plus any
    ``extra`` caller-supplied keys (e.g. train rows).  Deterministic
    and duplicate-free; matches the ``FitPartition.row_keys`` format.
    """
    keys = {f"{c.unit_id}|{c.valid_start[:10]}" for c in cases}
    keys.update(str(k) for k in extra)
    return tuple(sorted(keys))


def experiment_declaration(
        cases: Sequence[ForecastCase], *,
        declaration_id: str = "decl-synth-b3",
        threshold: float = 0.5,
        forecast_regime_digest: str = "",
        feature_row_keys: Any = None) -> dict:
    """A valid experiment-declaration mapping for ``cases``: every
    required key present, vintage lineage covering the cases'
    admitted digests, and the threshold record bound to
    ``threshold``.

    ``forecast_regime_digest`` (when non-empty) emits the optional
    EVAL-03 regime binding; ``feature_row_keys`` (when given) emits
    ``feature_row_keys_digest`` as ``sha256_canonical`` over the
    sorted key list — the same recompute ``evaluate`` performs.
    """
    payload = {
        "declaration_id": declaration_id,
        "feature_artifact_digest": _sha("feature-artifact"),
        "threshold_record": {"threshold": threshold,
                             "record_id": "thr-synth-b3"},
        "ablations": ["ablation-no-rule"],
        "vintage_lineage": sorted({c.vintage_digest for c in cases}),
    }
    if forecast_regime_digest:
        payload["forecast_regime_digest"] = forecast_regime_digest
    if feature_row_keys is not None:
        payload["feature_row_keys_digest"] = sha256_canonical(
            sorted(str(k) for k in feature_row_keys))
    return payload


def make_baseline_probs(
        cases: Sequence[ForecastCase], *,
        seed: int = 0) -> dict[str, list[float]]:
    """Compute all four mandatory baseline probability vectors for
    ``cases`` — aligned by position, one entry per case.

    Every fitted baseline (climatology rates, null base rate, the
    regularized supervised model) is fit on a declared TRAIN_ONLY
    partition drawn from a disjoint synthetic training stream — the
    scored cases' labels never enter a fit.  ``rule`` is predeclared
    and fits nothing.
    """
    partition = train_partition(cases, seed=seed)

    climatology = climatology_probs(
        cases, fit_climatology_rates(partition))

    rules = (ThresholdRule(feature="synth_precip", threshold=4.0,
                           low_prob=0.05, high_prob=0.85),)
    rule = rule_probs(cases, rules)

    null = null_probs(len(cases), fit_marginal_rate(partition))

    predict = fit_regularized_supervised(partition, seed=seed)
    supervised = predict([c.features for c in cases])

    return {"climatology": climatology, "rule": rule, "null": null,
            "regularized_supervised": supervised}


def powered_design() -> dict[str, Any]:
    """A design that clears the default precision floor: 10 regions x
    5 seasons x 2 mechanisms = 100 independent clusters."""
    regions = tuple(f"test_region_{i:02d}" for i in range(10))
    seasons = tuple(f"season_{i}" for i in range(5))
    mechanisms = ("snow_release", "lake_outburst")
    holdout = synthetic_holdout(test_groups=regions)
    cases = planted_cases(
        seed=11, regions=regions, seasons=seasons,
        mechanisms=mechanisms, horizons=("24h", "48h", "72h", "7d"),
        per_cluster=2, censored_every=17)
    return {"holdout": holdout, "cases": cases,
            "opportunities": opportunity_registry(cases),
            "unit_basins": unit_basins_for(cases),
            "region_basins": region_basins_for(regions)}


def underpowered_design() -> dict[str, Any]:
    """A minimal design far below the precision floor."""
    regions = ("region_east", "region_west")
    holdout = synthetic_holdout(test_groups=regions)
    cases = planted_cases(seed=5, regions=regions,
                          seasons=("season_a", "season_b"),
                          mechanisms=("snow_release",),
                          horizons=("24h", "48h"), per_cluster=3,
                          censored_every=0)
    return {"holdout": holdout, "cases": cases,
            "opportunities": opportunity_registry(cases),
            "unit_basins": unit_basins_for(cases),
            "region_basins": region_basins_for(regions)}
