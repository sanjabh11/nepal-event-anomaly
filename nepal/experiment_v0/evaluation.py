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
  metric numerator and counted in ``n_censored``; an unambiguous case
  whose linked opportunity is not a verified in-scope
  ``OBSERVED_FULL`` record is likewise censored — never scored — and
  counted in ``n_censored_for_opportunity_state`` /
  ``n_censored_out_of_scope``.  The false-alarm denominator is the
  count of *problem-free* ``OBSERVED_FULL`` registry opportunities
  inside the declared evaluation scope — partial, unknown, unobserved,
  and defective records are excluded and counted separately in
  ``n_censored_opportunities`` — never the unambiguous-case count and
  never a caller-chosen integer.
* Metrics are recomputed per region / season / mechanism slice and per
  lead-time (horizon) bucket; every slice denominator is derived by
  the shared ``_scoped_opportunity_count`` — case counts are never
  denominators.
* ``power_report`` derives the effective sample size as the count of
  independent clusters — an atomic ``event_group_id`` when the case
  carries one, else the ``(unit_id, season)`` basin-season cell —
  windows are never counted as independent — and gates the neutral
  status.
* ``missing_feed`` executes the declared missing-feed scenario set
  (provider / variable dropout executed as recomputations, member
  truncation and latency stress reported ``NOT_APPLICABLE`` where no
  ensemble or latency axis exists, missing-opportunity censoring) —
  degradation, never imputation.
* ``missing_feed_degradation`` drops cases under declared dropout
  scenarios and recomputes with registry-derived denominators.
* ``uncertainty_report`` runs a seeded (region, season) block
  bootstrap and reports percentile intervals per metric.
* ``experiment`` optionally binds a ``ForecastExperimentDeclaration``
  (or a convertible mapping); when absent the report records
  ``mode="fixture_only"`` — a standalone probability mapping can never
  claim more than fixture scope.

