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

# Opportunity states and control target states mirror the frozen
# contract vocabulary (policy.OPPORTUNITY_STATES / TargetState).
OPPORTUNITY_STATES = (
    "OBSERVED_FULL", "OBSERVED_PARTIAL", "UNOBSERVED", "UNKNOWN")
CONTROL_STATES = ("NEGATIVE", "CENSORED_OR_AMBIGUOUS")

_OPP_RANK = {"OBSERVED_FULL": 0, "OBSERVED_PARTIAL": 1,
             "UNOBSERVED": 2, "UNKNOWN": 3}


@dataclass(frozen=True)
class ObservationOpportunity:
    """A window in which a source could have observed an event.

    Mirrors ObservationOpportunityV0: identity, platform, window,
    coverage fraction, frame binding, and a controlled state.
    """
    opportunity_id: str
    unit_id: str
    source_id: str
    basin: str
    window_start: str
    window_end: str
    state: str = "UNKNOWN"          # OPPORTUNITY_STATES member
    platform: str = ""
    coverage_fraction: float | None = None
    coverage_quality: str = ""
    detection_threshold: str = ""
    source_as_of: str = ""
    frame_ids: tuple = ()

    def problems(self) -> list[str]:
        probs = []
        for f in ("opportunity_id", "unit_id", "source_id",
                  "window_start", "window_end"):
            if not getattr(self, f):
                probs.append(f"{f} is required")
        if self.state not in OPPORTUNITY_STATES:
            probs.append(f"state {self.state!r} not in "
                         f"{list(OPPORTUNITY_STATES)}")
        if self.state == "OBSERVED_FULL" and (
                self.coverage_fraction is None or
                self.coverage_fraction < 0.999 or
                not self.frame_ids):
            probs.append("OBSERVED_FULL requires coverage ~1.0 and "
                         "frame_ids")
        return probs


@dataclass(frozen=True)
class ControlWindow:
    """A non-event window bound to a real opportunity record.

    ``state`` is derived, never caller-asserted: NEGATIVE only when the
    linked opportunity is OBSERVED_FULL and no event overlaps. All
    covering opportunity ids are preserved for lineage.
    """
    control_id: str
    unit_id: str
    source_id: str
    basin: str
    window_start: str
    window_end: str
    opportunity_id: str
    opportunity_state: str
    state: str                       # CONTROL_STATES member
    covering_opportunity_ids: tuple = ()
    control_digest: str = ""


def _select_opportunity(
        covering: list[ObservationOpportunity]
        ) -> ObservationOpportunity:
    """Deterministic selection when several opportunities cover a
    window: best state rank, then smallest opportunity_id — stable and
    reproducible. Other covering ids are retained in lineage."""
    return sorted(covering,
                  key=lambda o: (_OPP_RANK[o.state],
                                 o.opportunity_id))[0]


def build_controls(unit_id: str, source_id: str, basin: str,
                   candidate_windows: list[tuple[str, str]],
                   opportunities: list[ObservationOpportunity],
                   events: list[EventIdentity]) -> list[ControlWindow]:
    """Control = window bound to an opportunity record, state derived.

    - every emitted control carries a linked opportunity_id and the
      opportunity's state;
    - NEGATIVE only when the selected opportunity is OBSERVED_FULL and
      no event interval overlaps;
    - missing/partial/unknown/unobserved coverage never yields
      NEGATIVE — the control is CENSORED_OR_AMBIGUOUS;
    - multiple covering opportunities: deterministic selection, all
      covering ids preserved;
    - a window with zero covering opportunities yields no control.
    """
    opps = [o for o in opportunities
            if o.unit_id == unit_id and o.source_id == source_id
            and o.basin == basin]
    for o in opps:
        if o.problems():
            raise ValueError(
                f"invalid opportunity {o.opportunity_id!r}: "
                f"{o.problems()}")
    evs = [e for e in events if e.basin == basin]
    controls = []
    for w0, w1 in candidate_windows:
        s, e = _parse(w0), _parse(w1)
        covering = [o for o in opps
                    if _parse(o.window_start) <= s and
                    e <= _parse(o.window_end)]
        if not covering:
            continue  # no opportunity => no control at all
        opp = _select_opportunity(covering)
        overlaps_event = any(
            _parse(ev.interval_start) < e and s < _parse(ev.interval_end)
            for ev in evs)
        state = ("NEGATIVE"
                 if opp.state == "OBSERVED_FULL" and not overlaps_event
                 else "CENSORED_OR_AMBIGUOUS")
        controls.append(ControlWindow(
            control_id=f"ctl:{unit_id}:{w0}",
            unit_id=unit_id, source_id=source_id, basin=basin,
            window_start=_iso(s), window_end=_iso(e),
            opportunity_id=opp.opportunity_id,
            opportunity_state=opp.state,
            state=state,
            covering_opportunity_ids=tuple(
                sorted(o.opportunity_id for o in covering)),
            control_digest=_sha(
                f"{unit_id}|{source_id}|{basin}|{w0}|{w1}|"
                f"{opp.opportunity_id}")))
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
    valid_splits = {"train", "val", "validation", "test"}
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

    # assignments carry the GEOGRAPHIC group id per event — the
    # HoldoutPlanV0 contract requires event -> declared group, with the
    # split role carried by the group lists.
    assignments = {e.event_id: group_of_basin[e.basin] for e in events}
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
    )


