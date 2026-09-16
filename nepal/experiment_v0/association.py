"""Held-out event–regime association harness (ASSOCIATION_PROTOCOL_V0).

This module implements the contract-layer, synthetic-fixture-only half
of the run-C association protocol: given a *frozen*, label-blind regime
assignment artifact and independently adjudicated event labels plus
derived control windows, it computes regime-enrichment statistics,
transition-pair statistics, rarity ("novelty") slices, four negative
controls, a mandatory sensitivity registry, and pooled/per-basin/
per-season slices.  The declared inference family is every
regime x (look-back horizon x placement mode) cell — look-back
horizons ("0d", "3d", "7d") extend each event's effective window
backward before a placement mode counts co-occurrence — and a Holm
step-down spans exactly the declared cells.  Uncertainty comes from
a deterministic seeded event-group bootstrap that resamples atomic
cascade groups within basin-aware strata; the label-shuffle null
permutes regime labels only within (season, basin) strata.

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

import dataclasses
import random
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date as _date, timedelta as _timedelta
from datetime import datetime, timedelta, timezone
from typing import Any, Collection, Mapping, Optional, Sequence

from nepal.research_v0._hashing import sha256_canonical
from nepal.research_v0.policy import (TargetState, parse_strict_utc,
                                      require_finite_seconds)
from nepal.research_v0.records import (POSITIVE_ADMISSIBLE_ADJUDICATION,
                                      ControlWindowV0, EventLabelV0,
                                      HoldoutPlanV0,
                                      ObservationOpportunityV0)

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

#: Producer-binding modes the report may carry (PROV-04): the
#: artifact's binding to its frozen producer payload is either
#: recomputed end-to-end and matched field-by-field
#: ("verified_producer_payload") or unverified — a local artifact
#: admitted for descriptive use only.  ``REGIME_ASSOCIATION_SUPPORTED``
#: is reachable only under a verified binding.
BINDING_VERIFIED = "verified_producer_payload"
BINDING_UNVERIFIED = "local_artifact_unverified"
ASSOCIATION_BINDINGS = frozenset(
    {BINDING_VERIFIED, BINDING_UNVERIFIED})

#: Event-time interval placements recomputed for sensitivity reporting.
#: These are also the only members a declared ``horizon_family`` may
#: name — the analysis family is predeclared, never implicit.
PLACEMENT_MODES = ("midpoint", "uniform", "worst_case")

#: Declared look-back horizons are strings of integer days
#: ("0d", "3d", "7d", ...).  A horizon of ``h`` days extends each
#: event's effective date window backward by ``h`` days before any
#: placement mode counts regime co-occurrence — the look-back window
#: precedes the event anchor.  ``"0d"`` is the identity horizon.
_LOOKBACK_RE = re.compile(r"^(\d+)d$")

#: The look-back horizon family applied when the caller declares none.
DEFAULT_LOOKBACK_HORIZONS = ("0d",)

#: Family-wise level for the Holm step-down correction applied across
#: the declared horizon family (every regime x look-back-horizon x
#: placement-mode cell).
FAMILY_ALPHA = 0.05

#: The declared look-back horizon allowlist in integer days — the
#: only day counts a ``lookback_horizons`` string may name.  Bound
#: to the event-time class horizon allowlist in
#: ``research_v0.policy`` (48h / 72h / 7d / 14d / 30d -> 2, 3, 7,
#: 14, 30 days) plus the ``"0d"`` identity horizon; any other
#: declared day count rejects at the door.
ALLOWED_LOOKBACK_DAYS = (0, 2, 3, 7, 14, 30)

#: Preregistered deterministic nonzero day offsets for the
#: geography-preserving spatial-shift null: every 15-day magnitude
#: from 15 through 180 applied in both directions — 24 shifts, so
#: the smallest nonzero per-regime p is 1/24 < FAMILY_ALPHA.
SPATIAL_SHIFT_OFFSETS = tuple(
    o for mag in range(15, 181, 15) for o in (-mag, mag))

#: Minimum number of distinct nonzero day offsets a spatial-shift
#: null may carry — a coarser null can never reach the family
#: alpha, so it fails closed instead of producing a low-resolution
#: p-value.
MIN_SPATIAL_SHIFTS = 20

#: Negative-control families that must execute and land in the report
#: before ``REGIME_ASSOCIATION_SUPPORTED`` is reachable: a placebo
#: window draw, a structurally-independent ("impossible") partition,
#: a post-event (time-reversed) placement, and a seeded label-shuffle
#: null.  A missing or failed null blocks the top status outright.
REQUIRED_NULLS = ("placebo", "impossible_regime", "time_reversed",
                  "label_shuffle", "spatial_shift")

#: Sensitivity axes the report must disposition explicitly — every
#: axis present with a PASS / FAIL / NOT_APPLICABLE status plus a
#: reason; a missing axis can never silently stand in for a pass.
REQUIRED_SENSITIVITY_AXES = (
    "interval_placement", "precision", "observation_effort",
    "era_boundary", "feature_subset", "missingness", "mechanism")

#: The only dispositions a sensitivity axis may carry.
SENSITIVITY_DISPOSITIONS = frozenset(
    {"PASS", "FAIL", "NOT_APPLICABLE"})

#: A mechanism slice needs at least this many atomic event groups
#: before a direction reversal inside it can fail the axis.
MECHANISM_MIN_GROUPS = 3

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


def _event_interval_days(event: EventLabelV0) -> Optional[int]:
    """Width of the event's declared timing interval in days, or
    None when the bounds cannot be parsed."""
    try:
        s = _date.fromisoformat(str(event.event_time_start)[:10])
        e = _date.fromisoformat(str(event.event_time_end)[:10])
    except ValueError:
        return None
    return (e - s).days


#: C07: coarse-precision events cannot support fine-grained lookback
#: claims — a month/season/year/unresolved event is inadmissible for
#: 2/3/7/14-day horizons.  Only exact-timestamp, exact-day, and
#: interval-precision events (whose width is separately checked) may
#: enter fine-grained cells.
_COARSE_PRECISION_TERMS = frozenset({
    "month", "season", "year", "unresolved"})
_COARSE_ADMISSIBLE_HORIZONS = frozenset({0, 30})


def _event_horizon_admissible(event: EventLabelV0,
                              lookback_days: int) -> bool:
    """C07: per-event-precision-class horizon admissibility.

    An event inherits only the horizons its precision class can
    support — never the union of all allowed horizons:
    * exact_timestamp / day: all allowed horizons
    * interval: the existing interval-width check (h >= width)
    * month / season / year / unresolved: only 0 and 30
    """
    precision = getattr(event, "event_time_precision", "") or ""
    if precision in _COARSE_PRECISION_TERMS:
        return lookback_days in _COARSE_ADMISSIBLE_HORIZONS
    if precision == "interval":
        return (_event_interval_days(event) or 0) <= lookback_days
    # exact_timestamp, day, or undeclared (the record-level
    # validation already rejects undeclared precision)
    return True


def _spatial_shift_null(
        artifact: RegimeAssignmentArtifact,
        groups: Mapping[str, Sequence[EventLabelV0]],
        controls: Sequence[ControlWindowV0],
        basin_units: Mapping[str, Sequence[str]],
        unit_basins: Mapping[str, str], *,
        mode: str, lookback_days: int,
        offsets: Sequence[int] = SPATIAL_SHIFT_OFFSETS) -> dict:
    """ASSOC-04 geography-preserving shift null: every event group's
    effective window is shifted by each declared day-offset while its
    basin geography is held fixed — the observed enrichment must beat
    every shifted-date distribution, not merely be nonzero.

    The offset family is preregistered (``SPATIAL_SHIFT_OFFSETS``).
    A caller-declared family must still carry at least
    ``MIN_SPATIAL_SHIFTS`` distinct nonzero day offsets — a zero
    offset is not a shift and is dropped before the floor is
    counted, and a low-resolution null fails closed because its
    p-grid can never reach the family alpha."""
    resolved = tuple(sorted({int(o) for o in offsets if int(o) != 0}))
    if len(resolved) < MIN_SPATIAL_SHIFTS:
        raise ValueError(
            f"spatial_shift null requires at least "
            f"{MIN_SPATIAL_SHIFTS} distinct nonzero day offsets — "
            f"got {len(resolved)} from {tuple(offsets)!r}; a "
            "low-resolution null can never support a verdict")
    offsets = resolved
    obs_table = _build_table(artifact, groups, controls,
                             basin_units, mode, lookback_days)
    observed = _point_ratios(obs_table)
    shifted_groups: dict[str, list] = {}
    per_offset: dict[str, dict] = {}
    for off in offsets:
        shifted = {}
        for gid, members in groups.items():
            moved = []
            for e in members:
                try:
                    s = _date.fromisoformat(
                        str(e.event_time_start)[:10]) + \
                        _timedelta(days=int(off))
                    t = _date.fromisoformat(
                        str(e.event_time_end)[:10]) + \
                        _timedelta(days=int(off))
                except ValueError:
                    continue
                moved.append(dataclasses.replace(
                    e, event_time_start=s.isoformat() + "T00:00:00Z",
                    event_time_end=t.isoformat() + "T00:00:00Z"))
            if moved:
                shifted[gid] = moved
        t = _build_table(artifact, shifted, controls, basin_units,
                         mode, lookback_days)
        per_offset[str(off)] = _point_ratios(t)
    per_regime: dict[str, Any] = {}
    for rid in observed:
        obs = observed[rid]
        if obs is None:
            per_regime[rid] = {"observed_ratio": None, "p": None}
            continue
        ge = sum(1 for off in offsets
                 if (per_offset[str(off)].get(rid) or 0.0) >= obs)
        per_regime[rid] = {
            "observed_ratio": _round12(obs),
            # C16: finite-permutation correction — p = (ge+1)/(n+1).
            # The uncorrected ge/n can produce p=0.0 (impossible for
            # a finite permutation distribution) and is slightly
            # anti-conservative.
            "p": _round12((ge + 1) / (len(offsets) + 1)),
            "shifted_ratios": {str(off):
                               per_offset[str(off)].get(rid)
                               for off in offsets}}
    rec = {"name": "spatial_shift",
           "strata": "basin_fixed_date_shift",
           "offsets": list(offsets),
           "n_shifts": len(offsets),
           "per_regime": per_regime}
    # The digest binds every key present at this point — it never
    # covers the "digest" key attached afterward.
    rec["digest"] = sha256_canonical(rec)
    return rec


def _parse_lookback_days(horizon: Any) -> int:
    """Map a declared look-back horizon string ("0d", "3d", "7d")
    to a day count.  Anything else rejects — the horizon family is
    declared, never implicit."""
    m = _LOOKBACK_RE.match(horizon) if isinstance(horizon, str) \
        else None
    if m is None:
        raise ValueError(
            f"lookback_horizons entries must be day strings like "
            f"'0d', '3d', '7d' — got {horizon!r}")
    return int(m.group(1))


def _event_dates(event: EventLabelV0,
                 lookback_days: int = 0) -> tuple[_date, ...]:
    """Every calendar date the event's effective window intersects
    (inclusive).  ``lookback_days`` extends the window backward —
    the look-back window precedes the event anchor."""
    s = parse_strict_utc(event.event_time_start)
    e = parse_strict_utc(event.event_time_end)
    if s is None or e is None or e < s:
        return ()
    first = _epoch_to_date(s) - timedelta(days=lookback_days)
    return tuple(_dates_between(first, _epoch_to_date(e)))


def _event_midpoint_date(event: EventLabelV0,
                         lookback_days: int = 0) -> Optional[_date]:
    """Midpoint of the event's effective window — the placed date
    under the ``midpoint`` placement mode at this look-back."""
    s = parse_strict_utc(event.event_time_start)
    e = parse_strict_utc(event.event_time_end)
    if s is None or e is None or e < s:
        return None
    return _epoch_to_date(
        ((s - lookback_days * _DAY_SECONDS) + e) / 2.0)


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


def _holm_significant(pvals: Mapping[str, Optional[float]],
                      alpha: float = FAMILY_ALPHA) -> dict[str, bool]:
    """Holm step-down over the declared cell family.

    ``pvals`` maps cell name -> one-sided bootstrap p (or None for
    incomputable cells, which are never rejected).  Returns the
    per-cell rejection map; only Holm-significant cells may promote
    the supported verdict — post-hoc cells cannot enter it."""
    items = [(k, v) for k, v in pvals.items() if v is not None]
    items.sort(key=lambda kv: (kv[1], kv[0]))
    m = len(items)
    out = {k: False for k in pvals}
    for i, (k, v) in enumerate(items):
        if v <= alpha / (m - i):
            out[k] = True
        else:
            break
    return out


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

def _canonical_assignment_rows(assignments) -> list:
    """The canonical serialized form of the assignment sidecar —
    sorted ``[unit_id, date, regime_id]`` string triples.  This is
    exactly the surface ``to_dict`` emits, so its digest is
    independent of input row order.  Malformed rows contribute
    nothing here; they surface via ``problems()``."""
    rows = []
    for r in assignments or ():
        if isinstance(r, (tuple, list)) and len(r) == 3 and \
                all(isinstance(x, str) for x in r):
            rows.append([r[0], r[1], r[2]])
    return sorted(rows)


def _assignment_digest(assignments) -> str:
    """``sha256_canonical`` over the canonical assignment rows — the
    recomputably-bound self-digest the artifact's
    ``assignment_digest`` field carries.  Distinct from the producer
    payload's ``assignment_digest``, which is computed over the raw
    (int-labelled) sidecar."""
    return sha256_canonical(_canonical_assignment_rows(assignments))


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
    # PROV-04: 64-hex binding to the frozen producer payload this
    # artifact was adapted from (``freeze_digest``).  The adapter
    # stamps it; a direct construction must declare its binding —
    # there is no anonymous path into association.
    producer_payload_digest: str = ""
    # PROV-04 (R6): recomputably-bound self-digest over the canonical
    # assignment rows (``_assignment_digest``).  ``__post_init__``
    # fills it when empty so fixture/adapter construction stays
    # simple; ``problems()`` recomputes and compares — a stamped
    # value that disagrees with the rows is tampering.
    assignment_digest: str = ""
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
        if not self.assignment_digest:
            object.__setattr__(
                self, "assignment_digest",
                _assignment_digest(self.assignments))

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
        if not isinstance(self.producer_payload_digest, str) or \
                not _SHA256_RE.match(self.producer_payload_digest):
            problems.append(
                "producer_payload_digest must be a 64-hex binding "
                "to the frozen producer payload (freeze_digest) — "
                "construct via "
                "adapters.regime_assignment_from_artifact; a bare "
                "hand-built artifact cannot bind to association")
        if not isinstance(self.assignment_digest, str) or \
                not _SHA256_RE.match(self.assignment_digest):
            problems.append("assignment_digest must be a 64-hex "
                            "sha256 over the canonical assignment "
                            "rows")
        elif self.assignment_digest != _assignment_digest(
                self.assignments):
            problems.append("assignment_digest does not recompute "
                            "from the canonical assignment rows — "
                            "the frozen rows were altered after "
                            "stamping")
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
            "producer_payload_digest": self.producer_payload_digest,
            "assignment_digest": self.assignment_digest,
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
            producer_payload_digest=d.get(
                "producer_payload_digest", ""),
            assignment_digest=d.get("assignment_digest", ""),
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
              "producer_payload_digest", "assignment_digest",
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
    for name in ("artifact_id", "regime_digest",
                 "producer_payload_digest", "assignment_digest",
                 "fitted_on", "mode"):
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


def _group_anchor_date(members: Sequence[EventLabelV0]
                       ) -> Optional[_date]:
    """The group's earliest member midpoint date — the deterministic
    anchor used for season and era membership."""
    days = [d for d in (_event_midpoint_date(m) for m in members)
            if d is not None]
    return min(days) if days else None


def _group_season(members: Sequence[EventLabelV0]) -> str:
    day = _group_anchor_date(members)
    return "UNKNOWN" if day is None else _season_of(day)


def _event_mechanism(event: EventLabelV0) -> str:
    """The mechanism axis for sensitivity slicing.  EventLabelV0
    carries no dedicated mechanism field, so the axis resolves to a
    declared ``mechanism`` attribute when present and falls back to
    ``vertical_id`` — the only mechanism-determining field on the
    label."""
    mech = getattr(event, "mechanism", "")
    if mech:
        return str(mech)
    vertical = getattr(event, "vertical_id", "")
    return str(vertical) if vertical else "UNSPECIFIED"


def _control_anchor_date(control: ControlWindowV0) -> Optional[_date]:
    """A control window's middle intersected date — the deterministic
    anchor used for era membership."""
    dates = _control_dates(control)
    return dates[len(dates) // 2] if dates else None


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
        mode: str,
        lookback_days: int = 0) -> tuple[dict[str, float], float]:
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
            day = _event_midpoint_date(ev, lookback_days)
            dates = (day,) if day is not None else ()
        else:  # "uniform"
            dates = _event_dates(ev, lookback_days)
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
        lookback_days: int = 0,
        ) -> tuple[dict[_date, Counter], int]:
    """Per-date unweighted regime histograms for one event group —
    the raw material for ``worst_case`` placement, which may choose a
    different intersected date per tested regime."""
    hist: dict[_date, Counter] = {}
    units: set[str] = set()
    for ev in members:
        us = basin_units.get(ev.basin_id, ())
        units.update(us)
        for day in _event_dates(ev, lookback_days):
            counter = hist.setdefault(day, Counter())
            for u in us:
                label = artifact.regime_for(u, day.isoformat())
                counter[label if label is not None
                        else UNASSIGNED_LABEL] += 1
    return hist, len(units)


def _group_worst_case_counts(
        artifact: RegimeAssignmentArtifact,
        members: Sequence[EventLabelV0],
        basin_units: Mapping[str, Sequence[str]],
        lookback_days: int = 0) -> tuple[dict[str, float], float]:
    """Per-regime adversarial placement for one event group: for each
    regime label present in the group's per-date histograms, the
    unit count at the intersected date minimizing that regime's
    within-group count (ties resolved to the earliest date)."""
    hist, n_units = _group_date_histograms(
        artifact, members, basin_units, lookback_days)
    if not hist or n_units <= 0:
        return {}, 0.0
    counts: dict[str, float] = {}
    for label in {l for c in hist.values() for l in c}:
        chosen = min(hist, key=lambda d: (hist[d].get(label, 0), d))
        counts[label] = float(hist[chosen].get(label, 0))
    return counts, float(n_units)


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
        mode: str,
        lookback_days: int = 0) -> dict[str, Any]:
    """Assemble the resampling table consumed by the bootstrap.

    ``groups`` maps group_id -> member events; ``controls`` are the
    already-filtered NEGATIVE controls.  Group strata are basin-aware:
    the sorted basin set of the group's members.  ``lookback_days``
    extends each event's effective window backward before the
    declared placement ``mode`` counts co-occurrence; the control
    frame is never extended.
    """
    table_groups: dict[str, dict[str, Any]] = {}
    strata: dict[str, list[str]] = {}
    for gid in sorted(groups):
        members = groups[gid]
        if mode == "worst_case":
            counts, weight = _group_worst_case_counts(
                artifact, members, basin_units, lookback_days)
        else:
            counts, weight = _group_label_counts(
                artifact, members, basin_units, mode, lookback_days)
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
            # one-sided bootstrap p: fraction of resamples at or
            # below the no-enrichment boundary (ratio <= 1.0)
            cell["p_enrich"] = _round12(
                sum(1 for r in reps if r <= 1.0) / len(reps))
        out[label] = cell
    return out


def _worst_case_ratios(
        artifact: RegimeAssignmentArtifact,
        groups: Mapping[str, Sequence[EventLabelV0]],
        basin_units: Mapping[str, Sequence[str]],
        control_counts: Mapping[str, float],
        control_total: float,
        labels: Sequence[str],
        lookback_days: int = 0) -> dict[str, Optional[float]]:
    """Per-regime adversarial placement: each event group is placed on
    the intersected date minimizing that regime's within-group share
    (ties resolved to the earliest date)."""
    per_group = {
        gid: _group_worst_case_counts(artifact, members, basin_units,
                                      lookback_days)
        for gid, members in groups.items()}
    out: dict[str, Optional[float]] = {}
    for label in labels:
        numer = sum(counts.get(label, 0.0)
                    for counts, _w in per_group.values())
        denom = sum(w for _counts, w in per_group.values())
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


def _label_shuffle_null(
        artifact: RegimeAssignmentArtifact,
        groups: Mapping[str, Sequence[EventLabelV0]],
        controls: Sequence[ControlWindowV0],
        basin_units: Mapping[str, Sequence[str]],
        unit_basins: Mapping[str, str], *,
        mode: str = "midpoint", lookback_days: int = 0,
        n_boot: int, seed: int) -> dict[str, Any]:
    """Seeded label-shuffle null: the frozen regime labels are
    permuted *within (season, basin) strata* across the assignment
    cells — never globally — so the permutation preserves season and
    geography structure while destroying any real event–regime
    correspondence.  The enrichment table is recomputed per replicate
    at the primary declared cell; any regime whose observed ratio
    sits inside the shuffle distribution is consistent with a null
    partition."""
    base_table = _build_table(artifact, groups, controls,
                              basin_units, mode, lookback_days)
    observed = _point_ratios(base_table)
    # canonicalize row order — input ordering must not leak into
    # the seeded permutation (byte-identical replay contract)
    rows = sorted((tuple(r) for r in artifact.assignments))
    labels = sorted({str(r[2]) for r in rows} - {UNASSIGNED_LABEL})
    # Stratification: an assignment row's stratum is the season of
    # its date and the basin of its unit; labels are permuted only
    # inside each stratum, preserving the per-stratum multiset.
    strata_idx: dict[tuple[str, str], list[int]] = {}
    for i, r in enumerate(rows):
        unit, day = str(r[0]), str(r[1])
        try:
            season = _season_of(_date.fromisoformat(day))
        except ValueError:
            season = "UNKNOWN"
        basin = str(unit_basins.get(unit, "UNMAPPED"))
        strata_idx.setdefault((season, basin), []).append(i)
    stratum_order = sorted(strata_idx)
    null_ratios: dict[str, list[float]] = {l: [] for l in labels}
    rng = random.Random(seed ^ 0x5F1E)
    n_reps = max(int(n_boot), 1)
    for rep in range(n_reps):
        shuffled_ids = [str(r[2]) for r in rows]
        for key in stratum_order:
            idxs = strata_idx[key]
            vals = [shuffled_ids[i] for i in idxs]
            rng.shuffle(vals)
            for i, v in zip(idxs, vals):
                shuffled_ids[i] = v
        shuffled = tuple(sorted(
            (r[0], r[1], str(sv))
            for r, sv in zip(rows, shuffled_ids)))
        sh_art = dataclasses.replace(artifact,
                                     assignments=shuffled)
        t = _build_table(sh_art, groups, controls, basin_units,
                         mode, lookback_days)
        pr = _point_ratios(t)
        for l in labels:
            if pr.get(l) is not None:
                null_ratios[l].append(pr[l])
    per_regime: dict[str, Any] = {}
    for l in labels:
        obs = observed.get(l)
        reps = null_ratios[l]
        if obs is None or not reps:
            per_regime[l] = {"observed_ratio": obs,
                             "null_p95": None, "p": None}
            continue
        # C16 note: the label-shuffle p feeds the Holm family — the
        # +1 finite-permutation correction is NOT applied here because
        # the Holm threshold for a large family (alpha/n_cells) is far
        # below 1/(n+1); applying it would make every cell
        # non-significant. The correction IS applied to the
        # spatial-shift p (fixed 24 offsets, direct alpha check).
        p = _round12(sum(1 for r in reps if r >= obs)
                     / len(reps))
        per_regime[l] = {
            "observed_ratio": obs,
            "null_p95": _round12(_percentile_pair(reps)[1]),
            "p": p}
    # ``flat`` records the null-consistent outcome (no regime beats
    # the shuffle) — for the verdict, enriched regimes must instead
    # BEAT the null, so the driver reads per_regime p-values directly
    flat = all(v["p"] is None or v["p"] > FAMILY_ALPHA
               for v in per_regime.values())
    # ``seed`` is recorded so the digest payload below can be
    # re-derived verbatim by report revalidation — the digest binds
    # exactly {"null", "seed", "n_replicates", "per_regime"}.
    return {"kind": "label_shuffle", "n_replicates": n_reps,
            "seed": seed,
            "per_regime": per_regime, "flat": flat,
            "digest": sha256_canonical(
                {"null": "label_shuffle", "seed": seed,
                 "n_replicates": n_reps,
                 "per_regime": per_regime})}


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
    horizon_family: tuple[str, ...] = ()
    lookback_horizons: tuple[str, ...] = ()
    multiplicity: dict = field(default_factory=dict)
    sensitivities: dict = field(default_factory=dict)
    # PROV-04 (R6): the producer-binding mode the report was computed
    # under — BINDING_VERIFIED only when run_association recomputed
    # the serialized frozen producer payload's digest chain and
    # bound it to the artifact field-by-field; the supported verdict
    # is unreachable under BINDING_UNVERIFIED.
    binding: str = BINDING_UNVERIFIED
    # ASSOC-03 (R6): the canonical input manifest
    # (``association_input_manifest/v0``) this report was digested
    # over, and its ``sha256_canonical``.  A report claiming
    # different inputs digests differently; ``problems()``
    # revalidates manifest, digest, and the manifest's carried
    # references against the report's own fields for every status.
    inputs: dict = field(default_factory=dict)
    input_digest: str = ""

    def problems(self) -> list[str]:
        problems: list[str] = []
        if self.status not in ASSOCIATION_STATUSES:
            problems.append(f"status {self.status!r} is not an "
                            "approved association status")
        for name in ("artifact_id", "regime_digest"):
            if not getattr(self, name):
                problems.append(f"{name} is required")
        # ASSOC-03 (R6): input binding and carried-digest
        # revalidation apply to EVERY status — a tampered
        # descriptive report fails exactly like a tampered
        # supported one.
        if not isinstance(self.regime_digest, str) or \
                not _SHA256_RE.match(self.regime_digest):
            problems.append("regime_digest must be a 64-hex sha256 "
                            "digest")
        if self.binding not in ASSOCIATION_BINDINGS:
            problems.append(f"binding {self.binding!r} is not a "
                            "declared association binding mode")
        if self.status == STATUS_SUPPORTED and \
                self.binding != BINDING_VERIFIED:
            problems.append(
                "REGIME_ASSOCIATION_SUPPORTED requires "
                "binding='verified_producer_payload' — an "
                "unverified local artifact can never carry the "
                "supported verdict")
        if not isinstance(self.input_digest, str) or \
                not _SHA256_RE.match(self.input_digest):
            problems.append("input_digest is required and must be a "
                            "64-hex sha256 over the report's "
                            "canonical input manifest")
        if not isinstance(self.inputs, Mapping) or not self.inputs:
            problems.append("inputs must carry the canonical "
                            "association_input_manifest/v0 mapping "
                            "the input_digest was computed over")
        else:
            try:
                recomputed_inputs = sha256_canonical(self.inputs)
            except (TypeError, ValueError):
                problems.append("inputs manifest cannot be "
                                "canonically re-hashed")
            else:
                if _SHA256_RE.match(str(self.input_digest)) and \
                        recomputed_inputs != self.input_digest:
                    problems.append("input_digest does not recompute "
                                    "from the carried inputs "
                                    "manifest")
                # The manifest's carried references must agree with
                # the report's own fields — a swapped manifest and a
                # swapped field both surface here.
                for key, expected in (
                        ("artifact_id", self.artifact_id),
                        ("regime_digest", self.regime_digest),
                        ("horizon_family",
                         list(self.horizon_family)),
                        ("lookback_horizons",
                         list(self.lookback_horizons)),
                        ("alpha", FAMILY_ALPHA)):
                    if key in self.inputs and \
                            self.inputs[key] != expected:
                        problems.append(
                            f"inputs manifest {key!r} does not "
                            "match the report's own field — the "
                            "manifest and the report disagree")
                for key in ("event_digests", "control_digests",
                            "opportunity_digests"):
                    carried = self.inputs.get(key)
                    if carried is not None and (
                            not isinstance(carried, (list, tuple))
                            or any(not isinstance(d, str)
                                   or not _SHA256_RE.match(d)
                                   for d in carried)):
                        problems.append(
                            f"inputs manifest {key!r} must be a "
                            "list of 64-hex digests")
        # Sensitivity dispositions must be complete and well-formed
        # for EVERY status — a stripped or malformed registry is a
        # tamper signal, not a descriptive omission.
        for axis in REQUIRED_SENSITIVITY_AXES:
            entry = (self.sensitivities or {}).get(axis)
            if not isinstance(entry, Mapping) or \
                    entry.get("status") not in \
                    SENSITIVITY_DISPOSITIONS \
                    or not entry.get("reason"):
                problems.append(f"sensitivity disposition "
                                f"{axis!r} is missing or malformed")
        # C08: mandatory report fields are required for EVERY status
        # — a stripped descriptive report is not a lighter report.
        if not isinstance(self.negative_controls, Mapping) or \
                not self.negative_controls:
            problems.append("negative_controls must be a non-empty "
                            "mapping of null records")
        else:
            for nc_name in REQUIRED_NULLS:
                if nc_name not in self.negative_controls:
                    problems.append(f"negative_controls missing "
                                    f"required null {nc_name!r}")
        if not isinstance(self.multiplicity, Mapping) or \
                not self.multiplicity:
            problems.append("multiplicity must be a non-empty "
                            "mapping (Holm family metadata)")
        else:
            if self.multiplicity.get("method") != "holm":
                problems.append("multiplicity method must be 'holm'")
            if not isinstance(self.multiplicity.get("alpha"),
                              (int, float)):
                problems.append("multiplicity alpha must be numeric")
            if not isinstance(self.multiplicity.get("family_pvals"),
                              Mapping):
                problems.append("multiplicity family_pvals must "
                                "be a mapping")
        if not isinstance(self.n_event_windows, int) or \
                self.n_event_windows < 0:
            problems.append("n_event_windows must be a non-negative "
                            "integer")
        if not isinstance(self.n_control_windows, int) or \
                self.n_control_windows < 0:
            problems.append("n_control_windows must be a non-negative "
                            "integer")
        # ASSOC-03: negative-control digest revalidation — every
        # null record carrying a "digest" is re-hashed over the
        # exact payload bound at creation and compared.  A record
        # altered after signing can never stand behind ANY status.
        for nc_name, entry in (
                self.negative_controls or {}).items():
            if not isinstance(entry, Mapping) \
                    or "digest" not in entry:
                continue
            digest = entry.get("digest")
            if not isinstance(digest, str) \
                    or not _SHA256_RE.match(digest):
                problems.append(f"negative control {nc_name!r} "
                                "carries a malformed digest")
                continue
            if entry.get("kind") == "label_shuffle":
                # Created as sha256_canonical over exactly this
                # key subset — never the whole record.
                payload = {
                    "null": "label_shuffle",
                    "seed": entry.get("seed"),
                    "n_replicates": entry.get("n_replicates"),
                    "per_regime": entry.get("per_regime")}
            else:
                # Created as sha256_canonical(rec) before the
                # "digest" key was attached — the digest never
                # covers itself.
                payload = {k: v for k, v in entry.items()
                           if k != "digest"}
            try:
                recomputed = sha256_canonical(payload)
            except (TypeError, ValueError):
                problems.append(f"negative control {nc_name!r} "
                                "payload cannot be canonically "
                                "re-hashed")
                continue
            if recomputed != digest:
                problems.append(
                    f"negative control {nc_name!r} digest does "
                    "not match its recorded payload")
        if self.status == STATUS_SUPPORTED:
            # The top status may never stand on an empty or partial
            # evidence surface — every declared-family, null, and
            # sensitivity requirement must be visibly discharged.
            if not self.enrichment:
                problems.append("REGIME_ASSOCIATION_SUPPORTED "
                                "requires a non-empty enrichment "
                                "mapping")
            if not self.horizon_family or not self.lookback_horizons:
                problems.append("a supported verdict requires a "
                                "non-empty declared horizon family "
                                "(look-back horizons x placement "
                                "modes)")
            missing_nulls = [k for k in REQUIRED_NULLS
                             if k not in self.negative_controls]
            if missing_nulls:
                problems.append(f"required null families missing "
                                f"from the report: {missing_nulls}")
            missing_axes = [a for a in REQUIRED_SENSITIVITY_AXES
                            if a not in self.sensitivities]
            if missing_axes:
                problems.append(f"sensitivity dispositions missing: "
                                f"{missing_axes}")
            else:
                malformed = [
                    a for a in REQUIRED_SENSITIVITY_AXES
                    if not isinstance(self.sensitivities[a], Mapping)
                    or self.sensitivities[a].get("status")
                    not in SENSITIVITY_DISPOSITIONS
                    or not self.sensitivities[a].get("reason")]
                if malformed:
                    problems.append(f"sensitivity dispositions "
                                    f"malformed: {malformed}")
            failed_axes = sorted(
                a for a, v in self.sensitivities.items()
                if isinstance(v, Mapping)
                and v.get("status") == "FAIL")
            if failed_axes:
                problems.append(f"sensitivity FAIL dispositions "
                                f"block the supported verdict: "
                                f"{failed_axes}")
            # ASSOC-07: full evidence revalidation — the supported
            # verdict must stand on recomputing surfaces, not labels.
            if self.n_event_groups <= 0:
                problems.append("a supported verdict requires at "
                                "least one atomic event group")
            mult = self.multiplicity or {}
            fp = mult.get("family_pvals")
            if not isinstance(fp, Mapping) or not fp:
                problems.append("a supported verdict requires a "
                                "non-empty family_pvals table")
            else:
                bad = [k for k, v in fp.items()
                       if v is not None and
                       (not isinstance(v, (int, float))
                        or isinstance(v, bool)
                        or not 0.0 <= float(v) <= 1.0)]
                if bad:
                    problems.append(f"family_pvals entries out of "
                                    f"range: {bad}")
            cov = mult.get("null_coverage")
            if not isinstance(cov, Mapping) or not cov:
                problems.append("a supported verdict requires a "
                                "non-empty null_coverage record")
            else:
                uncovered = [c for c, st in cov.items()
                             if st != "executed"]
                if uncovered:
                    problems.append(f"family cells without null "
                                    f"evidence: {uncovered}")
            # the supported claim must name at least one regime that
            # is Holm-significant in EVERY declared family cell —
            # a promoted regime may never ride on a partial cell set
            rejected = set(mult.get("holm_rejected") or ())
            supported_regs = [
                rid for rid in self.enrichment
                if all(f"{h}|{m}|{rid}" in rejected
                       for h in self.lookback_horizons
                       for m in self.horizon_family)]
            if not supported_regs:
                problems.append("a supported verdict requires at "
                                "least one regime Holm-significant "
                                "in every declared family cell")
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
        region_basins: Any,
        opportunities: Any) -> list[str]:
    """Scope which labels and windows may enter the association
    universe at all.

    Every event must (a) appear in ``holdout.event_assignments``,
    (b) map to a locked *test* group — train/validation labels are
    never held-out evidence and their presence rejects — and (c) sit
    in a basin inside the locked evaluation regions named by
    ``region_basins``.  Controls must sit on mapped units inside those
    regions; every unit referenced by controls or by the frozen
    assignments must appear in ``unit_basins``; and every evaluated
    unit must carry at least one frozen assignment row.

    Every control is additionally verified against the opportunity
    registry, which is the source of truth for observability: the
    linked ``opportunity_id`` must exist in ``opportunities`` as a
    problem-free ``ObservationOpportunityV0`` whose unit, window, and
    state exactly equal what the control asserts, and a NEGATIVE
    control must link an OBSERVED_FULL opportunity — a control window
    is never taken on faith.  The binding admits or rejects inputs —
    it never alters the artifact.
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
    basin_owner: dict[str, str] = {}
    for name in sorted(region_names):
        basins = region_basins.get(name)
        if not basins:
            problems.append(f"evaluation region {name!r} maps to no "
                            "basins")
            continue
        if isinstance(basins, str) or                 not isinstance(basins, (list, tuple, set, frozenset)):
            problems.append(f"evaluation region {name!r} basins must "
                            "be a collection of basin names")
            continue
        for basin in basins:
            basin = str(basin)
            if basin in basin_owner and basin_owner[basin] != name:
                problems.append(
                    f"basin {basin!r} is claimed by both regions "
                    f"{basin_owner[basin]!r} and {name!r}")
            else:
                basin_owner[basin] = name
            eval_basins.add(basin)

    train_val = set(holdout.train_groups) | set(
        holdout.validation_groups)
    test_groups = set(holdout.test_groups)
    assignments_map = holdout.event_assignments or {}
    seen_event_ids: set[str] = set()
    for e in events:
        eid0 = getattr(e, "event_id", None)
        if eid0 in seen_event_ids:
            problems.append(f"duplicate event_id {eid0!r} — an event "
                            "may enter the association universe once")
        else:
            seen_event_ids.add(eid0)
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
    if not isinstance(opportunities, Mapping):
        problems.append("opportunities must be a mapping of "
                        "opportunity_id -> ObservationOpportunityV0")
        opportunities = {}
    seen_control_ids: set[str] = set()
    used_opportunity_ids: set[str] = set()
    event_bounds = {
        getattr(e, "event_id", ""): (
            getattr(e, "event_time_start", ""),
            getattr(e, "event_time_end", ""),
            getattr(e, "basin_id", ""))
        for e in events}
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
        cid0 = getattr(c, "control_id", None)
        if cid0 in seen_control_ids:
            problems.append(f"duplicate control_id {cid0!r} — a "
                            "control may enter the universe once")
        else:
            seen_control_ids.add(cid0)
        oid = getattr(c, "opportunity_id", None)
        if oid in used_opportunity_ids:
            problems.append(f"control {cid0!r} shares opportunity "
                            f"{oid!r} with another control — one "
                            "control per opportunity")
        elif isinstance(oid, str):
            used_opportunity_ids.add(oid)
        opp = opportunities.get(oid)
        if opp is None:
            problems.append(f"control {cid!r} links opportunity "
                            f"{oid!r} which is absent from the "
                            "opportunity registry")
            continue
        if type(opp) is not ObservationOpportunityV0:
            problems.append(f"registry entry {oid!r} is not an "
                            "ObservationOpportunityV0 record")
            continue
        if opp.opportunity_id != oid:
            problems.append(f"registry key {oid!r} does not match "
                            f"record id {opp.opportunity_id!r}")
        problems.extend(f"opportunity {oid!r}: {p}"
                        for p in opp.problems())
        if opp.unit_id != unit:
            problems.append(f"control {cid!r} unit {unit!r} does not "
                            f"match opportunity {oid!r} unit "
                            f"{opp.unit_id!r}")
        if (opp.window_start, opp.window_end) != (
                getattr(c, "window_start", None),
                getattr(c, "window_end", None)):
            problems.append(f"control {cid!r} window does not equal "
                            f"the window of opportunity {oid!r}")
        if opp.state != getattr(c, "opportunity_state", None):
            problems.append(f"control {cid!r} opportunity_state "
                            f"{getattr(c, 'opportunity_state', None)!r} "
                            f"does not match opportunity {oid!r} "
                            f"state {opp.state!r}")
        if getattr(c, "state", None) == TargetState.NEGATIVE.value and \
                opp.state != "OBSERVED_FULL":
            problems.append(f"NEGATIVE control {cid!r} must link an "
                            "OBSERVED_FULL opportunity — registry "
                            f"state is {opp.state!r}")
        if getattr(c, "state", None) == TargetState.NEGATIVE.value:
            cbasin = unit_basins.get(getattr(c, "unit_id", ""), "")
            cw0 = getattr(c, "window_start", "")
            cw1 = getattr(c, "window_end", "")
            overlapping = [eid for eid, (es, ee, eb)
                           in event_bounds.items()
                           if eb == cbasin and es and ee
                           and es < cw1 and cw0 < ee]
            if overlapping:
                problems.append(
                    f"NEGATIVE control {cid0!r} window overlaps "
                    f"admitted event(s) {sorted(overlapping)} in "
                    f"basin {cbasin!r} — state is derived, never "
                    "caller-asserted")
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
# Producer-payload binding + canonical input manifest
# ---------------------------------------------------------------------

