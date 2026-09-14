"""Executable v0 cutoff/target policy primitives (policy doc binding).

Implements INFORMATION_CUTOFF_TARGET_POLICY_V0 as pure, fail-closed
functions.  Every rule below is a contract check, not an advisory:

* event-time precision classes bound which forecast horizons may exist;
* target labels are three-valued and censored is never a negative;
* the temporal embargo is the max of horizon, label interval,
  observation latency, and cascade duration — unknown means blocked;
* reanalysis is never a forecast feature;
* all timestamps are explicit UTC — naive or malformed values reject.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional, Sequence

# Candidate horizons in seconds, ordered narrowest to widest.
HORIZON_SECONDS = {
    "6h": 6 * 3600,
    "24h": 24 * 3600,
    "48h": 2 * 86400,
    "72h": 3 * 86400,
    "7d": 7 * 86400,
    "14d": 14 * 86400,
    "30d": 30 * 86400,
}
HORIZON_ORDER = tuple(HORIZON_SECONDS)

_HOUR = 3600
_DAY = 86400


class EventTimeClass(str, Enum):
    """Event-time precision classes (policy table)."""

    EXACT_TIMESTAMP = "EXACT_TIMESTAMP"          # uncertainty <= 1 hour
    EXACT_DAY = "EXACT_DAY"                      # uncertainty <= 24 hours
    INTERVAL_LE_7D = "INTERVAL_LE_7D"            # interval <= 7 days
    INTERVAL_8_30D = "INTERVAL_8_30D"            # interval 8-30 days
    COARSE_OR_UNRESOLVED = "COARSE_OR_UNRESOLVED"  # month/season/malformed


class TargetState(str, Enum):
    """Three-valued occurrence target; CENSORED is never folded to NEGATIVE."""

    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    CENSORED_OR_AMBIGUOUS = "CENSORED_OR_AMBIGUOUS"


class ForecastDataClass(str, Enum):
    """Forecast-data classes; only REFORECAST/ARCHIVED_OPERATIONAL may
    appear in a forecast experiment feature matrix."""

    REANALYSIS = "REANALYSIS"                    # ERA5/ERA5-Land: retrospective only
    REFORECAST = "REFORECAST"                    # fixed-model hindcast/reforecast
    ARCHIVED_OPERATIONAL = "ARCHIVED_OPERATIONAL"  # issue-time vintage archive
    CURRENT_FEED = "CURRENT_FEED"                # rolling feed: not an archive


class RegimeMode(str, Enum):
    RETROSPECTIVE_REGIME = "RETROSPECTIVE_REGIME"
    FORECAST_REGIME = "FORECAST_REGIME"


# ---------------------------------------------------------------------
# Strict timestamp / numeric handling
# ---------------------------------------------------------------------

_RFC3339_UTC_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)$")


def parse_strict_utc(value: Any) -> Optional[float]:
    """Parse a timestamp to epoch seconds.

    Accepts finite epoch numbers, or RFC3339 strings with an explicit
    UTC marker (``Z`` or ``+00:00``).  Naive timestamps, NaN, infinities,
    and malformed strings return ``None``.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, str):
        text = value.strip()
        if not _RFC3339_UTC_RE.match(text):
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo != timezone.utc:
            return None
        return parsed.timestamp()
    return None


