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
    ThresholdRule, climatology_probs, fit_regularized_supervised,
    null_probs, rule_probs)
from nepal.experiment_v0.evaluation import ForecastCase
from nepal.research_v0._hashing import sha256_canonical
from nepal.research_v0.policy import HORIZON_SECONDS
from nepal.research_v0.records import (ForecastVintageV0,
                                      HoldoutPlanV0)

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
        evaluation_region_names=groups[:2] if len(groups) >= 2
        else groups,
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
        issue_dt: datetime = _BASE) -> list[ForecastCase]:
    """Planted-signal synthetic cases over (region, season, mechanism)
    clusters.

    ``y_prob`` is the generating logistic probability plus tiny uniform
    noise, so the model ordering tracks the true rate.  ``features``
    carries ``synth_precip`` (signal-correlated) and ``synth_temp`` so
    the transparent rule baseline has something to threshold.  Every
    ``censored_every``-th case (when > 0) is CENSORED_OR_AMBIGUOUS.

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


def make_baseline_probs(
        cases: Sequence[ForecastCase], *,
        seed: int = 0) -> dict[str, list[float]]:
    """Compute all four mandatory baseline probability vectors for
    ``cases`` — aligned by position, one entry per case."""
    sub = [c for c in cases if c.y_state != "CENSORED_OR_AMBIGUOUS"]

    rates: dict[tuple[str, str], float] = {}
    cell: dict[tuple[str, str], list[int]] = {}
    for c in sub:
        cell.setdefault((c.unit_id, c.season), []).append(
            1 if c.y_state == "POSITIVE" else 0)
    for key, vals in cell.items():
        rates[key] = sum(vals) / len(vals)
    climatology = climatology_probs(cases, rates)

    rules = (ThresholdRule(feature="synth_precip", threshold=4.0,
                           low_prob=0.05, high_prob=0.85),)
    rule = rule_probs(cases, rules)

    null = null_probs(len(cases), unambiguous_rate(cases))

    # Fit the regularized baseline on a disjoint synthetic training
    # stream (train region) — never on the evaluation cases.
    train = planted_cases(seed + 7919, regions=("train_basin_a",),
                          seasons=seasons_of(cases),
                          mechanisms=mechanisms_of(cases),
                          per_cluster=8)
    train_sub = [c for c in train
                 if c.y_state != "CENSORED_OR_AMBIGUOUS"]
    predict = fit_regularized_supervised(
        [c.features for c in train_sub],
        [1 if c.y_state == "POSITIVE" else 0 for c in train_sub],
        seed=seed)
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
    return {"holdout": holdout, "cases": cases}


def underpowered_design() -> dict[str, Any]:
    """A minimal design far below the precision floor."""
    regions = ("region_east", "region_west")
    holdout = synthetic_holdout(test_groups=regions)
    cases = planted_cases(seed=5, regions=regions,
                          seasons=("season_a", "season_b"),
                          mechanisms=("snow_release",),
                          horizons=("24h", "48h"), per_cluster=3,
                          censored_every=0)
    return {"holdout": holdout, "cases": cases}
