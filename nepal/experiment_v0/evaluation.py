"""Forecast-evaluation scaffold for the v0 research lane.

Implements ``docs/science/run_c/FORECAST_EVAL_SCAFFOLD_V0.md`` as a
fail-closed, deterministic, synthetic-only evaluator:

* ``evaluate`` validates the whole contract up front — locked-region
  guard, admitted vintage digests *plus* vintage-metadata timing
  (issue equality, valid-window containment, lead/horizon width
  consistency), admissible horizons, three-valued targets, per-case
  lineage identifiers (verified-opportunity id, outcome-source id,
  issue-time feature cutoff), the mandatory baseline set, aligned
  probability vectors, and a verified-opportunity denominator
  *derived from the opportunity registry* — and raises one
  ``ValueError`` listing every problem.  Cases are canonically
  ordered before hashing so the report digest is invariant under
  input reordering.
* Censored (``CENSORED_OR_AMBIGUOUS``) cases are excluded from every
  metric numerator and counted in ``n_censored``; the false-alarm
  denominator is the count of registry opportunities inside the
  declared evaluation scope, never the unambiguous-case count and
  never a caller-chosen integer.
* Metrics are recomputed per region / season / mechanism slice and per
  lead-time (horizon) bucket.
* ``power_report`` derives the effective sample size as the count of
  independent (region, season, mechanism) clusters — windows are never
  counted as independent — and gates the neutral status.
* ``missing_feed_degradation`` drops cases under declared scenarios and
  recomputes — degradation, never imputation.
* ``uncertainty_report`` runs a seeded (region, season) block
  bootstrap and reports percentile intervals per metric.

This module scores no real forecasts and emits only neutral research
statuses (``FORECAST_EXPERIMENT_ONLY`` /
``UNDERPOWERED_DESCRIPTIVE_ONLY``).  Nothing here is a real-data
finding or an authorization of any kind.
"""
from __future__ import annotations

import hashlib
import math
import random
from dataclasses import MISSING as _MISSING
from dataclasses import asdict, dataclass, field, fields
from statistics import NormalDist
from typing import Any, Collection, Mapping, Optional, Sequence

from nepal.research_v0._hashing import sha256_canonical
from nepal.research_v0.policy import (HORIZON_SECONDS, TargetState,
                                      parse_strict_utc,
                                      require_finite_seconds)
from nepal.research_v0.records import (ForecastVintageV0,
                                      HoldoutPlanV0,
                                      ObservationOpportunityV0)

from . import metrics as _metrics
from .baselines import REQUIRED_BASELINE_NAMES

_CENSORED = TargetState.CENSORED_OR_AMBIGUOUS.value
_TARGET_VALUES = frozenset(s.value for s in TargetState)
_TIME_EPS = 1e-6

_STR_FIELDS = frozenset({
    "case_id", "unit_id", "region", "season", "mechanism",
    "issue_time", "valid_start", "valid_end", "horizon", "y_state",
    "vintage_digest", "opportunity_id", "outcome_source_id",
    "cutoff_time"})
_NUM_FIELDS = frozenset({"lead_seconds", "y_prob"})
_CASE_TAG = "ForecastCase"


