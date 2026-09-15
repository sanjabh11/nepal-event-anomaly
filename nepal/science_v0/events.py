"""Event identity, observation-opportunity, control, and holdout engine.

Implements EVENT_PACKAGE_SPEC_V0.md semantics on plain dicts — the
contract validators (nepal.research_v0) are the enforcement surface;
this module produces normalized intermediate structures that a later
gated intake lane will map into contract records.

Research-only: operates on synthetic or later qualified-source inputs;
emits no labels, claims, or operational state.

Conventions:
- Times are ISO-8601 UTC strings ("YYYY-MM-DDTHH:MM:SSZ").
- An "interval" is (start_iso, end_iso) with start < end.
- Timing classes follow the spec table: EXACT_TIMESTAMP / EXACT_DAY /
  INTERVAL_LE_7D / INTERVAL_8_30D / COARSE_OR_UNRESOLVED.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

# Predeclared Nepal basin universe (spec §4).
BASIN_UNIVERSE = (
    "koshi", "gandaki", "karnali", "mahakali", "bagmati",
    "dudh_koshi", "arun", "tamor", "seti", "marsyangdi",
    "kali_gandaki", "bheri", "humla_karnali", "langtang_trishuli",
    "indrawati",
)

TIMING_CLASSES = (
    "EXACT_TIMESTAMP", "EXACT_DAY", "INTERVAL_LE_7D",
    "INTERVAL_8_30D", "COARSE_OR_UNRESOLVED",
)

_MIN_EVAL_REGIONS = 2
_MIN_GEO_GROUPS = 3
# A single-cell/single-basin universe cannot support evaluation.
_SINGLE_BOX_BASINS = {"langtang_trishuli"}


def _parse(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError(f"naive timestamp rejected: {iso!r}")
    return dt.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- events

@dataclass(frozen=True)
class SourceRow:
    """Raw intake row as delivered by a source (pre-normalization)."""
    source_id: str
    source_version: str
    source_row_key: str
    mechanism: str
    interval_start: str
    interval_end: str
    declared_precision: str           # e.g. "day", "interval_3d", "month"
    basin: str                        # predeclared basin-universe name
    cascade_group_id: str | None = None
    parent_source_row_key: str | None = None
    observed_on: str | None = None    # observation/report timestamp


@dataclass(frozen=True)
class EventIdentity:
    """Canonical normalized event — one physical event, one record."""
    event_id: str
    source_id: str
    source_version: str
    mechanism: str
    interval_start: str
    interval_end: str
    uncertainty_seconds: int
    timing_class: str
    basin: str
    cascade_group_id: str
    parent_event_id: str | None
    duplicate_of: str | None
    timing_variants: tuple            # (min, central, max) ISO instants


def classify_timing(interval_start: str, interval_end: str,
                    declared_precision: str) -> str:
    """Measured timing class from interval width + declared precision.

    Conservative mapping (spec §2): declared coarse classes
    (month/year/slice) always map to COARSE_OR_UNRESOLVED regardless of
    measured width; measured width then bounds the class.
    """
    width = (_parse(interval_end) - _parse(interval_start)).total_seconds()
    if width <= 0:
        raise ValueError("interval_end must be after interval_start")
    if declared_precision in ("month", "year", "monsoon_slice",
                              "unresolved"):
        return "COARSE_OR_UNRESOLVED"
    if width <= 3600:
        return "EXACT_TIMESTAMP"
    if width <= 86400:
        return "EXACT_DAY"
    if width <= 7 * 86400:
        return "INTERVAL_LE_7D"
    if width <= 30 * 86400:
        return "INTERVAL_8_30D"
    return "COARSE_OR_UNRESOLVED"


def normalize_event(row: SourceRow) -> EventIdentity:
    """Normalize one source row into a canonical EventIdentity."""
    if row.basin not in BASIN_UNIVERSE:
        raise ValueError(f"basin {row.basin!r} not in the predeclared "
                         f"Nepal basin universe")
    if not row.source_id or not row.source_version:
        raise ValueError("source_id and source_version are required")
    t0, t1 = _parse(row.interval_start), _parse(row.interval_end)
    if t1 <= t0:
        raise ValueError("event interval must be non-empty")
    timing_class = classify_timing(row.interval_start, row.interval_end,
                                   row.declared_precision)
    uncertainty = int((t1 - t0).total_seconds())
    central = t0 + (t1 - t0) / 2
    event_id = f"{row.source_id}:{row.source_version}:{row.source_row_key}"
    return EventIdentity(
        event_id=event_id,
        source_id=row.source_id,
        source_version=row.source_version,
        mechanism=row.mechanism,
        interval_start=_iso(t0),
        interval_end=_iso(t1),
        uncertainty_seconds=uncertainty,
        timing_class=timing_class,
        basin=row.basin,
        cascade_group_id=(row.cascade_group_id
                          or f"solo:{event_id}"),
        parent_event_id=(
            f"{row.source_id}:{row.source_version}:"
            f"{row.parent_source_row_key}"
            if row.parent_source_row_key else None),
        duplicate_of=None,
        timing_variants=(_iso(t0), _iso(central), _iso(t1)),
    )


def deduplicate(events: list[EventIdentity]) -> list[EventIdentity]:
    """Cross-source dedup: never merge — mark duplicates instead.

    Two events are duplicates when their intervals overlap AND they
    share mechanism and basin. The canonical record is the one with
    the best (narrowest) timing class; others get duplicate_of set.
    Unresolved duplicates are marked by making timing class
    COARSE_OR_UNRESOLVED (caller maps to CENSORED_OR_AMBIGUOUS).
    """
    order = {c: i for i, c in enumerate(TIMING_CLASSES)}
    out: list[EventIdentity] = []
    seen: dict[str, EventIdentity] = {}
    for ev in events:
        if ev.event_id in seen:
            raise ValueError(f"event_id collision: {ev.event_id}")
        dup_of = None
        for prev in out:
            if (prev.mechanism != ev.mechanism or prev.basin != ev.basin):
                continue
            if _parse(prev.interval_end) <= _parse(ev.interval_start) or \
                    _parse(ev.interval_end) <= _parse(prev.interval_start):
                continue
            better = prev if order[prev.timing_class] <= \
                order[ev.timing_class] else ev
            worse = ev if better is prev else prev
            if better is ev:
                # Re-mark the earlier event as the duplicate.
                idx = out.index(prev)
                out[idx] = _with_dup(prev, ev.event_id)
                dup_of = None
            else:
                dup_of = prev.event_id
            break
        if dup_of:
            ev = _with_dup(ev, dup_of)
        seen[ev.event_id] = ev
        out.append(ev)
    return out


def _with_dup(ev: EventIdentity, dup_of: str) -> EventIdentity:
    return EventIdentity(**{**ev.__dict__, "duplicate_of": dup_of})


def validate_cascade_graph(events: list[EventIdentity]) -> None:
    """Cascade integrity: references must be bound and non-self; a
    cascade group must be atomic (all members share one basin-side
    holdout group — checked via basin equality as the assignment is
    basin-derived)."""
    ids = {e.event_id for e in events}
    groups: dict[str, set[str]] = {}
    for e in events:
        if e.parent_event_id is not None:
            if e.parent_event_id not in ids:
                raise ValueError(
                    f"{e.event_id}: dangling parent_event_id "
                    f"{e.parent_event_id}")
            if e.parent_event_id == e.event_id:
                raise ValueError(f"{e.event_id}: self parent reference")
        if e.duplicate_of is not None and e.duplicate_of not in ids:
            raise ValueError(
                f"{e.event_id}: dangling duplicate_of {e.duplicate_of}")
        groups.setdefault(e.cascade_group_id, set()).add(e.basin)
    for gid, basins in groups.items():
        if len(basins) > 1:
            raise ValueError(
                f"cascade group {gid} spans basins {sorted(basins)}; "
                f"cascade members must be atomic within one group")


# ------------------------------------------------------- opportunities

@dataclass(frozen=True)
class ObservationOpportunity:
    """A window in which a source could have observed an event."""
    source_id: str
    basin: str
    window_start: str
    window_end: str
    coverage_class: str   # OBSERVED_FULL / OBSERVED_PARTIAL
    opportunity_id: str = ""   # digest-derived when empty


@dataclass(frozen=True)
class ControlWindow:
    """A non-event window — only valid where opportunity exists."""
    source_id: str
    basin: str
    window_start: str
    window_end: str
    control_digest: str = ""
    opportunity_id: str = ""   # covering opportunity, carried through
    unit_id: str = ""          # consuming-side unit axis (== basin here)


def build_controls(source_id: str, basin: str,
                   candidate_windows: list[tuple[str, str]],
                   opportunities: list[ObservationOpportunity],
                   events: list[EventIdentity]) -> list[ControlWindow]:
    """Control = window with opportunity coverage AND no event overlap.

    Missing coverage or absence of a report never yields a negative.
    """
    opps = [o for o in opportunities
            if o.source_id == source_id and o.basin == basin]
    evs = [e for e in events
           if e.basin == basin]
    controls = []
    for w0, w1 in candidate_windows:
        s, e = _parse(w0), _parse(w1)
        covering = next(
            (o for o in opps
             if _parse(o.window_start) <= s
             and e <= _parse(o.window_end)), None)
        if covering is None:
            continue  # no opportunity => cannot be a control
        overlaps_event = any(
            _parse(ev.interval_start) < e and s < _parse(ev.interval_end)
            for ev in evs)
        if overlaps_event:
            continue
        opp_id = covering.opportunity_id or _sha(
            f"opp|{covering.source_id}|{covering.basin}|"
            f"{covering.window_start}|{covering.window_end}")
        controls.append(ControlWindow(
            source_id=source_id, basin=basin,
            window_start=_iso(s), window_end=_iso(e),
            control_digest=_sha(f"{source_id}|{basin}|{w0}|{w1}"),
            opportunity_id=opp_id, unit_id=basin))
    return controls


# ----------------------------------------------------------- holdouts

@dataclass(frozen=True)
class HoldoutAssignment:
    """Basin-group holdout plan produced BEFORE eligibility filtering."""
    assignments: dict           # event_id -> group ("train"/"val"/"test")
    basin_of_event: dict        # event_id -> basin
    basin_groups: dict          # group -> frozenset of basins
    evaluation_regions: tuple
    embargo_seconds: int
    split_of_group: dict = field(default_factory=dict)  # group -> split


def assign_holdouts(events: list[EventIdentity],
                    group_of_basin: dict[str, str],
                    split_of_group: dict[str, str],
                    evaluation_regions: tuple[str, ...],
                    embargo_seconds: int | None,
                    eligible_universe: set[str] | None = None
                    ) -> HoldoutAssignment:
    """Assign every event to a holdout split via its basin's geographic
    group.

    ``group_of_basin`` maps each basin to a geographic group id;
    ``split_of_group`` maps each group id to "train"/"val"/"test".
    Geographic groups are basin-level units — a split may span several
    groups, which is what makes >=3 disjoint groups meaningful.

    Rules (spec §4):
    - assignment BEFORE filtering: every event must receive a split,
      not only eligible ones;
    - >=3 disjoint geographic groups;
    - >=2 independent basins in the eligible universe;
    - evaluation regions come from test groups only and there must be
      >=2 of them;
    - Langtang-only evaluation rejected by construction;
    - cascade groups are atomic (all members share a basin already,
      enforced by validate_cascade_graph);
    - embargo_seconds=None blocks the plan.
    """
    if embargo_seconds is None:
        raise ValueError("embargo_seconds unknown — plan blocked "
                         "(unknown component)")
    if embargo_seconds < 0:
        raise ValueError("embargo_seconds must be non-negative")
    if not events:
        raise ValueError("empty event universe")
    valid_splits = {"train", "val", "test"}
    bad = set(split_of_group.values()) - valid_splits
    if bad:
        raise ValueError(f"unknown split labels: {sorted(bad)}")

    validate_cascade_graph(events)

    event_groups = {group_of_basin.get(e.basin) for e in events}
    if None in event_groups:
        missing = {e.basin for e in events
                   if e.basin not in group_of_basin}
        raise ValueError(f"basins lack group assignment: {missing}")
    n_groups = len(set(group_of_basin.values()))
    if n_groups < _MIN_GEO_GROUPS:
        raise ValueError(
            f"holdout needs >= {_MIN_GEO_GROUPS} disjoint geographic "
            f"groups; got {n_groups}")
    unsplit = set(group_of_basin.values()) - set(split_of_group)
    if unsplit:
        raise ValueError(f"groups lack a split assignment: "
                         f"{sorted(unsplit)}")

    universe = eligible_universe or {e.basin for e in events}
    if universe <= _SINGLE_BOX_BASINS:
        raise ValueError("single-box (Langtang-only) validation "
                         "rejected by construction")
    if len(universe) < 2:
        raise ValueError("eligible universe must contain >=2 "
                         "independent basins")

    test_basins = {b for b, g in group_of_basin.items()
                   if split_of_group[g] == "test"}
    if set(evaluation_regions) - test_basins:
        raise ValueError("evaluation regions must be drawn from test "
                         "groups only")
    if len(set(evaluation_regions)) < _MIN_EVAL_REGIONS:
        raise ValueError(">=2 independent evaluation regions required")

    assignments = {e.event_id: split_of_group[group_of_basin[e.basin]]
                   for e in events}
    basin_of_event = {e.event_id: e.basin for e in events}
    basin_groups: dict[str, set] = {}
    for basin, grp in group_of_basin.items():
        basin_groups.setdefault(grp, set()).add(basin)
    return HoldoutAssignment(
        assignments=assignments,
        basin_of_event=basin_of_event,
        basin_groups={g: frozenset(b) for g, b in basin_groups.items()},
        evaluation_regions=tuple(sorted(evaluation_regions)),
        embargo_seconds=embargo_seconds,
        split_of_group=dict(split_of_group),
    )