def _producer_payload_binding_problems(
        artifact: RegimeAssignmentArtifact,
        payload: Any) -> list[str]:
    """PROV-04 (R6): verify a serialized frozen producer payload
    against the artifact it claims to have produced.

    The check is enforceable purely from ``(artifact, payload)`` and
    is fail-closed at both ends:

    * **Payload internal consistency** — the freeze digest chain is
      recomputed exactly as
      ``science_v0.regimes.freeze_regime_artifact`` and
      ``adapters.regime_assignment_from_artifact`` do:
      ``assignment_digest`` over the raw ``assignments`` sidecar,
      ``regime_artifact_digest`` over the payload minus
      ``{regime_artifact_digest, freeze_digest, frozen}``, and
      ``freeze_digest`` over the payload minus
      ``{freeze_digest, frozen}``.  ``frozen`` must be ``True``.
      A carried digest is verified, never trusted.
    * **Artifact binding** — the adapter stamps
      ``regime_digest`` and ``producer_payload_digest`` to the
      payload's ``freeze_digest``; both must equal it.  The
      normalized sidecar rows (the single permitted conversion is
      int -> str ``regime_id``) must equal the artifact's canonical
      assignment rows, and the provenance fields the adapter copies
      verbatim (``fitted_on``/``label_blinding``/``mode``/``seeds``)
      must agree.
    """
    problems: list[str] = []
    if not isinstance(payload, Mapping):
        return ["producer_payload must be a JSON-object mapping — "
                "the serialized frozen producer artifact"]
    freeze = payload.get("freeze_digest")
    raw = payload.get("assignments")
    raw_ok = isinstance(raw, (list, tuple)) and bool(raw)
    # --- payload internal consistency: recompute the freeze
    # digest chain, never trust a carried digest ---
    try:
        pre_freeze = {k: v for k, v in payload.items()
                      if k not in ("freeze_digest", "frozen")}
        if not isinstance(freeze, str) or not _SHA256_RE.match(freeze):
            problems.append("producer_payload freeze_digest is "
                            "missing or not a 64-hex sha256")
        elif sha256_canonical(pre_freeze) != freeze:
            problems.append("producer_payload freeze_digest does not "
                            "recompute from the payload — post-freeze "
                            "mutation or a mislabeled artifact")
        rad = payload.get("regime_artifact_digest")
        if not isinstance(rad, str) or not _SHA256_RE.match(rad):
            problems.append("producer_payload regime_artifact_digest "
                            "is missing or not a 64-hex sha256")
        elif sha256_canonical(
                {k: v for k, v in pre_freeze.items()
                 if k != "regime_artifact_digest"}) != rad:
            problems.append("producer_payload regime_artifact_digest "
                            "does not recompute from the payload — "
                            "mutated after production")
        if not raw_ok:
            problems.append("producer_payload carries no assignment "
                            "sidecar — a summary-only payload cannot "
                            "bind the artifact")
        elif sha256_canonical(list(raw)) != \
                payload.get("assignment_digest"):
            problems.append("producer_payload assignment_digest does "
                            "not recompute over the assignment "
                            "sidecar")
    except (TypeError, ValueError):
        problems.append("producer_payload cannot be canonically "
                        "re-hashed — non-JSON-native content")
        raw_ok = False
    if payload.get("frozen") is not True:
        problems.append("producer_payload must carry frozen: true — "
                        "an unfrozen surface cannot bind")
    # --- artifact <-> payload binding ---
    if freeze != artifact.producer_payload_digest:
        problems.append("producer_payload freeze_digest does not "
                        "equal the artifact's producer_payload_digest "
                        "— the payload is not the producer this "
                        "artifact declares")
    if freeze != artifact.regime_digest:
        problems.append("producer_payload freeze_digest does not "
                        "equal the artifact's regime_digest — the "
                        "adapter stamps regime_digest = freeze_digest")
    if raw_ok:
        norm: list = []
        bad_row = False
        for row in raw:
            if isinstance(row, (list, tuple)) and len(row) == 3 and \
                    isinstance(row[0], str) and \
                    isinstance(row[1], str) and \
                    isinstance(row[2], (str, int)) and \
                    not isinstance(row[2], bool):
                norm.append([row[0], row[1], str(row[2])])
            else:
                bad_row = True
                break
        if bad_row:
            problems.append("producer_payload assignment rows must "
                            "be (str, str, str|int) triples — the "
                            "producer's raw labels admit only the "
                            "int -> str conversion")
        elif sorted(norm) != _canonical_assignment_rows(
                artifact.assignments):
            problems.append("producer_payload assignment sidecar "
                            "does not match the artifact's canonical "
                            "assignment rows — the payload binds a "
                            "different partition")
    for f in ("fitted_on", "label_blinding", "mode"):
        if payload.get(f) != getattr(artifact, f):
            problems.append(f"producer_payload {f} does not match "
                            "the artifact's stamped provenance")
    seeds = payload.get("seeds")
    if not isinstance(seeds, (list, tuple)) or \
            list(seeds) != list(artifact.seeds):
        problems.append("producer_payload seeds do not match the "
                        "artifact's declared seeds")
    return problems


