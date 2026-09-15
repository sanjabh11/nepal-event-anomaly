"""Held-out event–regime association harness (ASSOCIATION_PROTOCOL_V0).

This module implements the contract-layer, synthetic-fixture-only half
of the run-C association protocol: given a *frozen*, label-blind regime
assignment artifact and independently adjudicated event labels plus
derived control windows, it computes regime-enrichment statistics,
transition-pair statistics, rarity ("novelty") slices, three negative
controls, interval-placement sensitivity, and pooled/per-basin/
per-season slices.  Uncertainty comes from a deterministic seeded
event-group bootstrap that resamples atomic cascade groups within
basin-aware strata.

Boundaries honored here:

* Association only — every output is a co-occurrence statistic on
  held-out units; nothing here is an anticipatory, causal, or
  authority-carrying quantity.
* The regime artifact is an immutable input: it has no fitting or
  mutation surface and is never rebuilt from labels.
* Censored or ambiguous windows stay three-valued — a control that is
  not ``NEGATIVE`` never enters a control denominator, and an event
  label that is not adjudicated never enters a positive cell.
* Cascades and lineage-linked events are atomic units: they count once
  in group totals and are never split across bootstrap resamples.
* No wall-clock reads, no network, no real data: everything is a pure
  function of its arguments plus the declared ``seed``.
"""
from __future__ import annotations

import random
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date as _date
from datetime import datetime, timedelta, timezone
from typing import Any, Collection, Mapping, Optional, Sequence

from nepal.research_v0.policy import (TargetState, parse_strict_utc,
                                      require_finite_seconds)
from nepal.research_v0.records import (POSITIVE_ADMISSIBLE_ADJUDICATION,
                                      ControlWindowV0, EventLabelV0,
                                      HoldoutPlanV0)

# ---------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------

#: Minimum number of atomic event groups (cascade-aware) needed before
#: an enrichment interval may support the top neutral status.
MIN_EVENT_GROUPS = 20

#: The only statuses this harness may emit — a strict subset of the
#: neutral research status vocabulary in ``research_v0.records``.
ASSOCIATION_STATUSES = frozenset({
    "REGIME_ASSOCIATION_SUPPORTED",
    "DESCRIPTIVE_REGIME_ONLY",
    "UNDERPOWERED_DESCRIPTIVE_ONLY",
    "UNSUPERVISED_PATH_NOT_SUPPORTED",
})

STATUS_SUPPORTED = "REGIME_ASSOCIATION_SUPPORTED"
STATUS_DESCRIPTIVE = "DESCRIPTIVE_REGIME_ONLY"
STATUS_UNDERPOWERED = "UNDERPOWERED_DESCRIPTIVE_ONLY"
STATUS_NOT_SUPPORTED = "UNSUPERVISED_PATH_NOT_SUPPORTED"

#: Event-time interval placements recomputed for sensitivity reporting.
PLACEMENT_MODES = ("midpoint", "uniform", "worst_case")

#: A regime whose control-frame share is at or under this fraction is
#: reported in the rarity ("novelty") slice.
NOVELTY_SHARE_MAX = 0.10

#: Pseudo-label for (unit, date) cells with no frozen assignment.  It is
#: counted in share denominators — never silently dropped — but is never
#: reported as a regime in the enrichment mapping.
UNASSIGNED_LABEL = "__UNASSIGNED__"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DAY_SECONDS = 86400.0

# Fixed calendar-season mapping (meteorological quarters); deterministic
# and wall-clock free.
_SEASON_BY_MONTH = {
    12: "DJF", 1: "DJF", 2: "DJF",
    3: "MAM", 4: "MAM", 5: "MAM",
    6: "JJA", 7: "JJA", 8: "JJA",
    9: "SON", 10: "SON", 11: "SON",
}

# Transition statistics are defined only for labels whose interval is
# resolved to seven days or better; coarser labels remain in the
# enrichment universe but contribute no trajectory rows.
_TRANSITION_UNCERTAINTY_MAX = 7 * _DAY_SECONDS


# ---------------------------------------------------------------------
# Small deterministic helpers (pure; no wall clock)
# ---------------------------------------------------------------------

def _epoch_to_date(epoch: float) -> _date:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).date()


def _dates_between(first: _date, last: _date) -> list[_date]:
    out: list[_date] = []
    d = first
    while d <= last:
        out.append(d)
        d += timedelta(days=1)
    return out


def _event_dates(event: EventLabelV0) -> tuple[_date, ...]:
    """Every calendar date the event interval intersects (inclusive)."""
    s = parse_strict_utc(event.event_time_start)
    e = parse_strict_utc(event.event_time_end)
    if s is None or e is None or e < s:
        return ()
    return tuple(_dates_between(_epoch_to_date(s), _epoch_to_date(e)))


def _event_midpoint_date(event: EventLabelV0) -> Optional[_date]:
    s = parse_strict_utc(event.event_time_start)
    e = parse_strict_utc(event.event_time_end)
    if s is None or e is None or e < s:
        return None
    return _epoch_to_date((s + e) / 2.0)


def _season_of(day: _date) -> str:
    return _SEASON_BY_MONTH[day.month]


def _control_dates(control: ControlWindowV0) -> tuple[_date, ...]:
    s = parse_strict_utc(control.window_start)
    e = parse_strict_utc(control.window_end)
    if s is None or e is None or e <= s:
        return ()
    # A control window covers the dates it intersects; the half-open
    # end at exact midnight still touches that calendar date.
    return tuple(_dates_between(_epoch_to_date(s), _epoch_to_date(e)))


def _percentile_pair(values: Sequence[float]) -> tuple[float, float]:
    """Deterministic 2.5%/97.5% interval over a sorted-copy input."""
    ordered = sorted(values)
    n = len(ordered)
    if n == 1:
        return ordered[0], ordered[0]
    lo = ordered[min(n - 1, int(0.025 * n))]
    hi = ordered[min(n - 1, int(0.975 * n))]
    return lo, hi


def _ratio(numer: float, denom: float) -> Optional[float]:
    """Enrichment ratio; ``None`` when the control share is zero —
    infinity is never emitted (it cannot survive canonical JSON)."""
    if denom <= 0.0:
        return None
    return numer / denom


def _round12(x: Optional[float]) -> Optional[float]:
    """Deterministic 12-decimal rounding so float accumulation noise
    never sits a hair across an interval boundary in canonical JSON."""
    return None if x is None else round(x, 12)


# ---------------------------------------------------------------------
# Frozen regime-assignment artifact (synthetic stand-in)
# ---------------------------------------------------------------------