def require_finite_seconds(value: Any) -> Optional[float]:
    """Strict finite numeric seconds; rejects NaN/inf/bool/None."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def classify_event_time(
        uncertainty_seconds: Optional[float]) -> EventTimeClass:
    """Map an event-time uncertainty width to its precision class.

    ``None``, negative, NaN, infinite, or > 30-day uncertainty is
    COARSE_OR_UNRESOLVED — no occurrence forecast target may be built
    from it.
    """
    width = require_finite_seconds(uncertainty_seconds)
    if width is None or width < 0:
        return EventTimeClass.COARSE_OR_UNRESOLVED
    if width <= _HOUR:
        return EventTimeClass.EXACT_TIMESTAMP
    if width <= _DAY:
        return EventTimeClass.EXACT_DAY
    if width <= 7 * _DAY:
        return EventTimeClass.INTERVAL_LE_7D
    if width <= 30 * _DAY:
        return EventTimeClass.INTERVAL_8_30D
    return EventTimeClass.COARSE_OR_UNRESOLVED


# Binding class-level horizon allowlist (conservative reading: the width
# gate alone never widens a class bound — a 12-day scene interval is
# INTERVAL_8_30D and therefore admits only the 30-day horizon).
_CLASS_HORIZON_ALLOWLIST = {
    EventTimeClass.EXACT_TIMESTAMP: frozenset(HORIZON_ORDER),
    EventTimeClass.EXACT_DAY: frozenset({"48h", "72h", "7d", "14d", "30d"}),
    EventTimeClass.INTERVAL_LE_7D: frozenset({"7d", "14d", "30d"}),
    EventTimeClass.INTERVAL_8_30D: frozenset({"30d"}),
    EventTimeClass.COARSE_OR_UNRESOLVED: frozenset(),
}


def as_event_class(value: Any) -> Optional[EventTimeClass]:
    """Total coercion: return the enum for a valid value or name, else
    ``None`` — callers fail closed instead of raising (C12)."""
    if isinstance(value, EventTimeClass):
        return value
    if isinstance(value, str):
        try:
            return EventTimeClass(value)
        except ValueError:
            try:
                return EventTimeClass[value]
            except KeyError:
                return None
    return None


def eligible_horizons(
        event_class: Any, *,
        event_uncertainty_seconds: Any,
        observation_latency_seconds: Optional[float],
        processing_latency_seconds: Optional[float]) -> list[str]:
    """Return the horizons admissible for this event-time class.

    A horizon is eligible only when its width is at least the event-time
    uncertainty plus verified observation and processing/availability
    latency.  Missing, negative, NaN, infinite components — and any
    unrecognised event class — block every horizon.
    """
    klass = as_event_class(event_class)
    if klass is None:
        return []
    components = (require_finite_seconds(event_uncertainty_seconds),
                  require_finite_seconds(observation_latency_seconds),
                  require_finite_seconds(processing_latency_seconds))
    if any(c is None or c < 0 for c in components):
        return []
    required = sum(components)  # type: ignore[arg-type]
    allowed = _CLASS_HORIZON_ALLOWLIST[klass]
    return [h for h in HORIZON_ORDER
            if h in allowed and HORIZON_SECONDS[h] >= required]


# Observation-opportunity states used to derive target truth.
OPPORTUNITY_STATES = (
    "OBSERVED_FULL", "OBSERVED_PARTIAL", "UNOBSERVED", "UNKNOWN")


def _interval_wellformed(interval: Mapping[str, Any]) -> bool:
    start = require_finite_seconds(interval.get("start"))
    end = require_finite_seconds(interval.get("end"))
    return (start is not None and end is not None and
            start <= end and isinstance(interval.get("adjudicated"), bool))


def assign_target_state(
        window_start: float, window_end: float,
        event_intervals: Sequence[Mapping[str, Any]], *,
        opportunity_state: str) -> TargetState:
    """Assign the three-valued occurrence label for one target window.

    Truth is *derived*, never caller-asserted: ``opportunity_state`` must
    come from a validated ObservationOpportunityV0 record.  ``NEGATIVE``
    requires ``OBSERVED_FULL`` and no intersecting interval.  ``POSITIVE``
    requires a fully contained, adjudicated interval AND no other
    ambiguous overlap — an unresolved, unadjudicated, malformed, or
    boundary-clipping interval is dominant and censors the window even
    when a clean positive also exists (B13).
    """
    ws = require_finite_seconds(window_start)
    we = require_finite_seconds(window_end)
    if ws is None or we is None or ws >= we:
        return TargetState.CENSORED_OR_AMBIGUOUS
    if opportunity_state != "OBSERVED_FULL":
        return TargetState.CENSORED_OR_AMBIGUOUS
    contained_adjudicated = False
    ambiguous_overlap = False
    for interval in event_intervals:
        if not _interval_wellformed(interval):
            # Any malformed interval is a data-integrity defect: it
            # cannot be proven not to overlap.
            ambiguous_overlap = True
            continue
        start = float(interval["start"])
        end = float(interval["end"])
        if end <= ws or start >= we:
            continue
        if interval["adjudicated"] is True and \
                start >= ws and end <= we:
            contained_adjudicated = True
        else:
            ambiguous_overlap = True
    if ambiguous_overlap:
        return TargetState.CENSORED_OR_AMBIGUOUS
    if contained_adjudicated:
        return TargetState.POSITIVE
    return TargetState.NEGATIVE


def derive_control_state(
        window_start: float, window_end: float,
        event_intervals: Sequence[Mapping[str, Any]], *,
        opportunity_state: str) -> TargetState:
    """Derive a control-window state from validated inputs (B12).

    Controls are never POSITIVE: an event overlapping a control window
    makes it CENSORED_OR_AMBIGUOUS.  NEGATIVE requires a full
    observation opportunity and zero event overlap.
    """
    state = assign_target_state(
        window_start, window_end, event_intervals,
        opportunity_state=opportunity_state)
    if state is TargetState.NEGATIVE:
        return TargetState.NEGATIVE
    return TargetState.CENSORED_OR_AMBIGUOUS


def assign_target_state_typed(
        opportunity: Any,
        event_labels: Sequence[Any]) -> TargetState:
    """Typed-only target assignment (C13).

    Accepts an ``ObservationOpportunityV0`` and ``EventLabelV0``
    records — never raw dicts or caller-asserted state.  Every input is
    validated via ``problems()``; any invalid record censors the
    window.  The window is taken from the opportunity record itself.
    """
    # Exact class identity, not name-matching (C04/C13) — a spoofed
    # duck type cannot satisfy this check.
    from .records import EventLabelV0, ObservationOpportunityV0
    if type(opportunity) is not ObservationOpportunityV0:
        return TargetState.CENSORED_OR_AMBIGUOUS
    if opportunity.problems():
        return TargetState.CENSORED_OR_AMBIGUOUS
    ws = parse_strict_utc(opportunity.window_start)
    we = parse_strict_utc(opportunity.window_end)
    intervals: list[dict[str, Any]] = []
    for label in event_labels:
        if type(label) is not EventLabelV0 or label.problems():
            return TargetState.CENSORED_OR_AMBIGUOUS
        s = parse_strict_utc(label.event_time_start)
        e = parse_strict_utc(label.event_time_end)
        intervals.append({"start": s, "end": e,
                          "adjudicated": label.adjudicated})
    return assign_target_state(
        ws, we, intervals,  # type: ignore[arg-type]
        opportunity_state=opportunity.state)


def derive_control_state_typed(
        control: Any,
        opportunity: Any,
        event_labels: Sequence[Any]) -> TargetState:
    """Typed control derivation (B12/C13): the control's window must
    equal the linked opportunity's window; state is derived, never
    caller-asserted."""
    from .records import (ControlWindowV0, EventLabelV0,
                          ObservationOpportunityV0)
    if type(control) is not ControlWindowV0 or \
            type(opportunity) is not ObservationOpportunityV0:
        return TargetState.CENSORED_OR_AMBIGUOUS
    if control.problems() or opportunity.problems():
        return TargetState.CENSORED_OR_AMBIGUOUS
    if control.opportunity_id != opportunity.opportunity_id:
        return TargetState.CENSORED_OR_AMBIGUOUS
    ws = parse_strict_utc(control.window_start)
    we = parse_strict_utc(control.window_end)
    ows = parse_strict_utc(opportunity.window_start)
    owe = parse_strict_utc(opportunity.window_end)
    if None in (ws, we, ows, owe) or (ws, we) != (ows, owe):
        return TargetState.CENSORED_OR_AMBIGUOUS
    intervals = []
    for label in event_labels:
        if type(label) is not EventLabelV0 or label.problems():
            return TargetState.CENSORED_OR_AMBIGUOUS
        intervals.append({
            "start": parse_strict_utc(label.event_time_start),
            "end": parse_strict_utc(label.event_time_end),
            "adjudicated": label.adjudicated})
    return derive_control_state(
        ws, we, intervals,  # type: ignore[arg-type]
        opportunity_state=opportunity.state)


def embargo_seconds(*, max_horizon_seconds: Optional[float],
                    max_label_interval_seconds: Optional[float],
                    max_observation_latency_seconds: Optional[float],
                    max_cascade_seconds: Optional[float]
                    ) -> Optional[float]:
    """Temporal embargo = max(horizon, label interval, observation
    latency, cascade duration).  ``None`` means a component is unknown
    or invalid and the experiment is blocked."""
    parts = tuple(require_finite_seconds(v) for v in (
        max_horizon_seconds, max_label_interval_seconds,
        max_observation_latency_seconds, max_cascade_seconds))
    if any(p is None or p < 0 for p in parts):
        return None
    return max(parts)


# Ordered cutoff chain for operational-like evaluation.  Each field must
# be <= the next.  ``archive_availability`` and ``local_retrieval_time``
# are checked separately (provenance / archive rules below).
CUTOFF_ORDER = (
    "source_observation_end",
    "source_processing_complete",
    "source_publication",
    "feature_availability",
    "forecast_initialization",
    "forecast_issue",
    "forecast_valid_start",
    "forecast_valid_end",
)


def cutoff_order_problems(cutoff: Mapping[str, Any]) -> list[str]:
    """Verify the monotonic operational cutoff chain under strict UTC.

    All fields in :data:`CUTOFF_ORDER` must be present and parse as
    explicit-UTC timestamps (or finite epoch seconds).  Missing fields,
    naive timestamps, NaN/infinity, and inversions are problems.
    Additionally: ``archive_availability`` (when present) must be >=
    ``forecast_issue`` — an object cannot be archived before it is
    issued — and ``local_retrieval_time`` (provenance only) must be >=
    ``archive_availability``; retrieval never substitutes for
    provider-side availability.
    """
    problems: list[str] = []
    present: list[tuple[str, float]] = []
    for field in CUTOFF_ORDER:
        raw = cutoff.get(field)
        if raw is None:
            problems.append(f"cutoff field {field!r} is missing")
            continue
        value = parse_strict_utc(raw)
        if value is None:
            problems.append(
                f"cutoff field {field!r} is not an explicit-UTC "
                "timestamp or finite epoch seconds")
            continue
        present.append((field, value))
    for (prev_field, prev), (next_field, nxt) in zip(present, present[1:]):
        if nxt < prev:
            problems.append(
                f"cutoff order violated: {next_field!r} ({nxt}) precedes "
                f"{prev_field!r} ({prev})")
    by_name = dict(present)
    archive = cutoff.get("archive_availability")
    if archive is not None:
        archive_s = parse_strict_utc(archive)
        if archive_s is None:
            problems.append("archive_availability is not a strict-UTC "
                            "timestamp")
        elif "forecast_issue" in by_name and \
                archive_s < by_name["forecast_issue"]:
            problems.append("archive_availability precedes "
                            "forecast_issue — impossible archive")
    retrieval = cutoff.get("local_retrieval_time")
    archive_s = parse_strict_utc(archive) if archive is not None else None
    if retrieval is not None:
        retrieval_s = parse_strict_utc(retrieval)
        if retrieval_s is None:
            problems.append("local_retrieval_time is not a strict-UTC "
                            "timestamp")
        elif archive_s is not None and retrieval_s < archive_s:
            problems.append("local_retrieval_time precedes "
                            "archive_availability — retrieval cannot "
                            "precede archive")
    return problems