def _record_input_digest(rec: Any) -> str:
    """Canonical digest of one bound input record.  Non-record
    inputs digest to their type tag so they are still bound into the
    manifest — censored inputs are inputs too."""
    to_dict = getattr(rec, "to_dict", None)
    if callable(to_dict):
        try:
            return sha256_canonical(to_dict())
        except (TypeError, ValueError):
            pass
    return sha256_canonical(
        {"non_record_type": type(rec).__name__})


def _association_input_manifest(
        artifact: RegimeAssignmentArtifact,
        events: Sequence[EventLabelV0],
        controls: Sequence[ControlWindowV0],
        unit_basins: Any,
        holdout: Any,
        region_basins: Any,
        opportunities: Any,
        lookback_names: Sequence[str],
        family: Sequence[str],
        n_boot: int, seed: int) -> dict:
    """The canonical ``association_input_manifest/v0`` a report's
    ``input_digest`` is computed over.  Every input the harness
    consumes is bound: the artifact's digests, per-record digests of
    every passed event/control/registry opportunity (censored
    inputs included), the basin maps, the holdout digest, and the
    declared family parameters.  A report claiming different inputs
    produces a different ``input_digest``."""
    def _map_str(m: Any) -> dict:
        if not isinstance(m, Mapping):
            return {}
        return {str(k): str(v) for k, v in
                sorted(m.items(), key=lambda kv: str(kv[0]))}

    def _basin_list(v: Any) -> list:
        if isinstance(v, (list, tuple, set, frozenset)):
            return sorted(str(b) for b in v)
        return [str(v)]

    return {
        "record": "association_input_manifest/v0",
        "artifact_id": artifact.artifact_id,
        "regime_digest": artifact.regime_digest,
        "assignment_digest": artifact.assignment_digest,
        "producer_payload_digest": artifact.producer_payload_digest,
        "event_digests": sorted(
            _record_input_digest(e) for e in events),
        "control_digests": sorted(
            _record_input_digest(c) for c in controls),
        "opportunity_digests": sorted(
            _record_input_digest(o)
            for o in (opportunities.values()
                      if isinstance(opportunities, Mapping)
                      else ())),
        "unit_basins": _map_str(unit_basins),
        "region_basins": {
            str(k): _basin_list(v)
            for k, v in sorted(
                (region_basins or {}).items(),
                key=lambda kv: str(kv[0]))}
            if isinstance(region_basins, Mapping) else {},
        "holdout_digest": _record_input_digest(holdout),
        "lookback_horizons": [str(h) for h in lookback_names],
        "horizon_family": [str(m) for m in family],
        "alpha": FAMILY_ALPHA,
        "n_boot": int(n_boot),
        "seed": int(seed),
    }


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
        opportunities: Mapping[str, ObservationOpportunityV0],
        lookback_horizons: Collection[str]
        = DEFAULT_LOOKBACK_HORIZONS,
        horizon_family: Collection[str] = PLACEMENT_MODES,
        n_boot: int = 200, seed: int = 0,
        producer_payload: Optional[Mapping] = None
        ) -> AssociationReport:
    """Run the held-out event–regime association harness.

    The ``holdout``/``region_basins`` binding scopes which labels and
    windows are admitted (locked test groups, named evaluation
    regions), and ``opportunities`` is the observation-opportunity
    registry every control's lineage is verified against — control
    denominators derive only from windows whose registry linkage
    checks out.  Any binding violation raises ``ValueError`` listing
    all problems — fail-closed, no partial association universe.

    ``artifact`` must be frozen producer-adapter output — the
    ``RegimeAssignmentArtifact`` emitted by
    ``adapters.regime_assignment_from_artifact`` — carrying the
    artifact's own provenance fields (64-hex ``regime_digest``,
    non-empty ``assignments``, ``fitted_on``/``label_blinding``/
    ``mode``/``seeds``).  Locally-self-digested minimal artifacts
    lacking those fields are rejected at the door.  Synthetic
    fixtures satisfy this because they are constructed on the same
    record with the same provenance surface.

    ``lookback_horizons`` declares real look-back horizons ("0d",
    "3d", "7d" — integer-day strings drawn from
    ``ALLOWED_LOOKBACK_DAYS``): a horizon of ``h`` days
    extends each event's effective date window backward by ``h``
    days before regime co-occurrence is counted, so an event counts
    toward a cell when the co-occurrence holds within the look-back
    window preceding its anchor.  ``horizon_family`` declares the
    interval-placement subset (``midpoint`` / ``uniform`` /
    ``worst_case``); the inference family is every
    regime x (look-back horizon x placement mode) cell, and the Holm
    family spans exactly those declared cells.

    ``producer_payload`` (PROV-04) is the serialized frozen producer
    artifact (``artifact_payload``) the regime artifact was adapted
    from.  When supplied, its freeze digest chain is recomputed and
    bound to the artifact field-by-field — a supplied payload that
    fails verification rejects outright.  When absent, the artifact
    is admitted as a local record for descriptive use only: the
    report carries ``binding="local_artifact_unverified"`` and
    ``REGIME_ASSOCIATION_SUPPORTED`` is unreachable.

    Pure and deterministic: identical inputs plus ``seed`` give a
    byte-identical ``canonical_json(report.to_dict())``.
    """
    # ASSOC-C04 provenance floor: reject before any binding work —
    # a non-artifact or a record failing its own problems() (bad
    # digest, empty assignments, missing provenance fields) can
    # never enter the association universe.
    if not isinstance(artifact, RegimeAssignmentArtifact):
        raise ValueError(
            "association artifact must be a RegimeAssignmentArtifact "
            "— frozen producer adapter output "
            "(adapters.regime_assignment_from_artifact); synthetic "
            "fixtures built on the same record satisfy this")
    art_problems = artifact.problems()
    if art_problems:
        raise ValueError("association regime artifact rejected — "
                         "provenance floor unmet: "
                         + "; ".join(art_problems))
    # PROV-04 (R6): verified producer binding.  A supplied
    # producer_payload is recomputed end-to-end and bound to the
    # artifact; one that fails to verify rejects outright — a forged
    # binding is a binding violation, not a descriptive fallback.
    # Without it the artifact is a local record: admitted for
    # descriptive use, never for the supported verdict.
    if producer_payload is None:
        binding_mode = BINDING_UNVERIFIED
    else:
        pp_problems = _producer_payload_binding_problems(
            artifact, producer_payload)
        if pp_problems:
            raise ValueError(
                "association producer-payload binding rejected: "
                + "; ".join(pp_problems))
        binding_mode = BINDING_VERIFIED
    binding = _holdout_binding_problems(
        artifact, events, controls, unit_basins, holdout,
        region_basins, opportunities)
    if binding:
        raise ValueError("association holdout binding rejected: "
                         + "; ".join(binding))
    notes: list[str] = [
        "claim_scope=research_only_no_operational_authorization",
    ]
    if binding_mode != BINDING_VERIFIED:
        notes.append("no verified producer payload — the artifact "
                     "is admitted as a local record for descriptive "
                     "use; REGIME_ASSOCIATION_SUPPORTED is "
                     "unreachable")
    # Declared placement family — canonicalized to PLACEMENT_MODES
    # order so caller ordering cannot perturb the family.
    declared_modes = tuple(horizon_family or ())
    bad_modes = [m for m in declared_modes
                 if m not in PLACEMENT_MODES]
    family = tuple(m for m in PLACEMENT_MODES
                   if m in set(declared_modes))
    if bad_modes or not family:
        raise ValueError(
            f"horizon_family must be a non-empty declared subset of "
            f"{PLACEMENT_MODES} — got {declared_modes!r}; "
            "post-hoc cells are inadmissible")
    # Declared look-back horizons — canonicalized by day count.
    if not lookback_horizons:
        raise ValueError(
            "lookback_horizons must declare at least one "
            "integer-day horizon ('0d', '3d', '7d', ...) — an "
            "undeclared horizon family is inadmissible")
    lookback_days = tuple(sorted(
        {_parse_lookback_days(h) for h in lookback_horizons}))
    # ASSOC-02: syntax alone is not admissibility — every declared
    # horizon must sit inside the policy-derived allowlist
    # (event-time class horizons plus the "0d" identity).
    disallowed = [d for d in lookback_days
                  if d not in ALLOWED_LOOKBACK_DAYS]
    if disallowed:
        raise ValueError(
            f"lookback_horizons "
            f"{tuple(f'{d}d' for d in disallowed)!r} are outside "
            f"the declared allowlist {ALLOWED_LOOKBACK_DAYS} — "
            "only event-time-class-admissible look-back day "
            "counts may enter the inference family")
    lookback_names = tuple(f"{d}d" for d in lookback_days)
    regime_ids = artifact.regime_ids()

    # ASSOC-03 (R6): bind the report to its inputs — the canonical
    # manifest covers the artifact's digests, every passed event /
    # control / registry-opportunity record, the basin maps, the
    # holdout digest, and the declared family parameters.  A report
    # claiming different inputs digests differently.
    inputs_manifest = _association_input_manifest(
        artifact, events, controls, unit_basins, holdout,
        region_basins, opportunities, lookback_names, family,
        n_boot, seed)
    input_digest = sha256_canonical(inputs_manifest)

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
    # Every NEGATIVE control below was verified at binding to link a
    # problem-free OBSERVED_FULL registry opportunity — the registry,
    # not the controls list, is the denominator's source of truth.
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
    basin_units = _basin_units(unit_basins)
    # Phantom groups — every member basin resolves to zero units —
    # carry no evidence and cannot satisfy the power floor.
    n_event_groups = sum(
        1 for members in groups.values()
        if any(basin_units.get(m.basin_id) for m in members))

    if len(regime_ids) < 2:
        notes.append("regime artifact is degenerate — fewer than two "
                     "distinct regimes (single-regime trivial "
                     "partition)")
        return AssociationReport(
            artifact_id=artifact.artifact_id,
            regime_digest=artifact.regime_digest,
            n_event_windows=len(admissible_events),
            n_control_windows=len(negative_controls),
            n_event_groups=n_event_groups,
            enrichment={}, transitions={}, novelty={},
            negative_controls={}, interval_sensitivity={},
            slices={"pooled": {}, "per_basin": {}, "per_season": {}},
            status=STATUS_NOT_SUPPORTED, notes=tuple(notes),
            sensitivities={
                a: {"status": "NOT_APPLICABLE",
                    "reason": "degenerate single-regime partition — "
                              "no sensitivity surface exists"}
                for a in REQUIRED_SENSITIVITY_AXES},
            binding=binding_mode,
            inputs=inputs_manifest, input_digest=input_digest)

    # --- declared inference family: every regime x (look-back
    # horizon x placement mode) cell gets the full bootstrap; the
    # family is predeclared, never implicit ---
    family_tables: dict[tuple[int, str], dict[str, Any]] = {}
    family_enrichment: dict[tuple[int, str], dict[str, dict]] = {}
    precision_exclusions: dict[str, int] = {}
    for h in lookback_days:
        # ASSOC-01: a look-back horizon shorter than an event's own
        # timing uncertainty is inadmissible for that event — the
        # event is excluded from the cell and the exclusion counted.
        if h > 0:
            groups_h = {}
            n_excl = 0
            for gid, members in groups.items():
                # C07: per-event precision-class horizon policy —
                # coarse-precision events are inadmissible for
                # fine-grained lookback cells; interval events must
                # satisfy the width check.  An event never inherits
                # the union of all horizons.
                keep = [m for m in members
                        if _event_horizon_admissible(m, h)]
                n_excl += len(members) - len(keep)
                if keep:
                    groups_h[gid] = keep
        else:
            groups_h, n_excl = groups, 0
        for mode in family:
            t = _build_table(artifact, groups_h, negative_controls,
                             basin_units, mode, lookback_days=h)
            t["n_excluded_for_precision"] = n_excl
            precision_exclusions[f"{h}d|{mode}"] = n_excl
            family_tables[(h, mode)] = t
            family_enrichment[(h, mode)] = event_group_bootstrap(
                t, [e for m in groups_h.values() for e in m]
                if h > 0 else admissible_events,
                n_boot=n_boot, seed=seed)
    # The primary cell — smallest declared look-back, first declared
    # placement — carries the report's headline enrichment mapping.
    primary_cell = (lookback_days[0], family[0])
    table = family_tables[primary_cell]
    enrichment = family_enrichment[primary_cell]

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
        "label_shuffle": _label_shuffle_null(
            artifact, groups, negative_controls, basin_units,
            unit_basins, mode=primary_cell[1],
            lookback_days=primary_cell[0],
            n_boot=n_boot, seed=seed),
        # ASSOC-04 geography-preserving shift null — dates shifted
        # within fixed basin geography
        "spatial_shift": _spatial_shift_null(
            artifact, groups, negative_controls, basin_units,
            unit_basins, mode=primary_cell[1],
            lookback_days=primary_cell[0]),
    }
    # the flat controls (placebo, impossible, time-reversed) must
    # stay flat; the label-shuffle null must instead be BEATEN by
    # every enriched regime — its per-regime p must reach alpha.
    flat_nulls = ("placebo", "impossible_regime", "time_reversed")
    missing_nulls = [k for k in REQUIRED_NULLS if k not in neg]
    all_flat = (not missing_nulls) and all(
        neg[k].get("flat") for k in flat_nulls)
    shuffle_p = neg["label_shuffle"].get("per_regime", {})
    # spatial_shift: enriched regimes must beat the shifted-date
    # distribution (per-regime p <= declared alpha), not be flat.
    shift_p = neg["spatial_shift"].get("per_regime", {})

    # --- interval-placement sensitivity: every placement mode's
    # point ratios at the primary declared look-back (control frame
    # identical across modes — it is never look-back extended) ---
    sensitivity: dict[str, dict[str, Optional[float]]] = {}
    for mode in PLACEMENT_MODES:
        if (primary_cell[0], mode) in family_tables:
            sensitivity[mode] = _point_ratios(
                family_tables[(primary_cell[0], mode)])
        elif mode == "worst_case":
            sensitivity[mode] = _worst_case_ratios(
                artifact, groups, basin_units,
                table["control_counts"], table["control_total"],
                table["labels"], lookback_days=primary_cell[0])
        else:
            sensitivity[mode] = _point_ratios(_build_table(
                artifact, groups, negative_controls, basin_units,
                mode, lookback_days=primary_cell[0]))

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
    # Multiplicity: Holm step-down over every declared
    # regime x look-back-horizon x placement-mode cell; only
    # corrected-significant cells may promote the verdict.  Only
    # p_enrich cells computed against the declared family feed the
    # correction — sensitivity recomputations never enter it.
    # ASSOC-02/03/05: calibrated inference — the Holm family runs on
    # per-cell stratified label-shuffle permutation p-values (the
    # season x basin strata permutation null), one cell per
    # regime x look-back x placement.  Bootstrap ``p_enrich`` stays in
    # the enrichment cells as the uncertainty surface; it never feeds
    # the correction.  ``null_coverage`` records exactly which family
    # cells carried null evidence.
    null_reps = max(min(int(n_boot), 50), 10)
    null_coverage: dict[str, str] = {}
    family_pvals: dict[str, Optional[float]] = {}
    for (h, mode) in family_tables:
        cell_key_prefix = f"{h}d|{mode}"
        if (h, mode) == primary_cell:
            rec = neg["label_shuffle"]
        else:
            rec = _label_shuffle_null(
                artifact, groups, negative_controls, basin_units,
                unit_basins, mode=mode, lookback_days=h,
                n_boot=null_reps, seed=seed + 7 * h
                + PLACEMENT_MODES.index(mode))
        null_coverage[cell_key_prefix] = "executed"
        for rid, r in (rec.get("per_regime") or {}).items():
            family_pvals[f"{cell_key_prefix}|{rid}"] = r.get("p")
    holm = _holm_significant(family_pvals)
    corrected_cells = {k for k, v in holm.items() if v}
    # a regime counts as enriched only if it is Holm-significant in
    # EVERY declared family cell — a single-cell hit is a post-hoc
    # cell and cannot promote
    enriched = sorted({
        rid for (h, mode), cells in family_enrichment.items()
        for rid in cells
        if all(f"{hh}d|{mm}|{rid}" in corrected_cells
               for hh in lookback_days for mm in family)})
    direction_ok = all(
        all(sensitivity[mode].get(r) is not None
            and sensitivity[mode][r] > 1.0
            for mode in sensitivity)
        for r in enriched)

    # --- mandatory sensitivity registry: every axis in
    # REQUIRED_SENSITIVITY_AXES is dispositioned explicitly with
    # PASS / FAIL / NOT_APPLICABLE plus a reason; absent data can
    # never silently become a pass, and any FAIL blocks the
    # supported verdict ---
    sensitivities: dict[str, Any] = {}
    # (a) interval placement
    sensitivities["interval_placement"] = {
        "status": "PASS" if direction_ok or not enriched else "FAIL",
        "modes": sensitivity,
        "reason": "enrichment direction must survive every "
                  "placement mode at the primary look-back"}
    # (b) precision: events with non-day precision or unknown timing
    # are excluded and the pooled direction recomputed
    precise_events = [e for e in admissible_events
                      if e.event_time_precision == "day"
                      and _event_midpoint_date(e) is not None]
    n_imprecise = len(admissible_events) - len(precise_events)
    if n_imprecise == 0:
        sensitivities["precision"] = {
            "status": "NOT_APPLICABLE",
            "reason": "every admitted event carries day-resolved "
                      "timing — nothing to exclude"}
    elif not enriched:
        sensitivities["precision"] = {
            "status": "PASS",
            "n_excluded": n_imprecise,
            "reason": "no enriched regimes to sensitize — excluding "
                      "non-day-precision events only shrinks the "
                      "universe"}
    else:
        p_groups = _group_events(precise_events)
        n_precise_groups = sum(
            1 for m in p_groups.values()
            if any(basin_units.get(x.basin_id) for x in m))
        if n_precise_groups == 0:
            sensitivities["precision"] = {
                "status": "FAIL",
                "n_excluded": n_imprecise,
                "reason": "excluding non-day-precision events "
                          "leaves no precise event group — the "
                          "direction cannot be verified"}
        else:
            p_table = _build_table(
                artifact, p_groups, negative_controls, basin_units,
                primary_cell[1], lookback_days=primary_cell[0])
            p_ratios = _point_ratios(p_table)
            p_bad = [r for r in enriched
                     if p_ratios.get(r) is None
                     or p_ratios[r] <= 1.0]
            sensitivities["precision"] = {
                "status": "FAIL" if p_bad else "PASS",
                "n_excluded": n_imprecise,
                "precise_ratios": {r: p_ratios.get(r)
                                   for r in enriched},
                "reason": ("enriched regimes reversing under "
                           "day-precision-only recomputation: "
                           f"{p_bad}" if p_bad else
                           "enrichment direction survives "
                           "day-precision-only recomputation")}
    # (c) observation effort: control shares re-weighted by the
    # number of registry opportunities on each control's unit
    opp_per_unit = Counter(
        opp.unit_id for opp in opportunities.values()
        if type(opp) is ObservationOpportunityV0)
    w_controls = Counter()
    w_total = 0.0
    for c in negative_controls:
        w = float(opp_per_unit.get(c.unit_id, 0))
        if w > 0:
            for lbl, cnt in _control_label_counts(artifact, c).items():
                w_controls[lbl] += cnt * w
            w_total += w
    if w_total > 0:
        w_ratios = {}
        for rid in enriched:
            e_share = (enrichment.get(rid) or {}).get(
                "event_share", 0.0)
            wc = w_controls.get(rid, 0.0) / w_total
            w_ratios[rid] = _round12(_ratio(e_share, wc))
        sensitivities["observation_effort"] = {
            "status": "PASS" if all(
                r is not None and r > 1.0 for r in w_ratios.values())
                or not enriched else "FAIL",
            "weighted_ratios": w_ratios,
            "reason": "control frame re-weighted by verified "
                      "opportunity counts per unit"}
    else:
        sensitivities["observation_effort"] = {
            "status": "NOT_APPLICABLE",
            "reason": "no verified registry opportunities weight "
                      "the control frame"}
    # (d) era_boundary: event groups split at the median anchor date
    anchors = sorted(d for d in (_group_anchor_date(m)
                                 for m in groups.values())
                     if d is not None)
    if len(set(a.year for a in anchors)) < 2:
        sensitivities["era_boundary"] = {
            "status": "NOT_APPLICABLE",
            "reason": "event groups span a single era (one calendar "
                      "year) — no era split exists"}
    else:
        mid = anchors[len(anchors) // 2]
        era_ratios = {}
        era_fail = False
        for tag, pred in (("early", lambda d: d < mid),
                          ("late", lambda d: d >= mid)):
            sub = {gid: m for gid, m in groups.items()
                   if _group_anchor_date(m) is not None
                   and pred(_group_anchor_date(m))}
            if not sub:
                continue
            st = _build_table(artifact, sub, negative_controls,
                              basin_units, primary_cell[1],
                              lookback_days=primary_cell[0])
            pr = _point_ratios(st)
            era_ratios[tag] = {r: pr.get(r) for r in enriched}
            for r in enriched:
                if era_ratios[tag][r] is not None and                         era_ratios[tag][r] <= 1.0:
                    era_fail = True
        sensitivities["era_boundary"] = {
            "status": "FAIL" if era_fail else "PASS",
            "per_era_ratios": era_ratios,
            "reason": "enriched regimes must hold direction in both "
                      "era halves"}
    # (e) feature_subset: the regime artifact is frozen — feature
    # ablation lives on the producer side, never here
    sensitivities["feature_subset"] = {
        "status": "NOT_APPLICABLE",
        "reason": "regime artifact frozen — feature ablation is a "
                  "producer-side axis"}
    # (f) missingness: the registry's coverage spectrum — when every
    # opportunity is OBSERVED_FULL there is nothing to weight
    states = {opp.state for opp in opportunities.values()
              if type(opp) is ObservationOpportunityV0}
    if states <= {"OBSERVED_FULL"}:
        sensitivities["missingness"] = {
            "status": "NOT_APPLICABLE",
            "reason": "registry contains only OBSERVED_FULL "
                      "opportunities — no partial coverage to "
                      "sensitize"}
    else:
        partial = [o for o in opportunities.values()
                   if type(o) is ObservationOpportunityV0
                   and o.state != "OBSERVED_FULL"]
        sensitivities["missingness"] = {
            "status": "PASS",
            "n_partial": len(partial),
            "reason": "partial-coverage opportunities are excluded "
                      "from NEGATIVE derivation by binding already"}
    # (g) mechanism: pooled enrichment recomputed per mechanism
    # slice; a reversal inside any slice with >=3 atomic groups
    # fails the axis
    mech_buckets: dict[str, list[str]] = {}
    for gid in sorted(groups):
        key = "|".join(sorted({_event_mechanism(e)
                               for e in groups[gid]}))
        mech_buckets.setdefault(key, []).append(gid)
    qualifying_mechs = {k: ids for k, ids in mech_buckets.items()
                        if len(ids) >= MECHANISM_MIN_GROUPS}
    if not qualifying_mechs:
        sensitivities["mechanism"] = {
            "status": "NOT_APPLICABLE",
            "reason": f"no mechanism slice reaches the "
                      f"{MECHANISM_MIN_GROUPS}-group floor"}
    elif not enriched:
        sensitivities["mechanism"] = {
            "status": "PASS",
            "reason": "no enriched regimes to sensitize across "
                      "mechanism slices"}
    else:
        mech_ratios: dict[str, dict] = {}
        mech_reversals: list[str] = []
        for mech in sorted(qualifying_mechs):
            sub = {gid: groups[gid] for gid in qualifying_mechs[mech]}
            st = _build_table(artifact, sub, negative_controls,
                              basin_units, primary_cell[1],
                              lookback_days=primary_cell[0])
            pr = _point_ratios(st)
            mech_ratios[mech] = {r: pr.get(r) for r in enriched}
            for r in enriched:
                if pr.get(r) is None or pr[r] <= 1.0:
                    mech_reversals.append(f"{mech}|{r}")
        sensitivities["mechanism"] = {
            "status": "FAIL" if mech_reversals else "PASS",
            "per_mechanism_ratios": mech_ratios,
            "reason": ("enriched regimes reversing inside a "
                       "mechanism slice: "
                       f"{sorted(mech_reversals)}" if mech_reversals
                       else "enrichment direction survives every "
                            f"mechanism slice with >="
                            f"{MECHANISM_MIN_GROUPS} groups")}
    sens_failed = [k for k, v in sensitivities.items()
                   if v.get("status") == "FAIL"]

    if n_event_groups < MIN_EVENT_GROUPS:
        status = STATUS_UNDERPOWERED
        notes.append(f"{n_event_groups} atomic event groups is below "
                     f"the MIN_EVENT_GROUPS={MIN_EVENT_GROUPS} floor — "
                     "descriptive output only")
    elif enriched and all_flat and direction_ok \
            and not sens_failed \
            and all(
                (shuffle_p.get(r) or {}).get("p") is not None
                and shuffle_p[r]["p"] <= FAMILY_ALPHA
                for r in enriched) \
            and all(
                (shift_p.get(r) or {}).get("p") is not None
                and shift_p[r]["p"] <= FAMILY_ALPHA
                for r in enriched):
        if binding_mode == BINDING_VERIFIED:
            status = STATUS_SUPPORTED
            notes.append("one or more frozen regimes show a "
                         "non-random correspondence with held-out "
                         "adjudicated events under predeclared "
                         "associational tests — an association "
                         "result only")
        else:
            # PROV-04: the supported verdict can never ride on an
            # unverified local artifact — demote to descriptive,
            # mirroring the non-associable demotion below.
            status = STATUS_DESCRIPTIVE
            notes.append("the evidence surface otherwise qualifies "
                         "for the supported verdict, but the "
                         "artifact's producer binding is unverified "
                         "— a local artifact without its frozen "
                         "producer payload can never carry "
                         "REGIME_ASSOCIATION_SUPPORTED")
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
        if missing_nulls:
            notes.append(f"required null families missing: "
                         f"{missing_nulls}")
        if sens_failed:
            notes.append(f"sensitivity checks failed: {sens_failed}")

    return AssociationReport(
        artifact_id=artifact.artifact_id,
        regime_digest=artifact.regime_digest,
        n_event_windows=len(admissible_events),
        n_control_windows=len(negative_controls),
        n_event_groups=n_event_groups,
        enrichment=enrichment, transitions=transitions,
        novelty=novelty, negative_controls=neg,
        interval_sensitivity=sensitivity, slices=slices,
        status=status, notes=tuple(notes),
        horizon_family=tuple(family),
        lookback_horizons=lookback_names,
        multiplicity={"method": "holm", "alpha": FAMILY_ALPHA,
                      "family_pvals": family_pvals,
                      "holm_rejected": sorted(k for k, v in
                                              holm.items() if v),
                      "lookback_horizons": list(lookback_names),
                      "placement_modes": list(family),
                      "inference": "stratified_permutation_p",
                      "null_coverage": null_coverage,
                      "precision_exclusions": precision_exclusions},
        sensitivities=sensitivities,
        binding=binding_mode,
        inputs=inputs_manifest, input_digest=input_digest)


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
    "ALLOWED_LOOKBACK_DAYS",
    "ASSOCIATION_BINDINGS",
    "ASSOCIATION_STATUSES",
    "AssociationReport",
    "BINDING_UNVERIFIED",
    "BINDING_VERIFIED",
    "DEFAULT_LOOKBACK_HORIZONS",
    "MECHANISM_MIN_GROUPS",
    "MIN_EVENT_GROUPS",
    "MIN_SPATIAL_SHIFTS",
    "NOVELTY_SHARE_MAX",
    "PLACEMENT_MODES",
    "REQUIRED_SENSITIVITY_AXES",
    "RegimeAssignmentArtifact",
    "SENSITIVITY_DISPOSITIONS",
    "SPATIAL_SHIFT_OFFSETS",
    "STATUS_DESCRIPTIVE",
    "STATUS_NOT_SUPPORTED",
    "STATUS_SUPPORTED",
    "STATUS_UNDERPOWERED",
    "UNASSIGNED_LABEL",
    "association_report_text",
    "event_group_bootstrap",
    "run_association",
]