@dataclass(frozen=True)
class RegimeAssignmentArtifact:
    """Frozen synthetic regime-assignment artifact (stand-in for a real
    frozen RegimeArtifactV0 downstream output). Immutable; label-blind.

    ``assignments`` rows are ``(unit_id, "YYYY-MM-DD", regime_id)`` —
    one regime per unit-day, fixed before any label is opened.  The
    record carries no method that can fit, refit, or mutate it.
    """

    artifact_id: str
    regime_digest: str        # 64-hex sha256
    assignments: tuple[tuple[str, str, str], ...]
    fitted_on: str = "TRAIN_ONLY"
    label_blinding: bool = True
    seeds: tuple[int, ...] = ()
    mode: str = "RETROSPECTIVE_REGIME"

    def __post_init__(self) -> None:
        index: dict[tuple[str, str], str] = {}
        for row in self.assignments:
            try:
                unit_id, day, regime_id = row
            except (TypeError, ValueError):
                continue  # malformed rows surface via problems()
            index[(str(unit_id), str(day))] = str(regime_id)
        object.__setattr__(self, "_index", index)

    def problems(self) -> list[str]:
        problems: list[str] = []
        if not isinstance(self.artifact_id, str) or \
                not self.artifact_id.strip():
            problems.append("artifact_id is required")
        if not isinstance(self.regime_digest, str) or \
                not _SHA256_RE.match(self.regime_digest):
            problems.append("regime_digest must be a 64-hex sha256 "
                            "digest")
        if not self.assignments:
            problems.append("assignments must be non-empty")
        else:
            seen: set[tuple[str, str]] = set()
            for row in self.assignments:
                if not isinstance(row, (tuple, list)) or len(row) != 3:
                    problems.append("every assignment must be a "
                                    "(unit_id, date, regime_id) triple")
                    continue
                unit_id, day, regime_id = row
                for name, value in (("unit_id", unit_id),
                                    ("regime_id", regime_id)):
                    if not isinstance(value, str) or not value.strip():
                        problems.append(f"assignment {name} must be a "
                                        "non-empty string")
                if not isinstance(day, str) or not _DATE_RE.match(day):
                    problems.append("assignment date must be an ISO "
                                    "YYYY-MM-DD date")
                else:
                    try:
                        _date.fromisoformat(day)
                    except ValueError:
                        problems.append(f"assignment date {day!r} is "
                                        "not a real calendar date")
                key = (str(unit_id), str(day))
                if key in seen:
                    problems.append(f"duplicate assignment for unit "
                                    f"{unit_id!r} on {day!r} — one "
                                    "regime per unit-day")
                seen.add(key)
        if self.fitted_on != "TRAIN_ONLY":
            problems.append("assignments must be fitted on training "
                            "groups only")
        if self.label_blinding is not True:
            problems.append("label_blinding must be True — event labels "
                            "never enter regime fitting or selection")
        if self.mode != "RETROSPECTIVE_REGIME":
            problems.append("mode must be RETROSPECTIVE_REGIME — the "
                            "association lane accepts retrospective "
                            "partitions only")
        distinct = {s for s in self.seeds
                    if isinstance(s, int) and not isinstance(s, bool)
                    and s >= 0}
        if not self.seeds or len(distinct) < 3 or \
                len(distinct) != len(self.seeds):
            problems.append("at least three distinct non-negative "
                            "integer seeds are required")
        return problems

    def regime_ids(self) -> tuple[str, ...]:
        """Distinct regime labels in canonical (sorted) order."""
        return tuple(sorted({str(r[2]) for r in self.assignments
                             if isinstance(r, (tuple, list))
                             and len(r) == 3}))

    def regime_for(self, unit_id: str, date: str) -> Optional[str]:
        """The frozen regime for one unit-day, or ``None``."""
        return self._index.get((str(unit_id), str(date)))

    def to_dict(self) -> dict:
        return {
            "record_type": type(self).__name__,
            "artifact_id": self.artifact_id,
            "regime_digest": self.regime_digest,
            # Canonical ordering: the serialized form — and therefore
            # its digest — is independent of input row order.
            "assignments": sorted(
                [list(r) for r in self.assignments]),
            "fitted_on": self.fitted_on,
            "label_blinding": self.label_blinding,
            "seeds": list(self.seeds),
            "mode": self.mode,
        }

    @classmethod
    def from_dict(cls, d: Mapping) -> "RegimeAssignmentArtifact":
        """Strict typed reconstruction: unknown fields, missing
        required fields, and wrong primitive types all reject —
        ``"false"`` never becomes ``True``, and assignments must be
        real ``(str, str, str)`` triples with calendar-valid dates."""
        problems = _strict_artifact_payload_problems(d)
        if problems:
            raise ValueError("RegimeAssignmentArtifact.from_dict "
                             "rejected: " + "; ".join(problems))
        return cls(
            artifact_id=d["artifact_id"],
            regime_digest=d["regime_digest"],
            assignments=tuple(tuple(row) for row in d["assignments"]),
            fitted_on=d.get("fitted_on", "TRAIN_ONLY"),
            label_blinding=d.get("label_blinding", True),
            seeds=tuple(d.get("seeds", ())),
            mode=d.get("mode", "RETROSPECTIVE_REGIME"),
        )


def _strict_artifact_payload_problems(d: Any) -> list[str]:
    """Field-level validation for ``RegimeAssignmentArtifact``
    payloads: exact key set, required fields present, primitive types
    exact (``bool`` only for ``label_blinding``, ``int`` only for
    seeds, ``str`` only for text fields, real sequences for tuple
    fields), and assignment dates calendar-valid."""
    fields = ("artifact_id", "regime_digest", "assignments",
              "fitted_on", "label_blinding", "seeds", "mode")
    problems: list[str] = []
    if not isinstance(d, Mapping):
        return ["payload must be a JSON-object mapping"]
    tag = d.get("record_type")
    if tag is not None and tag != "RegimeAssignmentArtifact":
        problems.append(f"record_type {tag!r} is not "
                        "'RegimeAssignmentArtifact'")
    unknown = sorted(set(d) - set(fields) - {"record_type"})
    if unknown:
        problems.append(f"unknown fields {unknown}")
    for name in ("artifact_id", "regime_digest", "assignments"):
        if name not in d:
            problems.append(f"missing required field {name!r}")
    for name in ("artifact_id", "regime_digest", "fitted_on", "mode"):
        if name in d and type(d[name]) is not str:
            problems.append(f"field {name!r} must be a string")
    if "label_blinding" in d and type(d["label_blinding"]) is not bool:
        problems.append("field 'label_blinding' must be a boolean — "
                        "strings like 'false' are not accepted")
    if "seeds" in d:
        seeds = d["seeds"]
        if not isinstance(seeds, (list, tuple)) or isinstance(
                seeds, (str, bytes)):
            problems.append("field 'seeds' must be a sequence of "
                            "integers")
        else:
            for s in seeds:
                if type(s) is not int:
                    problems.append("every seed must be an integer")
                    break
    if "assignments" in d:
        rows = d["assignments"]
        if not isinstance(rows, (list, tuple)) or isinstance(
                rows, (str, bytes)):
            problems.append("field 'assignments' must be a sequence "
                            "of (unit_id, date, regime_id) triples")
        else:
            for row in rows:
                if not isinstance(row, (list, tuple)) or \
                        isinstance(row, (str, bytes)) or len(row) != 3:
                    problems.append("every assignment must be a "
                                    "3-element sequence")
                    continue
                if any(type(x) is not str for x in row):
                    problems.append("assignment fields must all be "
                                    "strings")
                    continue
                day = row[1]
                if not _DATE_RE.match(day):
                    problems.append(f"assignment date {day!r} is not "
                                    "YYYY-MM-DD")
                    continue
                try:
                    _date.fromisoformat(day)
                except ValueError:
                    problems.append(f"assignment date {day!r} is not "
                                    "a real calendar date")
    return problems