This module scores no real forecasts and emits only neutral research
statuses (``FORECAST_EXPERIMENT_ONLY`` /
``UNDERPOWERED_DESCRIPTIVE_ONLY``).  Nothing here is a real-data
finding or an authorization of any kind.
"""
from __future__ import annotations

import hashlib
import math
import random
import re
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
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

#: The declared missing-feed scenario vocabulary.  Every declared
#: scenario either recomputes (``EXECUTED``) or reports
#: ``NOT_APPLICABLE`` with a reason — never silently skipped.
DEFAULT_SCENARIOS = (
    "provider_dropout", "variable_dropout", "member_truncation",
    "latency_stress", "missing_opportunity")

_STR_FIELDS = frozenset({
    "case_id", "unit_id", "region", "season", "mechanism",
    "issue_time", "valid_start", "valid_end", "horizon", "y_state",
    "vintage_digest", "opportunity_id", "outcome_source_id",
    "cutoff_time", "event_group_id"})
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
    event_group_id: str = ""         # atomic event group (independence unit)
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


#: Required keys of a bound experiment declaration (EVAL-C06/FCST-C02).
_DECLARATION_KEYS = frozenset({
    "declaration_id", "feature_artifact_digest", "threshold_record",
    "ablations", "vintage_lineage"})


@dataclass(frozen=True)
class ForecastExperimentDeclaration:
    """A typed v0-lite experiment declaration.

    Binds the evaluation to its predeclared provenance: the
    declaration id, the feature-artifact digest, the decision
    ``threshold_record``, the ablation list, and the vintage lineage
    (the canonical digests of every admitted vintage the cases score
    under).  ``problems()`` is the admission surface — a declaration
    missing any key is inadmissible.  ``from_mapping`` performs the
    strict mapping conversion ``evaluate`` accepts.
    """

    declaration_id: str = ""
    feature_artifact_digest: str = ""
    threshold_record: Any = None
    ablations: Any = ()
    vintage_lineage: Any = ()

    def problems(self) -> list[str]:
        problems: list[str] = []
        if not isinstance(self.declaration_id, str) or \
                not self.declaration_id.strip():
            problems.append("declaration_id is required — a non-empty "
                            "string identifying the predeclared "
                            "experiment")
        if not isinstance(self.feature_artifact_digest, str) or \
                not _SHA256_RE.match(self.feature_artifact_digest):
            problems.append("feature_artifact_digest must be a 64-hex "
                            "sha256 digest binding the feature "
                            "artifact")
        tr = self.threshold_record
        if tr is None or tr == "" or \
                (isinstance(tr, (Mapping, Collection))
                 and not isinstance(tr, (str, bytes)) and not tr):
            problems.append("threshold_record is required — the "
                            "predeclared decision-threshold record")
        elif not isinstance(tr, (str, Mapping)):
            problems.append("threshold_record must be a mapping or a "
                            "record identifier string")
        abl = self.ablations
        if isinstance(abl, (str, bytes)) or \
                not isinstance(abl, Collection):
            problems.append("ablations must be a collection of "
                            "ablation declarations")
        elif any(not isinstance(a, (str, Mapping)) or
                 (isinstance(a, str) and not a.strip())
                 for a in abl):
            problems.append("ablations entries must be non-empty "
                            "identifiers or mappings")
        lin = self.vintage_lineage
        if isinstance(lin, (str, bytes)) or \
                not isinstance(lin, Collection):
            problems.append("vintage_lineage must be a collection of "
                            "canonical vintage digests")
        elif any(not isinstance(v, str) or not _SHA256_RE.match(v)
                 for v in lin):
            problems.append("vintage_lineage entries must be 64-hex "
                            "canonical vintage digests")
        return problems

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ablations"] = [dict(a) if isinstance(a, Mapping) else a
                          for a in (self.ablations or ())]
        d["vintage_lineage"] = list(self.vintage_lineage or ())
        d["declaration_type"] = "ForecastExperimentDeclaration"
        return d

    @classmethod
    def from_mapping(
            cls, m: Mapping) -> "ForecastExperimentDeclaration":
        """Strict mapping conversion — every required key must be
        present and no unknown keys are admitted."""
        if not isinstance(m, Mapping):
            raise ValueError(
                "experiment declaration must be a mapping")
        extra = set(m) - _DECLARATION_KEYS
        if extra:
            raise ValueError(
                f"experiment declaration: unknown keys "
                f"{sorted(extra)}")
        missing = _DECLARATION_KEYS - set(m)
        if missing:
            raise ValueError(
                f"experiment declaration: missing required keys "
                f"{sorted(missing)}")
        return cls(
            declaration_id=m["declaration_id"],
            feature_artifact_digest=m["feature_artifact_digest"],
            threshold_record=m["threshold_record"],
            ablations=tuple(m["ablations"])
            if isinstance(m["ablations"], Collection)
            and not isinstance(m["ablations"], (str, bytes))
            else m["ablations"],
            vintage_lineage=tuple(m["vintage_lineage"])
            if isinstance(m["vintage_lineage"], Collection)
            and not isinstance(m["vintage_lineage"], (str, bytes))
            else m["vintage_lineage"])


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


def _basin_owner_map(region_basins: Any) -> dict[str, str]:
    """Tolerant basin -> owning-region projection of a
    ``region_basins`` map (validation happens elsewhere; this helper
    only resolves scope membership and never raises)."""
    owner: dict[str, str] = {}
    if not isinstance(region_basins, Mapping):
        return owner
    for region, basins in region_basins.items():
        if isinstance(basins, (str, bytes)) or \
                not isinstance(basins, Collection):
            continue
        for basin in basins:
            if isinstance(basin, str) and basin not in owner:
                owner[basin] = str(region)
    return owner


def _scoped_opportunity_count(
        opportunities: Any,
        unit_basins: Any,
        region_basins: Any,
        *,
        regions: Optional[Collection[str]] = None,
        linked_ids: Optional[Collection[str]] = None,
        all_linked_ids: Optional[Collection[str]] = None) -> dict:
    """Verified and censored opportunity counts inside one scope.

    This is the single denominator-derivation surface shared by the
    top-level metric table, every slice (region / season / mechanism),
    and the lead-time buckets — case counts are never denominators.

    * ``regions`` restricts the scope to basins owned by those
      evaluation regions (``None`` = every declared region).
    * ``linked_ids`` — when given — restricts counting to
      opportunities linked by the scope's cases plus opportunities
      linked by *no* case (``all_linked_ids`` supplies the full
      case-linkage set), because registry entries carry no intrinsic
      season/mechanism/horizon attribution.  ``linked_ids=None``
      counts every in-scope entry.
    * An entry is *verified* when it is an ``ObservationOpportunityV0``
      whose id equals its registry key, whose ``problems()`` is empty,
      and whose ``state`` is ``OBSERVED_FULL``; anything else in scope
      is a *censored* opportunity — partial, unknown, unobserved, and
      defective records never enter a denominator.
    """
    owner = _basin_owner_map(region_basins)
    region_set = None if regions is None else \
        {str(r) for r in regions}
    allowed_basins = {b for b, o in owner.items()
                      if region_set is None or o in region_set}
    linked = None if linked_ids is None else \
        {str(k) for k in linked_ids}
    all_linked = None if all_linked_ids is None else \
        {str(k) for k in all_linked_ids}
    verified: list[str] = []
    censored: list[str] = []
    if not isinstance(opportunities, Mapping):
        return {"n_opportunities": 0, "n_censored_opportunities": 0,
                "verified_ids": (), "censored_ids": ()}
    for key, rec in opportunities.items():
        if type(rec) is not ObservationOpportunityV0:
            continue
        basin = unit_basins.get(rec.unit_id) \
            if isinstance(unit_basins, Mapping) else None
        if basin not in allowed_basins:
            continue
        if linked is not None:
            pool = all_linked if all_linked is not None else linked
            if key not in linked and key in pool:
                continue
        ok = (rec.opportunity_id == key
              and rec.state == "OBSERVED_FULL"
              and not rec.problems())
        (verified if ok else censored).append(str(key))
    return {"n_opportunities": len(verified),
            "n_censored_opportunities": len(censored),
            "verified_ids": tuple(sorted(verified)),
            "censored_ids": tuple(sorted(censored))}


def _slice_bundle(cases: Sequence[ForecastCase],
                  probs: Mapping[str, Sequence[float]],
                  idx: Sequence[int], *,
                  threshold: float,
                  scored: Collection[int],
                  link_status: Mapping[str, str],
                  scope: Mapping[str, Any],
                  scope_rule: str) -> dict:
    """Metric bundles for one slice.

    Only verified-basis unambiguous cases (``scored``) enter metric
    numerators; the opportunity denominator is the slice's own
    registry-derived ``opportunities_scoped`` — the slice's case count
    is never a denominator.
    """
    pos = [i for i in idx if i in scored]
    sub = [cases[i] for i in pos]
    y = _y(sub)
    scorers: dict[str, Any] = {}
    for name, full in probs.items():
        scorers[name] = _bundle(
            y, [full[i] for i in pos], threshold=threshold,
            n_opportunities=scope["n_opportunities"])
    n_state_censored = sum(
        1 for i in idx if cases[i].y_state == _CENSORED)
    unamb = [i for i in idx if cases[i].y_state != _CENSORED]
    return {
        "n_cases": len(idx),
        "n_scored": len(pos),
        "n_censored": n_state_censored,
        "n_censored_for_opportunity_state": sum(
            1 for i in unamb
            if link_status.get(cases[i].case_id) == "unverified"),
        "n_censored_out_of_scope": sum(
            1 for i in unamb
            if link_status.get(cases[i].case_id) == "out_of_scope"),
        "opportunities_scoped": scope["n_opportunities"],
        "n_censored_opportunities":
            scope["n_censored_opportunities"],
        "scope_rule": scope_rule,
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


def _scope_counter(opportunities: Any, unit_basins: Any,
                   region_basins: Any,
                   all_cases: Sequence[ForecastCase]):
    """Return ``subset_cases -> verified in-scope opportunity count``
    using the shared ``_scoped_opportunity_count`` (linked-by-subset
    plus never-linked, in the subset's region scope), or ``None`` when
    no opportunity registry is bound.  The fallback denominator —
    used only when the registry is absent — is the count of distinct
    linked opportunity ids, a lineage-derived count, never the raw
    case count."""
    bound = isinstance(opportunities, Mapping) and \
        isinstance(unit_basins, Mapping) and \
        isinstance(region_basins, Mapping)
    all_linked = {c.opportunity_id for c in all_cases
                  if isinstance(c.opportunity_id, str)
                  and c.opportunity_id}

    def count(subset: Sequence[ForecastCase]) -> int:
        if bound:
            return _scoped_opportunity_count(
                opportunities, unit_basins, region_basins,
                regions={c.region for c in subset},
                linked_ids={c.opportunity_id for c in subset
                            if c.opportunity_id},
                all_linked_ids=all_linked)["n_opportunities"]
        return len({c.opportunity_id for c in subset
                    if c.opportunity_id})

    return count


def _eligible_subset(cases: Sequence[ForecastCase],
                     eligible: Optional[Collection[str]]
                     ) -> list[ForecastCase]:
    """Unambiguous cases additionally restricted to the verified
    (eligible) basis when an eligibility set is bound."""
    if eligible is None:
        return _unambiguous(cases)
    ids = {str(x) for x in eligible}
    return [c for c in cases
            if c.y_state != _CENSORED and c.case_id in ids]


def missing_feed_degradation(
        cases: Sequence[ForecastCase],
        scenarios: Sequence[Mapping], *,
        opportunities: Any = None,
        unit_basins: Any = None,
        region_basins: Any = None,
        eligible: Optional[Collection[str]] = None,
        n_opportunities: Optional[int] = None,
        threshold: float = 0.5) -> dict:
    """Recompute core metrics under predeclared dropout scenarios.

    Each scenario is ``{"name": str}`` plus ``drop_units`` (an iterable
    of unit ids removed entirely) and/or ``drop_fraction`` (a
    deterministic fractional dropout by case-id hash).  Surviving
    cases are re-scored as-is — cases are degraded away, never
    imputed.  Opportunity denominators are registry-derived through
    ``_scoped_opportunity_count`` when the registry is bound (the
    surviving scope's verified opportunities), else the count of
    distinct linked opportunity ids — never a raw case count.
    ``eligible`` restricts scoring to verified-basis case ids.
    A scenario that empties the scored subset is flagged
    ``degenerate``.
    """
    cases = list(cases)
    denom = _scope_counter(
        opportunities, unit_basins, region_basins, cases)
    full = _eligible_subset(cases, eligible)
    reference = _core_metrics(
        _y(full), [c.y_prob for c in full], threshold=threshold,
        n_opportunities=(n_opportunities
                         if isinstance(n_opportunities, int)
                         and not isinstance(n_opportunities, bool)
                         else denom(cases)))
    out_scenarios: list[dict] = []
    for scenario in scenarios:
        name = str(scenario.get("name", "scenario"))
        drop_units = set(scenario.get("drop_units") or ())
        fraction = float(scenario.get("drop_fraction") or 0.0)
        survivors = [c for c in cases
                     if c.unit_id not in drop_units
                     and not _fraction_dropped(c.case_id, fraction)]
        sub = _eligible_subset(survivors, eligible)
        metrics = _core_metrics(_y(sub), [c.y_prob for c in sub],
                                threshold=threshold,
                                n_opportunities=denom(survivors))
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


def _case_cluster_key(c: ForecastCase) -> tuple:
    """The independence unit of a case: its atomic ``event_group_id``
    when carried, else the ``(unit_id, season)`` basin-season cell.
    Individual windows are never independent."""
    gid = c.event_group_id
    if isinstance(gid, str) and gid.strip():
        return ("event_group", gid)
    return ("unit_season_cell", c.unit_id, c.season)


def power_report(cases: Sequence[ForecastCase], *,
                 target_precision: float = 0.1,
                 alpha: float = 0.05,
                 eligible: Optional[Collection[str]] = None) -> dict:
    """Prospective precision/power computation on the declared design.

    The effective sample size is the number of independent clusters
    over the scored subset — an atomic ``event_group_id`` when the
    case carries one, else the ``(unit_id, season)`` basin-season
    cell.  ``n_effective`` is the distinct-cluster count, never the
    raw case count; ``clustering_unit`` names the rule.  ``eligible``
    optionally restricts scoring to verified-basis case ids.
    ``required_n`` is the cluster count needed for a worst-case
    (rate = 0.5) proportion to meet ``target_precision`` at the
    two-sided ``alpha`` level.  ``powered`` requires the effective
    size to meet the requirement and at least two clusters.
    """
    cases = list(cases)
    sub = _eligible_subset(cases, eligible)
    clusters = {_case_cluster_key(c) for c in sub}
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
        "n_scored": len(sub),
        "n_censored": len(cases) - len(sub),
        "n_clusters": n_clusters,
        "n_effective": n_clusters,
        "effective_n": n_clusters,
        "clustering_unit":
            "event_group_id_else_unit_id+season_cell",
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
                       *, n_boot: int = 200, seed: int = 0,
                       opportunities: Any = None,
                       unit_basins: Any = None,
                       region_basins: Any = None,
                       eligible: Optional[Collection[str]] = None,
                       n_opportunities: Optional[int] = None,
                       threshold: float = 0.5) -> dict:
    """Seeded (region, season) block bootstrap intervals.

    Clusters of *all* cases are resampled with replacement ``n_boot``
    times; each replicate recomputes the core metrics on the
    resampled scored (unambiguous, verified-basis) subset.  The point
    denominator is the registry-derived in-scope opportunity count
    (``n_opportunities`` when bound, else the count of distinct linked
    opportunity ids); each replicate's denominator is the
    registry-derived verified count for the drawn scope via the shared
    ``_scoped_opportunity_count`` — never a raw case count.
    Percentile intervals (alpha = 0.05) are reported per scorer and
    metric.  The stream is ``random.Random(seed)``: same inputs give
    identical intervals.
    """
    cases = list(cases)
    denom = _scope_counter(
        opportunities, unit_basins, region_basins, cases)
    point_denom = n_opportunities \
        if isinstance(n_opportunities, int) \
        and not isinstance(n_opportunities, bool) else denom(cases)
    clusters: dict[tuple[str, str], list[int]] = {}
    for i, c in enumerate(cases):
        clusters.setdefault((c.region, c.season), []).append(i)
    cluster_keys = sorted(clusters)
    rng = random.Random(int(seed))
    scorers: dict[str, Sequence[float]] = {
        "model": [c.y_prob for c in cases]}
    for name, probs in baseline_probs.items():
        scorers[str(name)] = list(probs)

    eligible_ids = None if eligible is None else \
        {str(x) for x in eligible}

    def _scored(idx: Sequence[int]) -> list[int]:
        return [i for i in idx
                if cases[i].y_state != _CENSORED
                and (eligible_ids is None
                     or cases[i].case_id in eligible_ids)]

    idx_un = _scored(range(len(cases)))
    out: dict[str, Any] = {}
    for name, probs in scorers.items():
        y_all = _y([cases[i] for i in idx_un])
        p_all = [probs[i] for i in idx_un]
        point = _core_metrics(y_all, p_all, threshold=threshold,
                              n_opportunities=point_denom)
        replicates: dict[str, list[float]] = {
            k: [] for k in ("brier", "auprc", "event_recall",
                            "false_alarms_per_opportunity")}
        for _ in range(max(0, int(n_boot))):
            if not cluster_keys:
                break
            draw = rng.choices(cluster_keys, k=len(cluster_keys))
            idx = [i for key in draw for i in clusters[key]]
            sub = _scored(idx)
            y = _y([cases[i] for i in sub])
            p = [probs[i] for i in sub]
            m = _core_metrics(
                y, p, threshold=threshold,
                n_opportunities=denom([cases[i] for i in idx]))
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
    ``declaration.mode`` is ``fixture_only`` unless a bound
    ``ForecastExperimentDeclaration`` was supplied.
    """

    experiment_id: str
    n_cases: int
    n_censored: int
    metrics: dict       # "model" + each baseline name -> metric bundle
    slices: dict        # per region / season / mechanism
    lead_time: dict     # per-horizon metric bundles
    degradation: dict   # dropout-scenario recomputation
    missing_feed: dict  # declared missing-feed scenario sub-reports
    power: dict
    uncertainty: dict   # block-bootstrap intervals
    status: str
    n_opportunities: int = 0
    n_censored_opportunities: int = 0
    n_censored_for_opportunity_state: int = 0
    n_censored_out_of_scope: int = 0
    opportunity_scope: dict = field(default_factory=dict)
    declaration: dict = field(default_factory=dict)
    claim_scope: str = "research_only_no_operational_authorization"

    def to_dict(self) -> dict:
        return asdict(self)


def _case_link_status(c: ForecastCase,
                      registry: Mapping[str, Any],
                      unit_basins: Mapping[str, Any],
                      basin_owner: Mapping[str, str]) -> str:
    """The opportunity-basis verdict for one case.

    ``verified`` — the case links a problem-free in-scope
    ``OBSERVED_FULL`` record on its own unit and claimed region; only
    verified unambiguous cases are scored.  ``unverified`` /
    ``out_of_scope`` censor the case (a case's state is never trusted
    without the opportunity basis).  ``absent`` /
    ``invalid_record`` / ``unit_mismatch`` / ``region_mismatch`` are
    structural defects reported as problems, never scored.
    """
    opp = registry.get(c.opportunity_id)
    if opp is None:
        return "absent"
    if type(opp) is not ObservationOpportunityV0 or \
            opp.opportunity_id != c.opportunity_id:
        return "invalid_record"
    if opp.unit_id != c.unit_id:
        return "unit_mismatch"
    basin = unit_basins.get(opp.unit_id) \
        if isinstance(unit_basins, Mapping) else None
    owner = basin_owner.get(basin) if isinstance(basin, str) else None
    if owner is None:
        return "out_of_scope"
    if owner != c.region:
        return "region_mismatch"
    if opp.state != "OBSERVED_FULL" or opp.problems():
        return "unverified"
    return "verified"


def _missing_feed_report(
        names: Sequence[str], *,
        cases: Sequence[ForecastCase],
        scored_idx: Sequence[int],
        scorers: Mapping[str, Sequence[float]],
        threshold: float,
        n_opportunities: int,
        n_censored_for_opportunity_state: int) -> dict:
    """Execute the declared missing-feed scenario set.

    Each scenario recomputes the scorer table on the identical
    verified-basis scored subset — ``EXECUTED`` with its own metric
    bundle — or reports ``NOT_APPLICABLE`` with a reason where the
    scenario's axis does not exist (point probabilities carry no
    ensemble-member axis; no provider-latency distribution is bound).
    """
    y = _y([cases[i] for i in scored_idx])

    def _table(drop: Collection[str] = (),
               overrides: Optional[Mapping[str, Sequence[float]]]
               = None) -> dict:
        out: dict[str, Any] = {}
        for name, probs in scorers.items():
            if name in drop:
                continue
            vec = probs if overrides is None \
                else overrides.get(name, probs)
            out[name] = _bundle(
                y, [vec[i] for i in scored_idx],
                threshold=threshold,
                n_opportunities=n_opportunities)
        return out

    results: list[dict] = []
    for raw in names:
        name = str(raw)
        if name == "provider_dropout":
            results.append({
                "name": name, "status": "EXECUTED",
                "reason": "model scorer output dropped — the "
                          "baseline suite is rescored on the "
                          "identical verified-basis subset",
                "n_scored": len(scored_idx),
                "scorers": _table(drop={"model"})})
        elif name == "variable_dropout":
            clim = scorers.get("climatology")
            if clim is None:
                results.append({
                    "name": name, "status": "NOT_APPLICABLE",
                    "reason": "no climatology baseline vector is "
                              "bound — the feature-starved rule "
                              "baseline has no fallback"})
            else:
                results.append({
                    "name": name, "status": "EXECUTED",
                    "reason": "case features are unavailable — the "
                              "rule baseline cannot observe its "
                              "inputs and falls back to climatology "
                              "rates",
                    "n_scored": len(scored_idx),
                    "scorers": _table(overrides={"rule": clim})})
        elif name == "member_truncation":
            results.append({
                "name": name, "status": "NOT_APPLICABLE",
                "reason": "cases carry point probabilities, not "
                          "ensemble members — there is no member "
                          "axis to truncate"})
        elif name == "latency_stress":
            results.append({
                "name": name, "status": "NOT_APPLICABLE",
                "reason": "no provider-latency distribution is bound "
                          "to the evaluation — the effective horizon "
                          "cannot be shifted"})
        elif name == "missing_opportunity":
            results.append({
                "name": name, "status": "EXECUTED",
                "reason": "metrics recomputed with cases linked to "
                          "non-OBSERVED_FULL or unverified registry "
                          "entries censored — a case's state is never "
                          "trusted without the opportunity basis",
                "n_scored": len(scored_idx),
                "n_censored_for_opportunity_state":
                    n_censored_for_opportunity_state,
                "scorers": _table()})
        else:
            results.append({
                "name": name, "status": "NOT_APPLICABLE",
                "reason": f"scenario {name!r} is not a declared "
                          "missing-feed scenario"})
    return {"policy": "declared_missing_feed_recompute_never_"
                      "imputation",
            "scenarios": results}


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
             seed: int = 0,
             scenarios: Collection[str] = DEFAULT_SCENARIOS,
             experiment: Any = None) -> EvaluationReport:
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
    must be an ``ObservationOpportunityV0`` whose ``opportunity_id``
    equals its map key and whose ``unit_id`` has a ``unit_basins``
    mapping; entries failing ``problems()`` or not ``OBSERVED_FULL``
    are excluded from every denominator and counted in
    ``n_censored_opportunities``.  Every case's ``opportunity_id``
    must resolve to a registry entry on the case's own unit; an
    unambiguous case whose linked entry is not a verified in-scope
    ``OBSERVED_FULL`` record is censored — never scored — and counted
    in ``n_censored_for_opportunity_state`` or
    ``n_censored_out_of_scope``.

    The false-alarm denominator ``n_opportunities`` is *derived* from
    the registry by ``_scoped_opportunity_count``: the count of
    problem-free ``OBSERVED_FULL`` opportunities whose unit's basin
    lies inside the declared evaluation regions.  It is never
    caller-chosen.  The optional ``n_opportunities_declared`` is
    tamper evidence only — when given it must equal the derived count
    exactly.

    ``scenarios`` names the missing-feed scenario set executed into
    ``report.missing_feed`` (default ``DEFAULT_SCENARIOS``).
    ``experiment`` optionally binds a
    ``ForecastExperimentDeclaration`` — as the typed record or a
    strictly-convertible mapping — whose ``problems()`` and whose
    ``vintage_lineage``/``threshold_record`` bindings are validated;
    when absent the report records ``mode="fixture_only"``.

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
            basin = ub.get(rec.unit_id)
            if not isinstance(basin, str) or not basin.strip():
                problems.append(
                    f"{tag}: unit {rec.unit_id!r} has no basin "
                    "mapping in unit_basins")
            # rec.problems() is NOT a hard reject: a defective or
            # non-OBSERVED_FULL record is excluded from every
            # denominator and counted as a censored opportunity —
            # the registry's scope evidence decides scoring.

    # The denominator: problem-free OBSERVED_FULL registry
    # opportunities inside the declared evaluation scope — derived by
    # the shared scope counter; partial/unknown/unobserved/defective
    # entries are counted separately as censored opportunities.
    scope_result = _scoped_opportunity_count(
        registry, ub, region_basins)
    n_opportunities = scope_result["n_opportunities"]
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
    link_status: dict[str, str] = {}
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
        status = _case_link_status(c, registry, ub, basin_owner)
        link_status[c.case_id] = status
        if isinstance(c.opportunity_id, str) and \
                c.opportunity_id.strip():
            if status == "absent":
                problems.append(
                    f"case {c.case_id!r}: opportunity_id "
                    f"{c.opportunity_id!r} is absent from the "
                    "opportunity registry")
            elif status == "invalid_record":
                problems.append(
                    f"case {c.case_id!r}: opportunity_id "
                    f"{c.opportunity_id!r} does not resolve to an "
                    "ObservationOpportunityV0 record keyed by that "
                    "id — the opportunity basis is unverifiable")
            elif status == "unit_mismatch":
                opp = registry[c.opportunity_id]
                problems.append(
                    f"case {c.case_id!r}: unit {c.unit_id!r} "
                    f"does not match opportunity "
                    f"{opp.opportunity_id!r} unit "
                    f"{opp.unit_id!r}")
            elif status == "region_mismatch":
                opp = registry[c.opportunity_id]
                basin = ub.get(opp.unit_id)
                problems.append(
                    f"case {c.case_id!r}: opportunity basin "
                    f"{basin!r} belongs to evaluation region "
                    f"{basin_owner.get(basin)!r}, not the case's "
                    f"declared region {c.region!r}")
            # "unverified" / "out_of_scope" are NOT problems: the
            # case is censored — never scored — because its declared
            # state cannot be trusted without the verified
            # opportunity basis.

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

    # ---- declared missing-feed scenario set -----------------------
    scenario_names: list[str] = []
    if isinstance(scenarios, (str, bytes)) or \
            not isinstance(scenarios, Collection):
        problems.append("scenarios must be a collection of declared "
                        "missing-feed scenario names")
    else:
        for s in scenarios:
            if not isinstance(s, str) or not s.strip():
                problems.append(
                    f"scenario name {s!r} must be a non-empty "
                    "string")
            else:
                scenario_names.append(s)

    # ---- optional bound experiment declaration --------------------
    decl: Optional[ForecastExperimentDeclaration] = None
    if experiment is not None:
        if type(experiment) is ForecastExperimentDeclaration:
            decl = experiment
        elif isinstance(experiment, Mapping):
            try:
                decl = ForecastExperimentDeclaration.from_mapping(
                    experiment)
            except ValueError as exc:
                problems.append(f"experiment: {exc}")
        else:
            problems.append(
                "experiment must be a ForecastExperimentDeclaration "
                "or a mapping convertible to one — a standalone "
                "probability mapping claims fixture scope only")
        if decl is not None:
            problems.extend(f"experiment: {p}"
                            for p in decl.problems())
            lineage = {str(v) for v in
                       (decl.vintage_lineage or ())}
            used = {c.vintage_digest for c in cases
                    if isinstance(c.vintage_digest, str)}
            uncovered = used - lineage
            if uncovered:
                problems.append(
                    "experiment: vintage_lineage does not cover the "
                    f"admitted case vintage digests "
                    f"{sorted(uncovered)} — the declared lineage "
                    "must bind every vintage the cases score under")
            tr = decl.threshold_record
            if isinstance(tr, Mapping) and "threshold" in tr:
                tv = tr["threshold"]
                if isinstance(tv, bool) or \
                        not isinstance(tv, (int, float)) or \
                        not math.isfinite(float(tv)):
                    problems.append(
                        "experiment: threshold_record['threshold'] "
                        "must be a finite number")
                elif abs(float(tv) - float(threshold)) > _TIME_EPS:
                    problems.append(
                        f"experiment: threshold_record threshold "
                        f"{tv!r} does not equal the evaluation "
                        f"threshold {threshold!r} — the decision "
                        "threshold is bound by the declaration")
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

    # The scored subset: unambiguous cases whose linked opportunity is
    # a verified in-scope OBSERVED_FULL record.  Every other case is
    # excluded from every metric numerator — a case's declared state
    # is never trusted without the opportunity basis.
    scored_idx = [i for i, c in enumerate(cases)
                  if c.y_state != _CENSORED
                  and link_status.get(c.case_id) == "verified"]
    scored_set = set(scored_idx)
    sub = [cases[i] for i in scored_idx]
    y = _y(sub)
    n_state_censored = sum(
        1 for c in cases if c.y_state == _CENSORED)
    n_cens_for_state = sum(
        1 for c in cases if c.y_state != _CENSORED
        and link_status.get(c.case_id) == "unverified")
    n_cens_out_scope = sum(
        1 for c in cases if c.y_state != _CENSORED
        and link_status.get(c.case_id) == "out_of_scope")

    scorers: dict[str, Sequence[float]] = {
        "model": [c.y_prob for c in cases]}
    scorers.update(aligned_baselines)

    metric_table: dict[str, Any] = {}
    for name, probs in scorers.items():
        metric_table[name] = _bundle(
            y, [probs[i] for i in scored_idx], threshold=threshold,
            n_opportunities=n_opportunities)

    all_linked_ids = {c.opportunity_id for c in cases
                      if c.opportunity_id}

    def _slice_scope(axis: str, value: str,
                     idx: Sequence[int]) -> tuple[dict, str]:
        """The slice's verified-opportunity scope via the shared
        ``_scoped_opportunity_count`` — region slices count every
        opportunity owned by the region; season/mechanism/lead-time
        slices count opportunities linked by the slice's cases plus
        opportunities linked by no case, within the slice's region
        scope (registry entries carry no intrinsic season, mechanism,
        or horizon attribution)."""
        if axis == "region":
            return _scoped_opportunity_count(
                registry, ub, region_basins,
                regions={value}), "region_owned_basins"
        return _scoped_opportunity_count(
            registry, ub, region_basins,
            regions={cases[i].region for i in idx},
            linked_ids={cases[i].opportunity_id
                        for i in idx if cases[i].opportunity_id},
            all_linked_ids=all_linked_ids), \
            "linked_case_opportunities_plus_unlinked_in_case_regions"

    slices: dict[str, Any] = {}
    for axis, key in (("region", lambda c: c.region),
                      ("season", lambda c: c.season),
                      ("mechanism", lambda c: c.mechanism)):
        slices[axis] = {}
        for value, idx in sorted(
                _group_indices(cases, key).items()):
            scope, rule = _slice_scope(axis, value, idx)
            slices[axis][value] = _slice_bundle(
                cases, scorers, idx, threshold=threshold,
                scored=scored_set, link_status=link_status,
                scope=scope, scope_rule=rule)
    lead_time = {}
    for h, idx in sorted(
            _group_indices(cases, lambda c: c.horizon).items(),
            key=lambda kv: HORIZON_SECONDS[kv[0]]):
        scope, rule = _slice_scope("horizon", h, idx)
        lead_time[h] = _slice_bundle(
            cases, scorers, idx, threshold=threshold,
            scored=scored_set, link_status=link_status,
            scope=scope, scope_rule=rule)

    scored_ids = {cases[i].case_id for i in scored_idx}
    power = power_report(cases, eligible=scored_ids)
    degradation = missing_feed_degradation(
        cases, (), opportunities=registry, unit_basins=ub,
        region_basins=region_basins, eligible=scored_ids,
        n_opportunities=n_opportunities, threshold=threshold)
    missing_feed = _missing_feed_report(
        scenario_names, cases=cases, scored_idx=scored_idx,
        scorers=scorers, threshold=threshold,
        n_opportunities=n_opportunities,
        n_censored_for_opportunity_state=n_cens_for_state)
    uncertainty = uncertainty_report(
        cases, aligned_baselines, n_boot=n_boot, seed=seed,
        opportunities=registry, unit_basins=ub,
        region_basins=region_basins, eligible=scored_ids,
        n_opportunities=n_opportunities, threshold=threshold)
    status = ("FORECAST_EXPERIMENT_ONLY" if power["powered"]
              else "UNDERPOWERED_DESCRIPTIVE_ONLY")

    if decl is None:
        declaration = {
            "mode": "fixture_only",
            "reason": "no experiment declaration bound — a "
                      "standalone probability mapping can never "
                      "claim more than fixture scope"}
    else:
        tr = decl.threshold_record
        declaration = {
            "mode": "declared",
            "declaration_id": decl.declaration_id,
            "feature_artifact_digest": decl.feature_artifact_digest,
            "threshold_record_digest":
                sha256_canonical(dict(tr)) if isinstance(tr, Mapping)
                else str(tr),
            "n_ablations": len(decl.ablations or ()),
            "n_vintage_lineage": len(decl.vintage_lineage or ()),
        }

    digest = sha256_canonical({
        "cases": [c.to_dict() for c in cases],
        "baselines": {k: list(v) for k, v in scorers.items()},
        "vintages": sorted(str(k) for k in admitted_vintages),
        "opportunity_ids": sorted(str(k) for k in registry),
        "n_opportunities": n_opportunities,
        "scenarios": scenario_names,
        "declaration": declaration,
        "holdout": holdout.to_dict(),
    })
    experiment_id = f"eval-{digest[:16]}"

    return EvaluationReport(
        experiment_id=experiment_id,
        n_cases=len(cases),
        n_censored=n_state_censored,
        metrics=metric_table,
        slices=slices,
        lead_time=lead_time,
        degradation=degradation,
        missing_feed=missing_feed,
        power=power,
        uncertainty=uncertainty,
        status=status,
        n_opportunities=n_opportunities,
        n_censored_opportunities=
        scope_result["n_censored_opportunities"],
        n_censored_for_opportunity_state=n_cens_for_state,
        n_censored_out_of_scope=n_cens_out_scope,
        opportunity_scope={
            "n_opportunities": n_opportunities,
            "n_censored_opportunities":
                scope_result["n_censored_opportunities"],
            "verified_ids": list(scope_result["verified_ids"]),
            "censored_ids": list(scope_result["censored_ids"]),
        },
        declaration=declaration)


__all__ = [
    "ForecastCase", "EvaluationReport",
    "ForecastExperimentDeclaration", "DEFAULT_SCENARIOS",
    "locked_region_problems", "evaluate", "power_report",
    "missing_feed_degradation", "uncertainty_report",
]