@dataclass(frozen=True)
class ForecastCase:
    """One scored case in an evaluation: a predicted probability bound
    to an admitted vintage digest, a locked test region, and a
    three-valued target state."""

    case_id: str
    unit_id: str
    region: str
    season: str
    mechanism: str
    issue_time: str
    valid_start: str
    valid_end: str
    horizon: str
    lead_seconds: float
    y_prob: float
    y_state: str                     # TargetState value
    vintage_digest: str
    opportunity_id: str = ""         # verified-opportunity lineage
    outcome_source_id: str = ""      # target/outcome source lineage
    cutoff_time: str = ""            # feature-availability cutoff
    features: Mapping[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["features"] = dict(self.features)
        d["case_type"] = _CASE_TAG
        return d

    @classmethod
    def from_dict(cls, d: Mapping) -> "ForecastCase":
        """Strict reconstruction — fail-closed, never coercive.

        Rejects non-mapping payloads, unknown fields, a mismatched
        ``case_type`` tag, missing required fields, and wrong primitive
        types (strings stay strings — ``"false"`` never becomes a
        boolean; numeric fields reject strings, booleans, and
        non-finite values).  ``features`` must be a mapping of
        ``str -> finite number``.
        """
        if not isinstance(d, Mapping):
            raise ValueError("ForecastCase payload must be a mapping")
        declared = {f.name for f in fields(cls)}
        tag = d.get("case_type")
        if tag is not None and tag != _CASE_TAG:
            raise ValueError(
                f"case_type {tag!r} is not {_CASE_TAG!r}")
        extra = set(d) - declared - {"case_type"}
        if extra:
            raise ValueError(
                f"ForecastCase: unknown fields {sorted(extra)}")
        required = {f.name for f in fields(cls)
                    if f.default is _MISSING
                    and f.default_factory is _MISSING}
        missing = required - set(d)
        if missing:
            raise ValueError(
                f"ForecastCase: missing required fields "
                f"{sorted(missing)}")
        kwargs: dict[str, Any] = {}
        for name, value in d.items():
            if name == "case_type":
                continue
            if name in _STR_FIELDS:
                if not isinstance(value, str):
                    raise ValueError(
                        f"ForecastCase: {name} must be a string, got "
                        f"{type(value).__name__}")
                if name == "y_state" and value not in _TARGET_VALUES:
                    raise ValueError(
                        f"ForecastCase: y_state {value!r} is not a "
                        f"TargetState value {sorted(_TARGET_VALUES)} — "
                        "truthy strings like 'yes'/'true'/'1' never "
                        "coerce")
                kwargs[name] = value
            elif name in _NUM_FIELDS:
                if isinstance(value, bool) or \
                        not isinstance(value, (int, float)) or \
                        not math.isfinite(float(value)):
                    raise ValueError(
                        f"ForecastCase: {name} must be a finite "
                        f"number, got {value!r}")
                kwargs[name] = float(value)
            elif name == "features":
                if not isinstance(value, Mapping):
                    raise ValueError(
                        "ForecastCase: features must be a mapping of "
                        "str to finite number")
                feats: dict[str, float] = {}
                for fk, fv in value.items():
                    if not isinstance(fk, str):
                        raise ValueError(
                            "ForecastCase: feature names must be "
                            f"strings, got {fk!r}")
                    if isinstance(fv, bool) or \
                            not isinstance(fv, (int, float)) or \
                            not math.isfinite(float(fv)):
                        raise ValueError(
                            f"ForecastCase: feature {fk!r} must be a "
                            f"finite number, got {fv!r}")
                    feats[fk] = float(fv)
                kwargs[name] = feats
            else:  # pragma: no cover - unknown keys rejected above
                kwargs[name] = value
        return cls(**kwargs)


def locked_region_problems(regions: Collection[str],
                           holdout: HoldoutPlanV0) -> list[str]:
    """Locked-test-region guard.

    Evaluation regions must be non-empty strings drawn from the
    holdout's locked test groups, there must be at least two distinct
    regions (single-box evaluations reject), and the holdout's test
    split must be locked.
    """
    problems: list[str] = []
    regs = list(regions)
    if any(not isinstance(r, str) or not r.strip() for r in regs):
        problems.append("every evaluation region must be a non-empty "
                        "string")
    distinct = {r for r in regs if isinstance(r, str)}
    if not getattr(holdout, "test_locked", False):
        problems.append("holdout test groups are not locked — nothing "
                        "may be tuned on them and evaluation on an "
                        "unlocked split is inadmissible")
    unmapped = distinct - set(getattr(holdout, "test_groups", ()))
    if unmapped:
        problems.append(
            f"evaluation regions not drawn from locked test groups: "
            f"{sorted(unmapped)}")
    if len(distinct) < 2:
        problems.append("at least two distinct evaluation regions are "
                        "required — single-region evaluations reject")
    return problems


def _finite_prob(value: Any) -> bool:
    p = require_finite_seconds(value)
    return p is not None and 0.0 <= p <= 1.0


def _unambiguous(cases: Sequence[ForecastCase]) -> list[ForecastCase]:
    return [c for c in cases if c.y_state != _CENSORED]


def _y(cases: Sequence[ForecastCase]) -> list[int]:
    return [1 if c.y_state == TargetState.POSITIVE.value else 0
            for c in cases]


def _bundle(y: list[int], p: list[float], *,
            threshold: float,
            n_opportunities: int) -> dict:
    """One scorer's full metric bundle on the unambiguous subset.

    ``n_opportunities`` is the verified-opportunity denominator for
    the false-alarm rate — derived from the opportunity registry by
    ``evaluate``, never inferred from the unambiguous count.
    """
    flags = [1 if pi >= threshold else 0 for pi in p]
    pr = _metrics.pr_curve(y, p)
    return {
        "n": len(y),
        "n_positive": int(sum(y)),
        "n_opportunities": n_opportunities,
        "brier": _metrics.brier_score(y, p),
        "calibration": {
            "bins": _metrics.calibration_bins(y, p),
            "fit": _metrics.calibration_fit(y, p),
        },
        "precision_recall": pr,
        "event_recall": _metrics.event_recall(y, flags),
        "false_alarms_per_opportunity":
            _metrics.false_alarms_per_opportunity(
                flags, n_opportunities),
    }


def _core_metrics(y: list[int], p: list[float], *,
                  threshold: float,
                  n_opportunities: int) -> dict:
    """Compact metric set used in degradation/uncertainty paths."""
    flags = [1 if pi >= threshold else 0 for pi in p]
    return {
        "n": len(y),
        "n_opportunities": n_opportunities,
        "brier": _metrics.brier_score(y, p),
        "auprc": _metrics.pr_curve(y, p)["auprc"],
        "event_recall": _metrics.event_recall(y, flags),
        "false_alarms_per_opportunity":
            _metrics.false_alarms_per_opportunity(
                flags, n_opportunities),
    }


def _slice_bundle(cases: Sequence[ForecastCase],
                  probs: Mapping[str, Sequence[float]],
                  idx: Sequence[int], *,
                  threshold: float) -> dict:
    """Metric bundles for one slice.  The slice's opportunity
    denominator is the slice's own case count — the smallest verified
    bound, since every case is one opportunity."""
    pos = [i for i in idx if cases[i].y_state != _CENSORED]
    sub = [cases[i] for i in pos]
    y = _y(sub)
    scorers: dict[str, Any] = {}
    for name, full in probs.items():
        scorers[name] = _bundle(y, [full[i] for i in pos],
                                threshold=threshold,
                                n_opportunities=len(idx))
    return {"n_cases": len(idx),
            "n_censored": len(idx) - len(pos),
            "scorers": scorers}


def _group_indices(cases: Sequence[ForecastCase],
                   key) -> dict[str, list[int]]:
    groups: dict[str, list[int]] = {}
    for i, c in enumerate(cases):
        groups.setdefault(str(key(c)), []).append(i)
    return groups


def _fraction_dropped(case_id: str, fraction: float) -> bool:
    """Deterministic fractional dropout: a case is dropped when the
    leading bits of its id hash fall below ``fraction`` — spread across
    units, stable across runs, no imputation."""
    if fraction <= 0.0:
        return False
    if fraction >= 1.0:
        return True
    digest = hashlib.sha256(case_id.encode("utf-8")).hexdigest()
    return int(digest[:12], 16) / float(16 ** 12) < fraction


def missing_feed_degradation(
        cases: Sequence[ForecastCase],
        scenarios: Sequence[Mapping]) -> dict:
    """Recompute core metrics under predeclared missing-feed scenarios.

    Each scenario is ``{"name": str}`` plus ``drop_units`` (an iterable
    of unit ids removed entirely) and/or ``drop_fraction`` (a
    deterministic fractional dropout by case-id hash).  Surviving
    cases are re-scored as-is — cases are degraded away, never
    imputed.  Each scenario's opportunity denominator is its surviving
    case count (every case is one opportunity).  A scenario that
    empties the unambiguous subset is flagged ``degenerate``.
    """
    cases = list(cases)
    full = _unambiguous(cases)
    y_full = _y(full)
    p_full = [c.y_prob for c in full]
    reference = _core_metrics(y_full, p_full, threshold=0.5,
                              n_opportunities=len(cases))
    out_scenarios: list[dict] = []
    for scenario in scenarios:
        name = str(scenario.get("name", "scenario"))
        drop_units = set(scenario.get("drop_units") or ())
        fraction = float(scenario.get("drop_fraction") or 0.0)
        survivors = [c for c in cases
                     if c.unit_id not in drop_units
                     and not _fraction_dropped(c.case_id, fraction)]
        sub = _unambiguous(survivors)
        metrics = _core_metrics(_y(sub), [c.y_prob for c in sub],
                                threshold=0.5,
                                n_opportunities=len(survivors))
        delta = {k: metrics[k] - reference[k]
                 for k in ("brier", "auprc", "event_recall",
                           "false_alarms_per_opportunity")}
        out_scenarios.append({
            "name": name,
            "drop_units": sorted(str(u) for u in drop_units),
            "drop_fraction": fraction,
            "n_cases": len(survivors),
            "n_dropped": len(cases) - len(survivors),
            "n_unambiguous": len(sub),
            "degenerate": len(sub) == 0,
            "metrics": metrics,
            "delta": delta,
        })
    return {"policy": "degradation_recompute_never_imputation",
            "reference_metrics": reference,
            "scenarios": out_scenarios}


def power_report(cases: Sequence[ForecastCase], *,
                 target_precision: float = 0.1,
                 alpha: float = 0.05) -> dict:
    """Prospective precision/power computation on the declared design.

    The effective sample size is the number of independent
    ``(region, season, mechanism)`` clusters over the unambiguous
    subset — individual windows are never counted as independent.
    ``required_n`` is the cluster count needed for a worst-case
    (rate = 0.5) proportion to meet ``target_precision`` at the
    two-sided ``alpha`` level.  ``powered`` requires the effective
    size to meet the requirement and at least two clusters.
    """
    cases = list(cases)
    sub = _unambiguous(cases)
    clusters = {(c.region, c.season, c.mechanism) for c in sub}
    n_clusters = len(clusters)
    observed_rate = (sum(1 for c in sub
                         if c.y_state == TargetState.POSITIVE.value)
                     / len(sub)) if sub else 0.0
    z = NormalDist().inv_cdf(1.0 - alpha / 2.0) if 0.0 < alpha < 1.0 \
        else 0.0
    if target_precision > 0.0:
        required_n = math.ceil(
            z * z * 0.25 / (target_precision * target_precision))
    else:
        required_n = -1  # degenerate target: never powered
    powered = (bool(sub) and required_n > 0 and n_clusters >= 2
               and n_clusters >= required_n)
    return {
        "n_cases": len(cases),
        "n_unambiguous": len(sub),
        "n_censored": len(cases) - len(sub),
        "n_clusters": n_clusters,
        "effective_n": n_clusters,
        "cluster_key": "region+season+mechanism",
        "observed_rate": observed_rate,
        "target_precision": target_precision,
        "alpha": alpha,
        "z": z,
        "required_n": required_n,
        "powered": powered,
    }


def _percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1,
              max(0, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[idx]


def uncertainty_report(cases: Sequence[ForecastCase],
                       baseline_probs: Mapping[str, Sequence[float]],
                       *, n_boot: int = 200, seed: int = 0) -> dict:
    """Seeded (region, season) block bootstrap intervals.

    Clusters of *all* cases (censored included — a case is one
    opportunity) are resampled with replacement ``n_boot`` times; each
    replicate recomputes the core metrics on the resampled unambiguous
    subset with the resampled case count as the opportunity
    denominator.  Percentile intervals (alpha = 0.05) are reported per
    scorer and metric.  The stream is ``random.Random(seed)``: same
    inputs give identical intervals.
    """
    cases = list(cases)
    clusters: dict[tuple[str, str], list[int]] = {}
    for i, c in enumerate(cases):
        clusters.setdefault((c.region, c.season), []).append(i)
    cluster_keys = sorted(clusters)
    rng = random.Random(int(seed))
    scorers: dict[str, Sequence[float]] = {
        "model": [c.y_prob for c in cases]}
    for name, probs in baseline_probs.items():
        scorers[str(name)] = list(probs)

    idx_un = [i for i, c in enumerate(cases) if c.y_state != _CENSORED]
    out: dict[str, Any] = {}
    for name, probs in scorers.items():
        y_all = _y([cases[i] for i in idx_un])
        p_all = [probs[i] for i in idx_un]
        point = _core_metrics(y_all, p_all, threshold=0.5,
                              n_opportunities=len(cases))
        replicates: dict[str, list[float]] = {
            k: [] for k in ("brier", "auprc", "event_recall",
                            "false_alarms_per_opportunity")}
        for _ in range(max(0, int(n_boot))):
            if not cluster_keys:
                break
            draw = rng.choices(cluster_keys, k=len(cluster_keys))
            idx = [i for key in draw for i in clusters[key]]
            sub = [i for i in idx if cases[i].y_state != _CENSORED]
            y = _y([cases[i] for i in sub])
            p = [probs[i] for i in sub]
            m = _core_metrics(y, p, threshold=0.5,
                              n_opportunities=len(idx))
            for k in replicates:
                replicates[k].append(m[k])
        metric_ci: dict[str, Any] = {}
        for k, vals in replicates.items():
            vals_sorted = sorted(vals)
            metric_ci[k] = {
                "point": point[k],
                "ci_low": _percentile(vals_sorted, 0.025),
                "ci_high": _percentile(vals_sorted, 0.975),
                "n_replicates": len(vals),
            }
        out[name] = metric_ci
    return {
        "method": "region_season_block_bootstrap",
        "cluster_key": "region+season",
        "n_boot": int(n_boot),
        "seed": int(seed),
        "n_clusters": len(cluster_keys),
        "scorers": out,
    }


@dataclass(frozen=True)
class EvaluationReport:
    """The full descriptive evaluation artifact.

    ``status`` is ``FORECAST_EXPERIMENT_ONLY`` when the declared design
    meets its precision floor, else ``UNDERPOWERED_DESCRIPTIVE_ONLY`` —
    both neutral research statuses; neither is a real-data finding.
    """

    experiment_id: str
    n_cases: int
    n_censored: int
    metrics: dict       # "model" + each baseline name -> metric bundle
    slices: dict        # per region / season / mechanism
    lead_time: dict     # per-horizon metric bundles
    degradation: dict   # missing-feed scenario recomputation
    power: dict
    uncertainty: dict   # block-bootstrap intervals
    status: str
    claim_scope: str = "research_only_no_operational_authorization"

    def to_dict(self) -> dict:
        return asdict(self)


def _case_vintage_problems(c: ForecastCase,
                           admitted: Mapping[str, Any]) -> list[str]:
    """Vintage-metadata timing checks for one case (scaffold §5.4).

    The case's digest must resolve to an admitted ``ForecastVintageV0``
    and the case's timing must be consistent with that vintage:
    ``issue_time`` equality, ``issue <= valid_start <= valid_end``,
    containment inside the vintage valid window, ``lead_seconds ==
    valid_start - issue_time``, and ``valid_end - valid_start ==
    HORIZON_SECONDS[horizon]``.
    """
    tag = f"case {c.case_id!r}"
    problems: list[str] = []
    if c.horizon not in HORIZON_SECONDS:
        problems.append(
            f"{tag}: horizon {c.horizon!r} is not an admissible "
            f"policy horizon {sorted(HORIZON_SECONDS)}")
    if c.y_state not in _TARGET_VALUES:
        problems.append(
            f"{tag}: y_state {c.y_state!r} is not a TargetState "
            f"value {sorted(_TARGET_VALUES)}")
    if not _finite_prob(c.y_prob):
        problems.append(
            f"{tag}: y_prob {c.y_prob!r} is not finite in [0,1]")
    lead = require_finite_seconds(c.lead_seconds)
    if lead is None or lead < 0:
        problems.append(
            f"{tag}: lead_seconds {c.lead_seconds!r} is not a "
            "finite non-negative value")

    vintage = admitted.get(c.vintage_digest)
    if vintage is None:
        problems.append(
            f"{tag}: vintage_digest is not in the admitted vintage "
            "set")
        return problems
    if type(vintage) is not ForecastVintageV0:
        problems.append(
            f"{tag}: admitted vintage entry has type "
            f"{type(vintage).__name__!r} — not a ForecastVintageV0")
        return problems

    issue = parse_strict_utc(c.issue_time)
    vs = parse_strict_utc(c.valid_start)
    ve = parse_strict_utc(c.valid_end)
    v_issue = parse_strict_utc(vintage.issue_time)
    v_vs = parse_strict_utc(vintage.valid_start)
    v_ve = parse_strict_utc(vintage.valid_end)

    if issue is None:
        problems.append(
            f"{tag}: issue_time {c.issue_time!r} is not an "
            "explicit-UTC timestamp")
    elif v_issue is None or issue != v_issue:
        problems.append(
            f"{tag}: issue_time does not equal the admitted "
            "vintage's issue_time")
    if vs is None:
        problems.append(
            f"{tag}: valid_start {c.valid_start!r} is not an "
            "explicit-UTC timestamp")
    if ve is None:
        problems.append(
            f"{tag}: valid_end {c.valid_end!r} is not an "
            "explicit-UTC timestamp")

    if issue is not None and vs is not None and vs < issue:
        problems.append(
            f"{tag}: valid_start precedes issue_time — a scored "
            "window cannot open before the forecast was issued")
    if vs is not None and ve is not None and ve < vs:
        problems.append(
            f"{tag}: valid_end precedes valid_start — inverted "
            "valid window")
    if vs is not None and v_vs is not None and vs < v_vs:
        problems.append(
            f"{tag}: valid_start precedes the admitted vintage's "
            "valid_start — outside the vintage window")
    if ve is not None and v_ve is not None and ve > v_ve:
        problems.append(
            f"{tag}: valid_end exceeds the admitted vintage's "
            "valid_end — outside the vintage window")
    if None not in (issue, vs) and lead is not None and lead >= 0 \
            and abs(lead - (vs - issue)) > _TIME_EPS:
        problems.append(
            f"{tag}: lead_seconds ({lead}) does not equal "
            f"valid_start - issue_time ({vs - issue})")
    if c.horizon in HORIZON_SECONDS and None not in (vs, ve) \
            and ve is not None and vs is not None and ve >= vs:
        width = ve - vs
        if abs(width - HORIZON_SECONDS[c.horizon]) > _TIME_EPS:
            problems.append(
                f"{tag}: valid window width ({width} s) does not "
                f"equal horizon {c.horizon!r} "
                f"({HORIZON_SECONDS[c.horizon]} s)")
    return problems


def _case_lineage_problems(c: ForecastCase) -> list[str]:
    """Per-case lineage checks: every scored case must carry the
    verified observation opportunity its target window was verified
    under, the outcome-source lineage id, and an explicit-UTC
    ``cutoff_time`` no later than ``issue_time`` — every feature is
    required to be available at issue time."""
    tag = f"case {c.case_id!r}"
    problems: list[str] = []
    if not isinstance(c.opportunity_id, str) or \
            not c.opportunity_id.strip():
        problems.append(
            f"{tag}: opportunity_id is required — the case must "
            "name the verified observation opportunity its target "
            "window was verified under")
    if not isinstance(c.outcome_source_id, str) or \
            not c.outcome_source_id.strip():
        problems.append(
            f"{tag}: outcome_source_id is required — the case must "
            "name the source lineage of its target state")
    cutoff = parse_strict_utc(c.cutoff_time)
    if cutoff is None:
        problems.append(
            f"{tag}: cutoff_time {c.cutoff_time!r} is not an "
            "explicit-UTC timestamp")
    else:
        issue = parse_strict_utc(c.issue_time)
        if issue is not None and cutoff > issue + _TIME_EPS:
            problems.append(
                f"{tag}: cutoff_time postdates issue_time — "
                "features must be available at issue time")
    return problems


def _region_basin_problems(holdout: Any, region_basins: Any,
                           ) -> tuple[list[str], dict[str, str]]:
    """Validate the evaluation-scope map and return
    ``(problems, basin_owner)`` where ``basin_owner`` binds each basin
    to the evaluation region that declares it.

    ``region_basins`` must be a mapping whose keys equal
    ``holdout.evaluation_region_names`` exactly; every region must map
    to a non-empty collection of basin names and no basin may be
    claimed by two regions.  This mirrors the ``run_association``
    holdout binding — a case's ``region`` is an evaluation-region
    name, and a case's verified opportunity must sit on a unit whose
    basin is claimed by that region.
    """
    problems: list[str] = []
    basin_owner: dict[str, str] = {}
    if not isinstance(region_basins, Mapping):
        return ["region_basins must be a mapping of "
                "evaluation-region name -> basins"], basin_owner
    named = set(getattr(holdout, "evaluation_region_names", ()) or ())
    if set(region_basins.keys()) != named:
        problems.append(
            "region_basins keys must equal "
            "holdout.evaluation_region_names exactly "
            f"(region_basins={sorted(map(str, region_basins))}, "
            f"evaluation_region_names={sorted(map(str, named))})")
    for key in sorted(region_basins, key=str):
        name = str(key)
        basins = region_basins[key]
        if isinstance(basins, (str, bytes)) or \
                not isinstance(basins, Collection) or not basins:
            problems.append(f"evaluation region {name!r} must map to "
                            "a non-empty collection of basin names")
            continue
        for basin in basins:
            if not isinstance(basin, str) or not basin.strip():
                problems.append(f"evaluation region {name!r}: basin "
                                "names must be non-empty strings")
                continue
            if basin in basin_owner and basin_owner[basin] != name:
                problems.append(
                    f"basin {basin!r} is claimed by both regions "
                    f"{basin_owner[basin]!r} and {name!r}")
            else:
                basin_owner[basin] = name
    return problems, basin_owner


def evaluate(cases: Sequence[ForecastCase], *,
             holdout: HoldoutPlanV0,
             baseline_probs: Mapping[str, Sequence[float]],
             admitted_vintages: Mapping[str, ForecastVintageV0],
             opportunities: Mapping[str, ObservationOpportunityV0],
             unit_basins: Mapping[str, str],
             region_basins: Mapping[str, Collection[str]],
             n_opportunities_declared: Optional[int] = None,
             threshold: float = 0.5,
             n_boot: int = 200,
             seed: int = 0) -> EvaluationReport:
    """Validate the contract and emit the full descriptive report.

    Raises ``ValueError`` listing *every* problem: unlocked or unmapped
    evaluation regions, unadmitted vintage digests, vintage-metadata
    timing violations (issue mismatch, post-issue or inverted windows,
    lead/horizon inconsistency), inadmissible horizons, invalid target
    states, empty per-case lineage identifiers (``opportunity_id``,
    ``outcome_source_id``, ``cutoff_time``) or a ``cutoff_time`` that
    postdates ``issue_time``, missing mandatory baselines, misaligned
    or out-of-range probability vectors, duplicate case ids, and any
    defect in the opportunity-registry binding.

    ``opportunities`` is the verified observation-opportunity registry
    (``opportunity_id`` -> ``ObservationOpportunityV0``) — the same
    registry type ``run_association`` consumes.  Every registry entry
    must be a problem-free record whose ``opportunity_id`` equals its
    map key and whose ``unit_id`` has a ``unit_basins`` mapping.
    Every case's ``opportunity_id`` must resolve to a registry entry
    on the case's own unit whose basin (via ``unit_basins``) is
    claimed — through ``region_basins`` — by the case's declared
    evaluation region.

    The false-alarm denominator ``n_opportunities`` is *derived* from
    the registry: the count of distinct registry opportunity ids whose
    unit's basin lies inside the declared evaluation regions.  It is
    never caller-chosen.  The optional ``n_opportunities_declared``
    is tamper evidence only — when given it must equal the derived
    count exactly.

    Censored cases are excluded from every metric numerator and
    counted in ``n_censored``; the false-alarm rate divides by the
    derived ``n_opportunities`` — never by the unambiguous count.
    Cases are canonically ordered by ``(case_id, opportunity_id)``
    before scoring and hashing, so reordering identical inputs yields
    an identical report and digest.
    """
    cases = list(cases)
    problems: list[str] = []
    # the holdout must BE a valid contract record — not merely expose
    # test_locked/test_groups attributes (type + full problems())
    if type(holdout) is not HoldoutPlanV0:
        problems.append("holdout must be a HoldoutPlanV0 record")
    else:
        problems.extend(f"holdout: {p}" for p in holdout.problems())
        named = set(holdout.evaluation_region_names)
        undeclared = {c.region for c in cases
                      if isinstance(c.region, str)} - named
        if undeclared:
            problems.append(
                f"case regions are not declared evaluation regions: "
                f"{sorted(undeclared)}")
    problems.extend(locked_region_problems(
        [c.region for c in cases], holdout))

    # every admitted vintage must be a problem-free record whose map
    # key recomputes to its canonical digest — the key is evidence,
    # not a label
    if not isinstance(admitted_vintages, Mapping):
        problems.append("admitted_vintages must be a mapping of "
                        "canonical vintage digest -> ForecastVintageV0")
    else:
        for key, v in admitted_vintages.items():
            tag = f"admitted vintage {key!r}"
            if type(v) is not ForecastVintageV0:
                problems.append(f"{tag}: not a ForecastVintageV0")
                continue
            problems.extend(f"{tag}: {pp}" for pp in v.problems())
            if sha256_canonical(v.to_dict()) != key:
                problems.append(
                    f"{tag}: key does not recompute to "
                    "sha256_canonical(vintage.to_dict()) — the "
                    "admission map is keyed by canonical digest")

    # ---- evaluation-scope maps: unit -> basin -> region ----------
    if not isinstance(unit_basins, Mapping):
        problems.append("unit_basins must be a mapping of unit_id -> "
                        "basin name")
        ub: Mapping[str, Any] = {}
    else:
        ub = unit_basins
        for u, b in unit_basins.items():
            if not isinstance(u, str) or not isinstance(b, str) or \
                    not b.strip():
                problems.append(
                    f"unit_basins entry {u!r} -> {b!r} must map a "
                    "string unit id to a non-empty string basin")
    rb_problems, basin_owner = _region_basin_problems(
        holdout, region_basins)
    problems.extend(rb_problems)
    eval_basins = set(basin_owner)

    # ---- opportunity registry: the denominator's source of truth --
    registry: Mapping[str, Any] = {}
    if not isinstance(opportunities, Mapping):
        problems.append(
            "opportunities must be a mapping of opportunity_id -> "
            "ObservationOpportunityV0 — the false-alarm denominator "
            "is registry-derived, never caller-chosen")
    else:
        registry = opportunities
        for key, rec in registry.items():
            tag = f"opportunity registry entry {key!r}"
            if type(rec) is not ObservationOpportunityV0:
                problems.append(f"{tag}: not an "
                                "ObservationOpportunityV0 record")
                continue
            if rec.opportunity_id != key:
                problems.append(
                    f"{tag}: record opportunity_id "
                    f"{rec.opportunity_id!r} does not equal the "
                    "registry key — the registry is keyed by "
                    "opportunity_id")
            problems.extend(f"{tag}: {p}" for p in rec.problems())
            basin = ub.get(rec.unit_id)
            if not isinstance(basin, str) or not basin.strip():
                problems.append(
                    f"{tag}: unit {rec.unit_id!r} has no basin "
                    "mapping in unit_basins")

    # The denominator: distinct registry opportunities whose unit's
    # basin lies inside the declared evaluation regions.
    n_opportunities = sum(
        1 for rec in registry.values()
        if type(rec) is ObservationOpportunityV0
        and ub.get(rec.unit_id) in eval_basins)
    if isinstance(opportunities, Mapping) and \
            isinstance(unit_basins, Mapping) and n_opportunities <= 0:
        problems.append(
            "the opportunity registry contains no verified "
            "opportunities inside the declared evaluation scope — "
            "the false-alarm denominator cannot be zero")
    if n_opportunities_declared is not None:
        if isinstance(n_opportunities_declared, bool) or \
                not isinstance(n_opportunities_declared, int):
            problems.append(
                f"n_opportunities_declared "
                f"{n_opportunities_declared!r} must be an integer "
                "equal to the registry-derived opportunity count")
        elif n_opportunities_declared != n_opportunities:
            problems.append(
                f"n_opportunities_declared "
                f"({n_opportunities_declared}) does not equal the "
                f"registry-derived in-scope opportunity count "
                f"({n_opportunities}) — the denominator is evidence, "
                "not a caller claim")

    seen_ids: set[str] = set()
    seen_opps: set[str] = set()
    for c in cases:
        if c.case_id in seen_ids:
            problems.append(f"case {c.case_id!r}: duplicate case_id")
        seen_ids.add(c.case_id)
        if c.opportunity_id and c.opportunity_id in seen_opps:
            problems.append(
                f"case {c.case_id!r}: opportunity_id "
                f"{c.opportunity_id!r} is shared by another case — "
                "every case must bind a distinct verified "
                "opportunity")
        seen_opps.add(c.opportunity_id)
        problems.extend(_case_lineage_problems(c))
        problems.extend(_case_vintage_problems(c, admitted_vintages))
        if isinstance(c.opportunity_id, str) and \
                c.opportunity_id.strip():
            opp = registry.get(c.opportunity_id)
            if c.opportunity_id not in registry:
                problems.append(
                    f"case {c.case_id!r}: opportunity_id "
                    f"{c.opportunity_id!r} is absent from the "
                    "opportunity registry")
            elif type(opp) is ObservationOpportunityV0:
                if opp.unit_id != c.unit_id:
                    problems.append(
                        f"case {c.case_id!r}: unit {c.unit_id!r} "
                        f"does not match opportunity "
                        f"{opp.opportunity_id!r} unit "
                        f"{opp.unit_id!r}")
                basin = ub.get(opp.unit_id)
                owner = basin_owner.get(basin) \
                    if isinstance(basin, str) else None
                if owner is None:
                    problems.append(
                        f"case {c.case_id!r}: opportunity "
                        f"{opp.opportunity_id!r} sits in basin "
                        f"{basin!r} outside the declared evaluation "
                        "regions")
                elif owner != c.region:
                    problems.append(
                        f"case {c.case_id!r}: opportunity basin "
                        f"{basin!r} belongs to evaluation region "
                        f"{owner!r}, not the case's declared region "
                        f"{c.region!r}")

    provided = {str(k) for k in baseline_probs}
    missing = REQUIRED_BASELINE_NAMES - provided
    if missing:
        problems.append(
            f"baseline_probs is missing mandatory baselines: "
            f"{sorted(missing)} (required: "
            f"{sorted(REQUIRED_BASELINE_NAMES)})")
    for name, probs in baseline_probs.items():
        probs_list = list(probs)
        if len(probs_list) != len(cases):
            problems.append(
                f"baseline {name!r}: {len(probs_list)} probabilities "
                f"for {len(cases)} cases — vectors must be aligned")
            continue
        if any(not _finite_prob(p) for p in probs_list):
            problems.append(
                f"baseline {name!r}: probabilities must be finite in "
                "[0,1]")
    if problems:
        raise ValueError("forecast evaluation rejected: "
                         + "; ".join(problems))

    # Canonical case order: everything downstream — metric bundles,
    # slices, aligned baseline vectors, and the experiment digest —
    # is computed over the stable (case_id, opportunity_id) ordering,
    # so a byte-identical reordering of the inputs yields identical
    # metrics and an identical digest.
    order = sorted(range(len(cases)),
                   key=lambda i: (cases[i].case_id,
                                  cases[i].opportunity_id))
    cases = [cases[i] for i in order]
    aligned_baselines = {
        str(name): [list(probs)[i] for i in order]
        for name, probs in baseline_probs.items()}

    sub = _unambiguous(cases)
    pos_idx = [i for i, c in enumerate(cases) if c.y_state != _CENSORED]
    y = _y(sub)

    scorers: dict[str, Sequence[float]] = {
        "model": [c.y_prob for c in cases]}
    scorers.update(aligned_baselines)

    metric_table: dict[str, Any] = {}
    for name, probs in scorers.items():
        metric_table[name] = _bundle(
            y, [probs[i] for i in pos_idx], threshold=threshold,
            n_opportunities=n_opportunities)

    slices = {
        axis: {value: _slice_bundle(cases, scorers, idx,
                                    threshold=threshold)
               for value, idx in sorted(
                   _group_indices(cases, key).items())}
        for axis, key in (("region", lambda c: c.region),
                          ("season", lambda c: c.season),
                          ("mechanism", lambda c: c.mechanism))
    }
    lead_time = {
        h: _slice_bundle(cases, scorers, idx, threshold=threshold)
        for h, idx in sorted(
            _group_indices(cases, lambda c: c.horizon).items(),
            key=lambda kv: HORIZON_SECONDS[kv[0]])
    }

    power = power_report(cases)
    degradation = missing_feed_degradation(cases, ())
    uncertainty = uncertainty_report(
        cases, aligned_baselines, n_boot=n_boot, seed=seed)
    status = ("FORECAST_EXPERIMENT_ONLY" if power["powered"]
              else "UNDERPOWERED_DESCRIPTIVE_ONLY")

    digest = sha256_canonical({
        "cases": [c.to_dict() for c in cases],
        "baselines": {k: list(v) for k, v in scorers.items()},
        "vintages": sorted(str(k) for k in admitted_vintages),
        "opportunity_ids": sorted(str(k) for k in registry),
        "n_opportunities": n_opportunities,
        "holdout": holdout.to_dict(),
    })
    experiment_id = f"eval-{digest[:16]}"

    return EvaluationReport(
        experiment_id=experiment_id,
        n_cases=len(cases),
        n_censored=len(cases) - len(sub),
        metrics=metric_table,
        slices=slices,
        lead_time=lead_time,
        degradation=degradation,
        power=power,
        uncertainty=uncertainty,
        status=status)


__all__ = [
    "ForecastCase", "EvaluationReport", "locked_region_problems",
    "evaluate", "power_report", "missing_feed_degradation",
    "uncertainty_report",
]