# ---------------------------------------------------------------------
# Atomic event groups (cascade / lineage aware)
# ---------------------------------------------------------------------

def _group_events(events: Sequence[EventLabelV0]) -> dict[str, list]:
    """Union-find over cascade_group_id and parent/duplicate lineage.

    Returns ``{group_id: [member events]}`` where ``group_id`` is the
    lexicographically smallest member ``event_id`` — deterministic for
    identical input sets regardless of input order.
    """
    parent: dict[str, str] = {e.event_id: e.event_id for e in events}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    by_cascade: dict[str, list[str]] = {}
    for e in events:
        if e.cascade_group_id:
            by_cascade.setdefault(e.cascade_group_id, []).append(
                e.event_id)
    for members in by_cascade.values():
        for member in members[1:]:
            union(members[0], member)
    for e in events:
        for ref in (e.parent_event_id, e.duplicate_of):
            if ref and ref in parent:
                union(e.event_id, ref)
    comps: dict[str, list] = {}
    for e in events:
        comps.setdefault(find(e.event_id), []).append(e)
    groups: dict[str, list] = {}
    for members in comps.values():
        ordered = sorted(members, key=lambda m: m.event_id)
        groups[ordered[0].event_id] = ordered
    return groups


def _group_basins(members: Sequence[EventLabelV0]) -> tuple[str, ...]:
    return tuple(sorted({m.basin_id for m in members if m.basin_id}))


def _group_season(members: Sequence[EventLabelV0]) -> str:
    days = [d for d in (_event_midpoint_date(m) for m in members)
            if d is not None]
    if not days:
        return "UNKNOWN"
    return _season_of(min(days))


# ---------------------------------------------------------------------
# Association table construction
# ---------------------------------------------------------------------