# --------------------------------------------------------- adapters

_ADJUDICATION_STATES = (
    "UNADJUDICATED", "TWO_REVIEW_AGREE", "THIRD_PARTY_ADJUDICATED",
    "DISAGREEMENT_RETAINED")
_POSITIVE_ADJUDICATION = {"TWO_REVIEW_AGREE", "THIRD_PARTY_ADJUDICATED"}

# Timing class -> contract event_time_precision term.
_TIMING_TO_PRECISION = {
    "EXACT_TIMESTAMP": "exact_timestamp",
    "EXACT_DAY": "day",
    "INTERVAL_LE_7D": "interval",
    "INTERVAL_8_30D": "interval",
    "COARSE_OR_UNRESOLVED": "unresolved",
}


def to_event_label(identity: EventIdentity, *,
                   vertical_id: str,
                   geometry_role: str,
                   event_time_basis: str,
                   adjudication_state: str,
                   reviewer_ids: tuple = (),
                   adjudication_notes: str = "",
                   latitude: float | None = None,
                   longitude: float | None = None) -> dict:
    """Serialize an EventIdentity into an EventLabelV0-shaped payload.

    Adjudication is NEVER fabricated: ``adjudication_state`` must be
    passed explicitly, and a positive-admissible state
    (TWO_REVIEW_AGREE / THIRD_PARTY_ADJUDICATED) requires >=2 unique
    reviewer identities — matching the contract validator.
    """
    if adjudication_state not in _ADJUDICATION_STATES:
        raise ValueError(f"adjudication_state {adjudication_state!r} "
                         f"not in {list(_ADJUDICATION_STATES)}")
    if adjudication_state in _POSITIVE_ADJUDICATION:
        if len(set(reviewer_ids)) < 2:
            raise ValueError(
                "positive adjudication requires >=2 unique "
                "reviewer_ids — adjudication is never fabricated")
    return {
        "record_type": "EventLabelV0",
        "event_id": identity.event_id,
        "vertical_id": vertical_id,
        "source_id": identity.source_id,
        "source_version": identity.source_version,
        "event_time_start": identity.interval_start,
        "event_time_end": identity.interval_end,
        "uncertainty_seconds": identity.uncertainty_seconds,
        "event_time_precision":
            _TIMING_TO_PRECISION[identity.timing_class],
        "event_time_basis": event_time_basis,
        "geometry_role": geometry_role,
        "latitude": latitude,
        "longitude": longitude,
        "basin_id": identity.basin,
        "cascade_group_id": identity.cascade_group_id,
        "parent_event_id": identity.parent_event_id or "",
        "duplicate_of": identity.duplicate_of or "",
        "adjudication_state": adjudication_state,
        "adjudication_notes": adjudication_notes,
        "reviewer_ids": tuple(reviewer_ids),
    }


def to_holdout_plan(assignment: HoldoutAssignment, *,
                    group_of_basin: dict[str, str],
                    holdout_plan_id: str,
                    assignment_rule: str = "basin",
                    split_of_group: dict[str, str],
                    test_locked: bool = True) -> dict:
    """Serialize a HoldoutAssignment into a HoldoutPlanV0-shaped
    payload. Preserves assignment-before-filtering, named evaluation
    regions, test_locked, embargo, and complete event assignments."""
    if assignment_rule not in ("basin", "catchment", "macroregion",
                               "fixed_spatial"):
        raise ValueError(f"assignment_rule {assignment_rule!r} "
                         "not in contract vocabulary")
    groups = set(assignment.basin_groups)
    if groups - set(split_of_group):
        raise ValueError("every geographic group must have a split")
    splits = {g: split_of_group[g] for g in groups}
    # Contract vocabulary: evaluation_region_names are drawn from the
    # locked TEST groups. Engine-level regions name basins — translate
    # each to its geographic group; distinct regions that collapse onto
    # one group fail the >=2 unique-name requirement at the contract.
    named = tuple(sorted({
        group_of_basin[b] for b in assignment.evaluation_regions}))
    if len(named) < 2:
        raise ValueError("evaluation regions must map to >=2 distinct "
                         "geographic groups")
    payload = {
        "record_type": "HoldoutPlanV0",
        "holdout_plan_id": holdout_plan_id,
        "assignment_rule": assignment_rule,
        "train_groups": tuple(sorted(
            g for g, s in splits.items() if s == "train")),
        "validation_groups": tuple(sorted(
            g for g, s in splits.items() if s in ("val",
                                                  "validation"))),
        "test_groups": tuple(sorted(
            g for g, s in splits.items() if s == "test")),
        # event -> group (not split): contract requires values inside
        # the declared group names
        "event_assignments": dict(assignment.assignments),
        "evaluation_region_names": named,
        "assigned_before_filtering": True,
        "test_locked": test_locked,
        "embargo_seconds": assignment.embargo_seconds,
    }
    return payload