def _basin_units(unit_basins: Mapping[str, str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for unit, basin in unit_basins.items():
        out.setdefault(str(basin), []).append(str(unit))
    return {b: sorted(us) for b, us in out.items()}


def _group_label_counts(
        artifact: RegimeAssignmentArtifact,
        members: Sequence[EventLabelV0],
        basin_units: Mapping[str, Sequence[str]],
        mode: str) -> tuple[dict[str, float], float]:
    """Weighted regime histogram for one atomic event group.

    Each group contributes total weight equal to the number of distinct
    basin units it touches, spread evenly over its placed (unit, date)
    cells — so ``midpoint`` and ``uniform`` differ only in how the
    interval weight is spread across dates, never in group weight.
    Cells with no frozen assignment count under ``UNASSIGNED_LABEL``.
    """
    samples: list[tuple[str, _date]] = []
    units: set[str] = set()
    for ev in members:
        us = basin_units.get(ev.basin_id, ())
        units.update(us)
        if mode == "midpoint":
            day = _event_midpoint_date(ev)
            dates = (day,) if day is not None else ()
        else:  # "uniform"
            dates = _event_dates(ev)
        for u in us:
            for d in dates:
                samples.append((u, d))
    if not samples or not units:
        return {}, 0.0
    weight = len(units) / float(len(samples))
    counts: Counter[str] = Counter()
    for unit, day in samples:
        label = artifact.regime_for(unit, day.isoformat())
        counts[label if label is not None else UNASSIGNED_LABEL] += weight
    return dict(counts), float(len(units))


def _group_date_histograms(
        artifact: RegimeAssignmentArtifact,
        members: Sequence[EventLabelV0],
        basin_units: Mapping[str, Sequence[str]],
        ) -> tuple[dict[_date, Counter], int]:
    """Per-date unweighted regime histograms for one event group —
    the raw material for ``worst_case`` placement, which may choose a
    different intersected date per tested regime."""
    hist: dict[_date, Counter] = {}
    units: set[str] = set()
    for ev in members:
        us = basin_units.get(ev.basin_id, ())
        units.update(us)
        for day in _event_dates(ev):
            counter = hist.setdefault(day, Counter())
            for u in us:
                label = artifact.regime_for(u, day.isoformat())
                counter[label if label is not None
                        else UNASSIGNED_LABEL] += 1
    return hist, len(units)


def _group_transition_counts(
        artifact: RegimeAssignmentArtifact,
        members: Sequence[EventLabelV0],
        basin_units: Mapping[str, Sequence[str]],
        ) -> tuple[dict[str, float], float]:
    """Weighted transition-pair histogram for one event group.

    For each member's midpoint date ``d`` and each basin unit ``u`` the
    pair is ``regime(u, d-1) -> regime(u, d)``; pairs touching an
    unassigned day are skipped (they carry no regime information).
    Only members resolved to a 7-day-or-better interval contribute.
    """
    samples: list[tuple[str, _date]] = []
    units: set[str] = set()
    for ev in members:
        unc = require_finite_seconds(ev.uncertainty_seconds)
        if unc is None or unc > _TRANSITION_UNCERTAINTY_MAX:
            continue
        us = basin_units.get(ev.basin_id, ())
        units.update(us)
        day = _event_midpoint_date(ev)
        if day is None:
            continue
        for u in us:
            samples.append((u, day))
    if not samples or not units:
        return {}, 0.0
    weight = len(units) / float(len(samples))
    counts: Counter[str] = Counter()
    for unit, day in samples:
        prev = artifact.regime_for(unit, (day - timedelta(days=1))
                                   .isoformat())
        cur = artifact.regime_for(unit, day.isoformat())
        if prev is None or cur is None:
            continue
        counts[f"{prev}->{cur}"] += weight
    return dict(counts), float(len(units))


def _control_label_counts(
        artifact: RegimeAssignmentArtifact,
        control: ControlWindowV0) -> dict[str, float]:
    """One control window's regime histogram (unit weight 1 spread
    evenly over its intersected dates)."""
    dates = _control_dates(control)
    if not dates:
        return {}
    weight = 1.0 / len(dates)
    counts: Counter[str] = Counter()
    for day in dates:
        label = artifact.regime_for(control.unit_id, day.isoformat())
        counts[label if label is not None else UNASSIGNED_LABEL] += weight
    return dict(counts)


def _control_transition_counts(
        artifact: RegimeAssignmentArtifact,
        control: ControlWindowV0) -> dict[str, float]:
    dates = _control_dates(control)
    pairs = [(dates[i - 1], dates[i]) for i in range(1, len(dates))]
    if not pairs:
        return {}
    weight = 1.0 / len(pairs)
    counts: Counter[str] = Counter()
    for prev_day, day in pairs:
        prev = artifact.regime_for(control.unit_id, prev_day.isoformat())
        cur = artifact.regime_for(control.unit_id, day.isoformat())
        if prev is None or cur is None:
            continue
        counts[f"{prev}->{cur}"] += weight
    return dict(counts)


def _build_table(
        artifact: RegimeAssignmentArtifact,
        groups: Mapping[str, Sequence[EventLabelV0]],
        controls: Sequence[ControlWindowV0],
        basin_units: Mapping[str, Sequence[str]],
        mode: str) -> dict[str, Any]:
    """Assemble the resampling table consumed by the bootstrap.

    ``groups`` maps group_id -> member events; ``controls`` are the
    already-filtered NEGATIVE controls.  Group strata are basin-aware:
    the sorted basin set of the group's members.
    """
    table_groups: dict[str, dict[str, Any]] = {}
    strata: dict[str, list[str]] = {}
    for gid in sorted(groups):
        members = groups[gid]
        counts, weight = _group_label_counts(
            artifact, members, basin_units, mode)
        basins = _group_basins(members)
        stratum = "|".join(basins) if basins else "UNMAPPED"
        table_groups[gid] = {
            "counts": counts,
            "weight": weight,
            "stratum": stratum,
            "basins": list(basins),
            "season": _group_season(members),
        }
        strata.setdefault(stratum, []).append(gid)
    control_counts: Counter[str] = Counter()
    control_total = 0.0
    for ctl in controls:
        cc = _control_label_counts(artifact, ctl)
        control_counts.update(cc)
        control_total += 1.0
    labels = sorted((set(control_counts)
                     | {l for g in table_groups.values()
                        for l in g["counts"]}) - {UNASSIGNED_LABEL})
    return {
        "groups": table_groups,
        "strata": {s: sorted(ids) for s, ids in strata.items()},
        "control_counts": dict(control_counts),
        "control_total": control_total,
        "event_total": sum(g["weight"] for g in table_groups.values()),
        "labels": labels,
    }


def _build_transition_table(
        artifact: RegimeAssignmentArtifact,
        groups: Mapping[str, Sequence[EventLabelV0]],
        controls: Sequence[ControlWindowV0],
        basin_units: Mapping[str, Sequence[str]]) -> dict[str, Any]:
    table_groups: dict[str, dict[str, Any]] = {}
    strata: dict[str, list[str]] = {}
    for gid in sorted(groups):
        members = groups[gid]
        counts, weight = _group_transition_counts(
            artifact, members, basin_units)
        basins = _group_basins(members)
        stratum = "|".join(basins) if basins else "UNMAPPED"
        table_groups[gid] = {"counts": counts, "weight": weight,
                             "stratum": stratum}
        strata.setdefault(stratum, []).append(gid)
    control_counts: Counter[str] = Counter()
    control_total = 0.0
    for ctl in controls:
        control_counts.update(_control_transition_counts(artifact, ctl))
        control_total += 1.0
    labels = sorted({l for g in table_groups.values()
                     for l in g["counts"]}
                    | set(control_counts))
    return {
        "groups": table_groups,
        "strata": {s: sorted(ids) for s, ids in strata.items()},
        "control_counts": dict(control_counts),
        "control_total": control_total,
        "event_total": sum(g["weight"] for g in table_groups.values()),
        "labels": labels,
    }


# ---------------------------------------------------------------------
# Event-group bootstrap
# ---------------------------------------------------------------------

def _empty_cell() -> dict[str, Any]:
    return {"event_share": 0.0, "control_share": 0.0, "ratio": None,
            "ci_low": None, "ci_high": None, "n_events": 0.0,
            "n_controls": 0.0}


def event_group_bootstrap(
        table_inputs: Mapping[str, Any],
        events: Sequence[EventLabelV0],
        *, n_boot: int = 200, seed: int = 0) -> dict[str, dict]:
    """Deterministic seeded event-group bootstrap.

    Resamples atomic event groups *within basin-aware strata* (with
    replacement, stratum sizes preserved) and returns one cell per
    label: observed shares, enrichment ratio, and a percentile interval
    over ``n_boot`` replicates.  ``table_inputs`` is a mapping produced
    by this module's table builders (``groups`` with per-group
    ``counts``/``weight``/``stratum``, ``control_counts``,
    ``control_total``, ``labels``).  ``events`` is the label sequence
    the table was built from; when non-empty the resampling strata are
    re-derived from it so the routine also works standalone — when
    empty, the strata stored in the table are used.
    """
    groups: Mapping[str, Mapping[str, Any]] = table_inputs.get(
        "groups", {})
    control_counts: Mapping[str, float] = table_inputs.get(
        "control_counts", {})
    control_total = float(table_inputs.get("control_total", 0.0))
    labels = [l for l in table_inputs.get("labels", ())
              if l != UNASSIGNED_LABEL]

    out: dict[str, dict] = {}
    if not labels:
        return out
    if not groups or control_total <= 0.0:
        for label in labels:
            out[label] = _empty_cell()
        return out

    # Resampling strata: re-derived from the label sequence when given,
    # otherwise the stored table strata.
    if events:
        derived = _group_events(list(events))
        strata: dict[str, list[str]] = {}
        for gid, members in derived.items():
            if gid not in groups:
                continue
            basins = _group_basins(members)
            stratum = "|".join(basins) if basins else "UNMAPPED"
            strata.setdefault(stratum, []).append(gid)
        strata = {s: sorted(ids) for s, ids in strata.items()}
    else:
        strata = {str(s): sorted(ids)
                  for s, ids in table_inputs.get("strata", {}).items()}
    if not strata:
        for label in labels:
            out[label] = _empty_cell()
        return out

    event_total = float(table_inputs.get(
        "event_total",
        sum(g["weight"] for g in groups.values())))
    for label in labels:
        e_share = (sum(g["counts"].get(label, 0.0)
                       for g in groups.values())
                   / event_total if event_total > 0 else 0.0)
        c_count = float(control_counts.get(label, 0.0))
        c_share = c_count / control_total
        cell = _empty_cell()
        cell["event_share"] = _round12(e_share)
        cell["control_share"] = _round12(c_share)
        cell["n_events"] = _round12(sum(
            g["counts"].get(label, 0.0) for g in groups.values()))
        cell["n_controls"] = _round12(c_count)
        ratio = _ratio(e_share, c_share)
        cell["ratio"] = _round12(ratio)
        if c_share > 0.0 and n_boot >= 1:
            rng = random.Random(seed)
            reps: list[float] = []
            ordered_strata = sorted(strata)
            for _ in range(int(n_boot)):
                acc: Counter[str] = Counter()
                tot = 0.0
                for s in ordered_strata:
                    ids = strata[s]
                    if not ids:
                        continue
                    for _ in range(len(ids)):
                        gid = ids[rng.randrange(len(ids))]
                        g = groups[gid]
                        acc.update(g["counts"])
                        tot += g["weight"]
                share = (acc.get(label, 0.0) / tot) if tot > 0 else 0.0
                reps.append(share / c_share)
            lo, hi = _percentile_pair(reps)
            cell["ci_low"], cell["ci_high"] = _round12(lo), _round12(hi)
        out[label] = cell
    return out


def _worst_case_ratios(
        artifact: RegimeAssignmentArtifact,
        groups: Mapping[str, Sequence[EventLabelV0]],
        basin_units: Mapping[str, Sequence[str]],
        control_counts: Mapping[str, float],
        control_total: float,
        labels: Sequence[str]) -> dict[str, Optional[float]]:
    """Per-regime adversarial placement: each event group is placed on
    the intersected date minimizing that regime's within-group share
    (ties resolved to the earliest date)."""
    hists = {gid: _group_date_histograms(artifact, members, basin_units)
             for gid, members in groups.items()}
    out: dict[str, Optional[float]] = {}
    for label in labels:
        numer = 0.0
        denom = 0.0
        for gid in sorted(groups):
            hist, n_units = hists[gid]
            if not hist or n_units <= 0:
                continue
            chosen = min(hist, key=lambda d: (hist[d].get(label, 0), d))
            numer += hist[chosen].get(label, 0)
            denom += n_units
        e_share = numer / denom if denom > 0 else 0.0
        c_share = float(control_counts.get(label, 0.0)) / control_total \
            if control_total > 0 else 0.0
        out[label] = _round12(_ratio(e_share, c_share))
    return out


def _point_ratios(table: Mapping[str, Any]) -> dict[str, Optional[float]]:
    control_total = float(table.get("control_total", 0.0))
    event_total = float(table.get("event_total", 0.0))
    out: dict[str, Optional[float]] = {}
    for label in table.get("labels", ()):
        if label == UNASSIGNED_LABEL:
            continue
        e_share = (sum(g["counts"].get(label, 0.0)
                       for g in table["groups"].values())
                   / event_total if event_total > 0 else 0.0)
        c_share = (float(table["control_counts"].get(label, 0.0))
                   / control_total if control_total > 0 else 0.0)
        out[label] = _round12(_ratio(e_share, c_share))
    return out


# ---------------------------------------------------------------------
# Negative controls
# ---------------------------------------------------------------------

def _placebo_control(
        artifact: RegimeAssignmentArtifact,
        controls: Sequence[ControlWindowV0],
        unit_basins: Mapping[str, str],
        n_groups: int, *,
        n_boot: int, seed: int) -> dict[str, Any]:
    """Placebo: a seeded subset of NEGATIVE controls is treated as
    pseudo-events and contrasted against the full control frame.

    No event can occupy these windows, so any regime enrichment here
    indicates frame imbalance rather than association."""
    entries = [(c.control_id, _control_label_counts(artifact, c),
                unit_basins.get(c.unit_id, "UNMAPPED"))
               for c in controls]
    rng = random.Random(seed ^ 0x5EED)
    ids = sorted(e[0] for e in entries)
    pick_n = min(max(n_groups, 0), len(ids))
    picked = set(rng.sample(ids, pick_n)) if pick_n else set()
    pseudo_counts: Counter[str] = Counter()
    pseudo_total = 0.0
    for cid, counts, _basin in entries:
        if cid in picked:
            pseudo_counts.update(counts)
            pseudo_total += 1.0
    all_counts: Counter[str] = Counter()
    for _cid, counts, _basin in entries:
        all_counts.update(counts)
    all_total = float(len(entries))
    labels = sorted((set(all_counts) | set(pseudo_counts))
                    - {UNASSIGNED_LABEL})
    ratios: dict[str, Any] = {}
    for label in labels:
        p_share = (pseudo_counts.get(label, 0.0) / pseudo_total
                   if pseudo_total > 0 else 0.0)
        c_share = (all_counts.get(label, 0.0) / all_total
                   if all_total > 0 else 0.0)
        # Interval over re-draws of the same pseudo-set size.
        rep_ratios: list[float] = []
        if c_share > 0.0 and pick_n > 0 and n_boot >= 1:
            brng = random.Random((seed ^ 0xACE5) + 1)
            for _ in range(int(n_boot)):
                draw = [ids[brng.randrange(len(ids))]
                        for _ in range(pick_n)]
                acc: Counter[str] = Counter()
                for cid, counts, _basin in entries:
                    if cid in draw:
                        acc.update(counts)
                share = acc.get(label, 0.0) / pick_n
                rep_ratios.append(share / c_share)
        lo, hi = (_percentile_pair(rep_ratios) if rep_ratios
                  else (None, None))
        ratios[label] = {
            "ratio": _round12(_ratio(p_share, c_share)),
            "ci_low": _round12(lo), "ci_high": _round12(hi),
        }
    flat = all(v["ci_low"] is None or v["ci_low"] <= 1.0
               for v in ratios.values())
    return {"kind": "placebo", "n_pseudo_windows": pick_n,
            "per_regime": ratios, "flat": flat}


def _impossible_regime_control(
        artifact: RegimeAssignmentArtifact,
        groups: Mapping[str, Sequence[EventLabelV0]],
        controls: Sequence[ControlWindowV0],
        basin_units: Mapping[str, Sequence[str]], *,
        n_boot: int, seed: int) -> dict[str, Any]:
    """Structurally-independent labeling control.

    Every (unit, date) cell is re-labeled by a fixed alternating
    partition (``H0``/``H1`` by unit-index + day parity) that shares no
    structure with the frozen regimes; the same event-group
    association machinery is then rerun.  A partition orthogonal to
    the event labels cannot co-occur with them, so enrichment here
    must sit at ~1.0.
    """
    unit_order = {u: i for i, u in enumerate(sorted(
        {u for us in basin_units.values() for u in us}
        | {c.unit_id for c in controls}))}

    def h_label(unit: str, day: _date) -> str:
        return "H%d" % ((unit_order.get(unit, 0) + day.toordinal()) % 2)

    # Reuse the table shape with hash labels substituted in.
    table_groups: dict[str, dict[str, Any]] = {}
    strata: dict[str, list[str]] = {}
    for gid in sorted(groups):
        members = groups[gid]
        samples: list[tuple[str, _date]] = []
        units: set[str] = set()
        for ev in members:
            us = basin_units.get(ev.basin_id, ())
            units.update(us)
            day = _event_midpoint_date(ev)
            if day is None:
                continue
            for u in us:
                samples.append((u, day))
        counts: Counter[str] = Counter()
        if samples and units:
            w = len(units) / float(len(samples))
            for u, d in samples:
                counts[h_label(u, d)] += w
        basins = _group_basins(members)
        stratum = "|".join(basins) if basins else "UNMAPPED"
        table_groups[gid] = {"counts": dict(counts),
                             "weight": float(len(units)),
                             "stratum": stratum}
        strata.setdefault(stratum, []).append(gid)
    control_counts: Counter[str] = Counter()
    control_total = 0.0
    for ctl in controls:
        dates = _control_dates(ctl)
        if not dates:
            continue
        w = 1.0 / len(dates)
        for d in dates:
            control_counts[h_label(ctl.unit_id, d)] += w
        control_total += 1.0
    table = {
        "groups": table_groups,
        "strata": {s: sorted(ids) for s, ids in strata.items()},
        "control_counts": dict(control_counts),
        "control_total": control_total,
        "event_total": sum(g["weight"] for g in table_groups.values()),
        "labels": ["H0", "H1"],
    }
    cells = event_group_bootstrap(table, [], n_boot=n_boot, seed=seed + 7)
    flat = all(c["ci_low"] is None or c["ci_low"] <= 1.0
               for c in cells.values())
    return {"kind": "impossible_regime", "per_regime": cells,
            "flat": flat}


def _time_reversed_control(
        artifact: RegimeAssignmentArtifact,
        groups: Mapping[str, Sequence[EventLabelV0]],
        controls: Sequence[ControlWindowV0],
        basin_units: Mapping[str, Sequence[str]], *,
        n_boot: int, seed: int) -> dict[str, Any]:
    """Post-event placement analyzed as if it were the event window.

    Each event interval is shifted to the post-event window — the
    ``n`` dates immediately following the last intersected date — and
    sampled at that window's midpoint date.  Symmetric enrichment under
    this reversal indicates contamination; absence of enrichment is
    the expected null shape."""
    table_groups: dict[str, dict[str, Any]] = {}
    strata: dict[str, list[str]] = {}
    for gid in sorted(groups):
        members = groups[gid]
        samples: list[tuple[str, _date]] = []
        units: set[str] = set()
        for ev in members:
            dates = _event_dates(ev)
            if not dates:
                continue
            n = len(dates)
            shifted_mid = dates[-1] + timedelta(days=(n + 1) // 2)
            us = basin_units.get(ev.basin_id, ())
            units.update(us)
            for u in us:
                samples.append((u, shifted_mid))
        counts: Counter[str] = Counter()
        if samples and units:
            w = len(units) / float(len(samples))
            for u, d in samples:
                label = artifact.regime_for(u, d.isoformat())
                counts[label if label is not None
                         else UNASSIGNED_LABEL] += w
        basins = _group_basins(members)
        stratum = "|".join(basins) if basins else "UNMAPPED"
        table_groups[gid] = {"counts": dict(counts),
                             "weight": float(len(units)),
                             "stratum": stratum}
        strata.setdefault(stratum, []).append(gid)
    control_counts: Counter[str] = Counter()
    control_total = 0.0
    for ctl in controls:
        cc = _control_label_counts(artifact, ctl)
        control_counts.update(cc)
        control_total += 1.0
    labels = sorted((set(control_counts)
                     | {l for g in table_groups.values()
                        for l in g["counts"]}) - {UNASSIGNED_LABEL})
    table = {
        "groups": table_groups,
        "strata": {s: sorted(ids) for s, ids in strata.items()},
        "control_counts": dict(control_counts),
        "control_total": control_total,
        "event_total": sum(g["weight"] for g in table_groups.values()),
        "labels": labels,
    }
    cells = event_group_bootstrap(table, [], n_boot=n_boot, seed=seed + 13)
    flat = all(c["ci_low"] is None or c["ci_low"] <= 1.0
               for c in cells.values())
    return {"kind": "time_reversed", "per_regime": cells, "flat": flat}


# ---------------------------------------------------------------------
# Report record
# ---------------------------------------------------------------------

@dataclass(frozen=True)
class AssociationReport:
    """Neutral, associational result record for one frozen regime
    artifact against held-out labels.  ``enrichment`` maps
    ``regime_id -> {event_share, control_share, ratio, ci_low,
    ci_high, n_events, n_controls}``; every interval is an event-group
    bootstrap percentile interval."""

    artifact_id: str
    regime_digest: str
    n_event_windows: int
    n_control_windows: int
    n_event_groups: int
    enrichment: dict
    transitions: dict
    novelty: dict
    negative_controls: dict
    interval_sensitivity: dict
    slices: dict
    status: str
    notes: tuple[str, ...]

    def problems(self) -> list[str]:
        problems: list[str] = []
        if self.status not in ASSOCIATION_STATUSES:
            problems.append(f"status {self.status!r} is not an "
                            "approved association status")
        for name in ("artifact_id", "regime_digest"):
            if not getattr(self, name):
                problems.append(f"{name} is required")
        return problems

    def to_dict(self) -> dict:
        d = asdict(self)
        d["record_type"] = type(self).__name__
        return d


# ---------------------------------------------------------------------
# Holdout binding (fail-closed admission)
# ---------------------------------------------------------------------

def _holdout_binding_problems(
        artifact: RegimeAssignmentArtifact,
        events: Sequence[EventLabelV0],
        controls: Sequence[ControlWindowV0],
        unit_basins: Mapping[str, str],
        holdout: Any,
        region_basins: Any) -> list[str]:
    """Scope which labels and windows may enter the association
    universe at all.

    Every event must (a) appear in ``holdout.event_assignments``,
    (b) map to a locked *test* group — train/validation labels are
    never held-out evidence and their presence rejects — and (c) sit
    in a basin inside the locked evaluation regions named by
    ``region_basins``.  Controls must sit on mapped units inside those
    regions; every unit referenced by controls or by the frozen
    assignments must appear in ``unit_basins``; and every evaluated
    unit must carry at least one frozen assignment row.  The binding
    admits or rejects inputs — it never alters the artifact.
    """
    problems: list[str] = []
    if type(holdout) is not HoldoutPlanV0:
        return ["holdout must be a HoldoutPlanV0 record"]
    problems.extend(f"holdout: {p}" for p in holdout.problems())
    if not holdout.test_locked:
        problems.append("holdout test groups must be locked")
    if len(set(holdout.evaluation_region_names)) < 2:
        problems.append("at least two locked evaluation regions are "
                        "required")

    if not isinstance(region_basins, Mapping):
        problems.append("region_basins must be a mapping of "
                        "evaluation-region name -> basins")
        region_basins = {}
    region_names = set(holdout.evaluation_region_names)
    if set(region_basins.keys()) != region_names:
        problems.append(
            "region_basins keys must equal "
            "holdout.evaluation_region_names exactly "
            f"(region_basins={sorted(region_basins)}, "
            f"evaluation_region_names={sorted(region_names)})")
    eval_basins: set[str] = set()
    for name in sorted(region_names):
        basins = region_basins.get(name)
        if not basins:
            problems.append(f"evaluation region {name!r} maps to no "
                            "basins")
            continue
        for basin in basins:
            eval_basins.add(str(basin))

    train_val = set(holdout.train_groups) | set(
        holdout.validation_groups)
    test_groups = set(holdout.test_groups)
    assignments_map = holdout.event_assignments or {}
    for e in events:
        eid = getattr(e, "event_id", None)
        basin = getattr(e, "basin_id", "")
        grp = assignments_map.get(eid)
        if eid not in assignments_map:
            problems.append(f"event {eid!r} is absent from "
                            "holdout.event_assignments")
        elif grp in train_val:
            problems.append(
                f"event {eid!r} is assigned to non-test group "
                f"{grp!r} — train/validation labels are never "
                "held-out evidence")
        elif grp not in test_groups:
            problems.append(f"event {eid!r} is assigned to "
                            f"undeclared group {grp!r}")
        if basin not in eval_basins:
            problems.append(f"event {eid!r} basin {basin!r} is "
                            "outside the locked evaluation regions")
    for c in controls:
        unit = getattr(c, "unit_id", None)
        cid = getattr(c, "control_id", None)
        if unit not in unit_basins:
            problems.append(f"control {cid!r} references unit "
                            f"{unit!r} which is missing from "
                            "unit_basins")
        elif unit_basins[unit] not in eval_basins:
            problems.append(f"control {cid!r} sits in basin "
                            f"{unit_basins[unit]!r} outside the "
                            "locked evaluation regions")
    artifact_units = {str(r[0]) for r in artifact.assignments
                      if isinstance(r, (tuple, list)) and len(r) == 3}
    for unit in sorted(artifact_units - set(unit_basins)):
        problems.append(f"assignment unit {unit!r} is missing from "
                        "unit_basins — the unit mapping is incomplete")
    evaluated_units = {u for u, b in unit_basins.items()
                       if b in eval_basins}
    for unit in sorted(evaluated_units - artifact_units):
        problems.append(f"evaluated unit {unit!r} has zero regime "
                        "assignments in the artifact")
    return problems


# ---------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------

def run_association(
        artifact: RegimeAssignmentArtifact,
        events: Sequence[EventLabelV0],
        controls: Sequence[ControlWindowV0],
        unit_basins: Mapping[str, str],
        *, holdout: HoldoutPlanV0,
        region_basins: Mapping[str, Collection[str]],
        n_boot: int = 200, seed: int = 0) -> AssociationReport:
    """Run the held-out event–regime association harness.

    The ``holdout``/``region_basins`` binding scopes which labels and
    windows are admitted (locked test groups, named evaluation
    regions); any binding violation raises ``ValueError`` listing all
    problems — fail-closed, no partial association universe.  Pure and
    deterministic: identical inputs plus ``seed`` give a
    byte-identical ``canonical_json(report.to_dict())``.
    """
    binding = _holdout_binding_problems(
        artifact, events, controls, unit_basins, holdout,
        region_basins)
    if binding:
        raise ValueError("association holdout binding rejected: "
                         + "; ".join(binding))
    notes: list[str] = [
        "claim_scope=research_only_no_operational_authorization",
    ]
    art_problems = artifact.problems()
    regime_ids = artifact.regime_ids()

    # Three-valued discipline: only clean adjudicated labels enter
    # positive cells; only derived-NEGATIVE controls enter the control
    # frame.  Everything else is censored and counted, never folded in.
    admissible_events = [
        e for e in events
        if type(e) is EventLabelV0 and not e.problems()
        and e.adjudication_state in POSITIVE_ADMISSIBLE_ADJUDICATION]
    n_censored_events = len(events) - len(admissible_events)
    if n_censored_events:
        notes.append(f"{n_censored_events} event label(s) excluded as "
                     "censored or ambiguous — never counted as "
                     "negatives")
    negative_controls = [
        c for c in controls
        if type(c) is ControlWindowV0 and not c.problems()
        and c.state == TargetState.NEGATIVE.value]
    n_censored_controls = len(controls) - len(negative_controls)
    if n_censored_controls:
        notes.append(f"{n_censored_controls} control window(s) "
                     "excluded as censored or ambiguous — never "
                     "counted as negatives")

    groups = _group_events(admissible_events)
    n_event_groups = len(groups)
    basin_units = _basin_units(unit_basins)

    degenerate = bool(art_problems) or len(regime_ids) < 2
    if art_problems:
        notes.append("regime artifact failed validation: "
                     + "; ".join(art_problems))
    if len(regime_ids) < 2:
        notes.append("regime artifact is degenerate — fewer than two "
                     "distinct regimes (single-regime trivial "
                     "partition)")

    if degenerate:
        return AssociationReport(
            artifact_id=artifact.artifact_id,
            regime_digest=artifact.regime_digest,
            n_event_windows=len(admissible_events),
            n_control_windows=len(negative_controls),
            n_event_groups=n_event_groups,
            enrichment={}, transitions={}, novelty={},
            negative_controls={}, interval_sensitivity={},
            slices={"pooled": {}, "per_basin": {}, "per_season": {}},
            status=STATUS_NOT_SUPPORTED, notes=tuple(notes))

    # --- primary table (midpoint placement) + bootstrap intervals ---
    table = _build_table(artifact, groups, negative_controls,
                         basin_units, "midpoint")
    enrichment = event_group_bootstrap(table, admissible_events,
                                       n_boot=n_boot, seed=seed)

    # --- transition pairs (same resampling machinery) ---
    trans_table = _build_transition_table(artifact, groups,
                                          negative_controls,
                                          basin_units)
    transitions = event_group_bootstrap(trans_table, [],
                                        n_boot=n_boot, seed=seed + 3)

    # --- rarity slice ---
    rare = [r for r, cell in enrichment.items()
            if 0.0 < cell["control_share"] <= NOVELTY_SHARE_MAX]
    novelty = {
        "rare_threshold": NOVELTY_SHARE_MAX,
        "rare_regimes": sorted(rare),
        "enrichment": {r: enrichment[r] for r in sorted(rare)},
    }

    # --- negative controls ---
    neg = {
        "placebo": _placebo_control(
            artifact, negative_controls, unit_basins,
            n_event_groups, n_boot=n_boot, seed=seed),
        "impossible_regime": _impossible_regime_control(
            artifact, groups, negative_controls, basin_units,
            n_boot=n_boot, seed=seed),
        "time_reversed": _time_reversed_control(
            artifact, groups, negative_controls, basin_units,
            n_boot=n_boot, seed=seed),
    }
    all_flat = all(neg[k]["flat"] for k in
                   ("placebo", "impossible_regime", "time_reversed"))

    # --- interval-placement sensitivity ---
    uniform_table = _build_table(artifact, groups, negative_controls,
                                 basin_units, "uniform")
    sensitivity = {
        "midpoint": _point_ratios(table),
        "uniform": _point_ratios(uniform_table),
        "worst_case": _worst_case_ratios(
            artifact, groups, basin_units,
            table["control_counts"], table["control_total"],
            table["labels"]),
    }

    # --- slices ---
    per_basin: dict[str, Any] = {}
    basins = sorted({b for g in table["groups"].values()
                     for b in g["basins"]})
    for basin in basins:
        sub_groups = {gid: m for gid, m in groups.items()
                      if _group_basins(m) == (basin,)}
        sub_controls = [c for c in negative_controls
                        if unit_basins.get(c.unit_id) == basin]
        sub_events = [e for gid in sub_groups for e in sub_groups[gid]]
        sub_table = _build_table(artifact, sub_groups, sub_controls,
                                 basin_units, "midpoint")
        per_basin[basin] = event_group_bootstrap(
            sub_table, sub_events, n_boot=n_boot, seed=seed)
    per_season: dict[str, Any] = {}
    seasons = sorted({g["season"] for g in table["groups"].values()}
                     - {"UNKNOWN"})
    for season in seasons:
        sub_groups = {gid: m for gid, m in groups.items()
                      if _group_season(m) == season}
        sub_controls = [
            c for c in negative_controls
            if _control_dates(c)
            and _season_of(_control_dates(c)[len(_control_dates(c))
                                           // 2]) == season]
        sub_events = [e for gid in sub_groups for e in sub_groups[gid]]
        sub_table = _build_table(artifact, sub_groups, sub_controls,
                                 basin_units, "midpoint")
        per_season[season] = event_group_bootstrap(
            sub_table, sub_events, n_boot=n_boot, seed=seed)
    slices = {"pooled": enrichment, "per_basin": per_basin,
              "per_season": per_season}

    # --- decision rule ---
    enriched = [r for r, cell in enrichment.items()
                if cell["ci_low"] is not None and cell["ci_low"] > 1.0]
    direction_ok = all(
        (sensitivity["uniform"].get(r) is not None
         and sensitivity["uniform"][r] > 1.0
         and sensitivity["worst_case"].get(r) is not None
         and sensitivity["worst_case"][r] > 1.0)
        for r in enriched)
    if n_event_groups < MIN_EVENT_GROUPS:
        status = STATUS_UNDERPOWERED
        notes.append(f"{n_event_groups} atomic event groups is below "
                     f"the MIN_EVENT_GROUPS={MIN_EVENT_GROUPS} floor — "
                     "descriptive output only")
    elif enriched and all_flat and direction_ok:
        status = STATUS_SUPPORTED
        notes.append("one or more frozen regimes show a non-random "
                     "correspondence with held-out adjudicated events "
                     "under predeclared associational tests — an "
                     "association result only")
    else:
        status = STATUS_DESCRIPTIVE
        if not enriched:
            notes.append("no regime interval lies entirely above 1.0 "
                         "— descriptive correspondence only")
        if not all_flat:
            notes.append("a negative control shows non-flat "
                         "enrichment — association cannot be "
                         "supported")
        if enriched and not direction_ok:
            notes.append("interval-placement sensitivity reverses or "
                         "collapses the enrichment direction")

    return AssociationReport(
        artifact_id=artifact.artifact_id,
        regime_digest=artifact.regime_digest,
        n_event_windows=len(admissible_events),
        n_control_windows=len(negative_controls),
        n_event_groups=n_event_groups,
        enrichment=enrichment, transitions=transitions,
        novelty=novelty, negative_controls=neg,
        interval_sensitivity=sensitivity, slices=slices,
        status=status, notes=tuple(notes))


# ---------------------------------------------------------------------
# Associational prose
# ---------------------------------------------------------------------

def association_report_text(report: AssociationReport) -> str:
    """Render the report as associational prose only.

    Every sentence is descriptive co-occurrence language; the claim
    scanner must return ``[]`` on this output.
    """
    lines = [
        f"Association report {report.artifact_id} "
        f"(regime digest {report.regime_digest[:12]}...).",
        f"Status: {report.status}.",
        "Scope: a frozen, label-blind retrospective regime partition "
        "set against held-out adjudicated event labels and matched "
        "derived control windows; descriptive research correspondence "
        "only.",
        f"Universe: {report.n_event_windows} event windows, "
        f"{report.n_control_windows} negative control windows, "
        f"{report.n_event_groups} atomic event groups.",
    ]
    if report.enrichment:
        lines.append("Regime enrichment (event share / control share, "
                     "event-group bootstrap interval):")
        for rid in sorted(report.enrichment):
            cell = report.enrichment[rid]
            ratio = cell["ratio"]
            lo, hi = cell["ci_low"], cell["ci_high"]
            if ratio is None:
                lines.append(
                    f"  - regime {rid}: control share is zero; ratio "
                    "undefined.")
                continue
            ci = ("[" + (f"{lo:.3f}" if lo is not None else "n/a")
                  + ", "
                  + (f"{hi:.3f}" if hi is not None else "n/a") + "]")
            direction = ("is enriched under"
                         if lo is not None and lo > 1.0 else
                         "is not enriched under")
            lines.append(
                f"  - regime {rid} {direction} the frozen partition: "
                f"event share {cell['event_share']:.3f}, control "
                f"share {cell['control_share']:.3f}, ratio "
                f"{ratio:.3f} {ci} — the regime co-occurs with event "
                "windows relative to matched controls.")
    if report.transitions:
        top = sorted(report.transitions.items(),
                     key=lambda kv: kv[0])[:5]
        lines.append("Regime transition-pair correspondence "
                     "(descriptive trajectory statistic):")
        for pair, cell in top:
            ratio = cell["ratio"]
            lines.append(
                f"  - {pair}: ratio "
                + (f"{ratio:.3f}" if ratio is not None else "undefined")
                + " pre-event versus controls.")
    neg = report.negative_controls
    if neg:
        parts = []
        for key in ("placebo", "impossible_regime", "time_reversed"):
            entry = neg.get(key)
            if entry:
                parts.append(f"{key}={'flat' if entry['flat'] else 'non-flat'}")
        lines.append("Negative controls: " + "; ".join(parts) + ".")
    if report.interval_sensitivity:
        lines.append("Interval-placement sensitivity (midpoint / "
                     "uniform / worst-case) is reported per regime in "
                     "the structured record.")
    if report.slices:
        lines.append("Slices: pooled, per-basin, and per-season "
                     "correspondences are reported in the structured "
                     "record.")
    if report.notes:
        lines.append("Notes:")
        for note in report.notes:
            lines.append(f"  - {note}")
    lines.append(
        "This is a retrospective association result on held-out "
        "basins; it carries no causal, anticipatory, or authority "
        "interpretation.")
    return "\n".join(lines)


__all__ = [
    "ASSOCIATION_STATUSES",
    "AssociationReport",
    "MIN_EVENT_GROUPS",
    "NOVELTY_SHARE_MAX",
    "PLACEMENT_MODES",
    "RegimeAssignmentArtifact",
    "STATUS_DESCRIPTIVE",
    "STATUS_NOT_SUPPORTED",
    "STATUS_SUPPORTED",
    "STATUS_UNDERPOWERED",
    "UNASSIGNED_LABEL",
    "association_report_text",
    "event_group_bootstrap",
    "run_association",
]
