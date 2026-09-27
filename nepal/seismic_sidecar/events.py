"""Typed event/opportunity/control package for the seismic sidecar.

Closes S10: a detection claim needs labelled ground truth — known
event windows and observed non-event (control) windows — aligned to
the feature frame WITHOUT leakage and kept strictly outside the
waveform predictor set and the semantic feature digest.

Records:

- ``SeismicEventV0`` — a declared seismic event (e.g. an avalanche
  or earthquake detection window) bound to a source catalog.
- ``OpportunityWindowV0`` — a declared interval during which an
  event COULD have been observed (station live, sensor deployed,
  channel observable) — the honest basis for controls.
- ``SeismicEventPackageV0`` — the assembled, digested package:
  events + opportunity + derived controls + the alignment ledger.

Alignment rules (all fail-closed):

- event origin after the declared cutoff → rejected (leakage);
- event outside the station's waveform coverage → recorded as
  ``censored``, never silently kept;
- duplicate event ids → rejected;
- cascade events (distinct ids within the declared dedup window of
  an earlier event) → flagged as cascade, kept but labelled;
- controls derive ONLY from opportunity windows carrying no event;
- opportunity windows with unknown/partial coverage are not valid
  negatives — they drop into the ledger.

Labels attach to frame rows as a SEPARATE column surface
(``event_label``) returned as a distinct mapping — never merged
into the semantic feature digest or predictor columns.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from nepal.research_v0._hashing import sha256_canonical
from nepal.research_v0.policy import parse_strict_utc

SEISMIC_EVENT_TYPE = "SeismicEventV0"
OPPORTUNITY_WINDOW_TYPE = "OpportunityWindowV0"
EVENT_PACKAGE_TYPE = "SeismicEventPackageV0"

EVENT_LABELS = frozenset({"EVENT", "CONTROL", "CENSORED", "CASCADE"})


def _utc(text: Any) -> float | None:
    return parse_strict_utc(text)


@dataclass(frozen=True)
class SeismicEventV0:
    """One declared seismic event bound to a catalog source."""
    event_id: str
    origin_iso: str
    event_class: str = "seismic_event"
    source_catalog: str = ""
    station_ids: tuple = ()
    latitude: float | None = None
    longitude: float | None = None
    magnitude: float | None = None
    problems: tuple = ()

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not isinstance(self.event_id, str) or \
                not self.event_id.strip():
            problems.append("event_id must be a non-empty string")
        if _utc(self.origin_iso) is None:
            problems.append("origin_iso must be an explicit-UTC "
                            "timestamp")
        if not isinstance(self.source_catalog, str) or \
                not self.source_catalog.strip():
            problems.append("source_catalog must name the "
                            "catalog that declared this event")
        if self.event_class not in ("seismic_event", "avalanche",
                                    "earthquake", "other"):
            problems.append(
                f"event_class {self.event_class!r} is not a "
                "declared class")
        if not isinstance(self.station_ids, (list, tuple)) or \
                any(not isinstance(s, str) or not s.strip()
                    for s in self.station_ids):
            problems.append("station_ids must be a tuple of "
                            "non-empty strings (empty = all "
                            "stations)")
        for name in ("latitude", "longitude", "magnitude"):
            v = getattr(self, name)
            if v is not None and (
                    isinstance(v, bool) or
                    not isinstance(v, (int, float))):
                problems.append(f"{name} must be a finite number "
                                "or absent")
        if self.latitude is not None and \
                not -90.0 <= self.latitude <= 90.0:
            problems.append("latitude outside [-90, 90]")
        if self.longitude is not None and \
                not -180.0 <= self.longitude <= 180.0:
            problems.append("longitude outside [-180, 180]")
        return problems

    def to_dict(self) -> dict:
        d = asdict(self)
        d["station_ids"] = list(self.station_ids)
        d["problems"] = list(self.problems)
        d["record_type"] = SEISMIC_EVENT_TYPE
        return d


@dataclass(frozen=True)
class OpportunityWindowV0:
    """A declared interval in which an event COULD be observed on a
    station — the honest basis for negative controls."""
    station_id: str
    window_start: str
    window_end: str
    coverage_basis: str = ""
    coverage_fraction: float = 1.0
    problems: tuple = ()

    def validate(self) -> list[str]:
        problems: list[str] = []
        for name in ("station_id", "coverage_basis"):
            v = getattr(self, name)
            if not isinstance(v, str) or not v.strip():
                problems.append(f"{name} must be a non-empty "
                                "string")
        ws, we = _utc(self.window_start), _utc(self.window_end)
        if ws is None or we is None:
            problems.append("window_start/window_end must be "
                            "explicit-UTC timestamps")
        elif we <= ws:
            problems.append("window_end precedes window_start — "
                            "inverted opportunity window")
        if isinstance(self.coverage_fraction, bool) or \
                not isinstance(self.coverage_fraction,
                               (int, float)) or \
                not 0.0 <= self.coverage_fraction <= 1.0:
            problems.append("coverage_fraction must be in [0, 1]")
        return problems

    def to_dict(self) -> dict:
        d = asdict(self)
        d["problems"] = list(self.problems)
        d["record_type"] = OPPORTUNITY_WINDOW_TYPE
        return d


@dataclass(frozen=True)
class SeismicEventPackageV0:
    """The digested event/opportunity/control surface — labels are
    bound by digest and kept out of the predictor channel."""
    events: tuple = ()
    opportunities: tuple = ()
    controls: tuple = ()
    label_map: Mapping = field(default_factory=dict)
    censored: tuple = ()
    cascade: tuple = ()
    events_digest: str = ""
    opportunity_digest: str = ""
    controls_digest: str = ""
    labels_digest: str = ""
    package_digest: str = ""
    problems: tuple = ()

    def to_dict(self) -> dict:
        return {
            "record_type": EVENT_PACKAGE_TYPE,
            "events": [e.to_dict() for e in self.events],
            "opportunities": [o.to_dict() for o in self.opportunities],
            "controls": list(self.controls),
            "label_map": dict(self.label_map),
            "censored": list(self.censored),
            "cascade": list(self.cascade),
            "events_digest": self.events_digest,
            "opportunity_digest": self.opportunity_digest,
            "controls_digest": self.controls_digest,
            "labels_digest": self.labels_digest,
            "package_digest": self.package_digest,
            "problems": list(self.problems)}


def build_event_package(
        *,
        events: Sequence[SeismicEventV0],
        opportunities: Sequence[OpportunityWindowV0],
        frame_rows: Sequence[Mapping[str, Any]],
        cutoff_iso: str,
        dedup_seconds: float = 120.0) -> SeismicEventPackageV0:
    """Assemble the package and align labels to frame rows.

    ``frame_rows`` are the window-level rows (station_id,
    window_start, window_end); ``cutoff_iso`` is the declared
    information cutoff — no event after it may label a window.
    Labels are returned inside ``label_map`` keyed by
    ``(station_id, window_start)`` — a separate surface, never a
    predictor column.
    """
    problems: list[str] = []
    cutoff = _utc(cutoff_iso)
    if cutoff is None:
        return SeismicEventPackageV0(problems=(
            "cutoff_iso must be an explicit-UTC timestamp",))
    if isinstance(dedup_seconds, bool) or \
            not isinstance(dedup_seconds, (int, float)) or \
            not dedup_seconds >= 0:
        return SeismicEventPackageV0(problems=(
            "dedup_seconds must be a non-negative number",))

    # ---- event floor -------------------------------------------------
    seen_ids: set[str] = set()
    good_events: list[SeismicEventV0] = []
    censored: list[str] = []
    for e in events:
        if not isinstance(e, SeismicEventV0):
            problems.append("events must be SeismicEventV0 records")
            return SeismicEventPackageV0(problems=tuple(problems))
        ep = e.validate()
        if ep:
            problems.extend(f"event {e.event_id}: {p}" for p in ep)
            continue
        if e.event_id in seen_ids:
            problems.append(f"duplicate event_id {e.event_id!r} — "
                            "one record per event")
            continue
        seen_ids.add(e.event_id)
        origin = _utc(e.origin_iso)
        if origin > cutoff:
            problems.append(
                f"event {e.event_id}: origin after the declared "
                "cutoff — post-cutoff evidence is leakage")
            continue
        good_events.append(e)
    if problems:
        return SeismicEventPackageV0(problems=tuple(problems))

    # ---- opportunity floor -------------------------------------------
    good_opps: list[OpportunityWindowV0] = []
    for o in opportunities:
        if not isinstance(o, OpportunityWindowV0):
            problems.append("opportunities must be "
                            "OpportunityWindowV0 records")
            return SeismicEventPackageV0(problems=tuple(problems))
        op = o.validate()
        if op:
            problems.extend(
                f"opportunity {o.station_id}: {p}" for p in op)
            continue
        good_opps.append(o)
    if problems:
        return SeismicEventPackageV0(problems=tuple(problems))

    # ---- frame coverage index ----------------------------------------
    span_by_station: dict[str, list] = {}
    for row in frame_rows:
        sta = str(row.get("station_id", ""))
        ws, we = _utc(row.get("window_start")), \
            _utc(row.get("window_end"))
        if ws is None or we is None:
            continue
        span_by_station.setdefault(sta, []).append((ws, we))

    # ---- cascade / censored classification ----------------------------
    cascade: list[str] = []
    aligned: list[dict] = []
    by_origin = sorted(good_events,
                       key=lambda e: _utc(e.origin_iso))
    prev_origin: float | None = None
    prev_id = ""
    for e in by_origin:
        origin = _utc(e.origin_iso)
        stations = tuple(e.station_ids) or \
            tuple(sorted(span_by_station))
        covered = any(
            any(ws <= origin <= we
                for ws, we in span_by_station.get(s, ()))
            for s in stations)
        if not covered:
            censored.append(e.event_id)
        if prev_origin is not None and \
                origin - prev_origin < dedup_seconds:
            cascade.append(
                f"{e.event_id} within {dedup_seconds:g}s of "
                f"{prev_id} — cascade label retained, distinct "
                "id kept")
        prev_origin, prev_id = origin, e.event_id
        aligned.append(e)

    # ---- label map ----------------------------------------------------
    label_map: dict[str, str] = {}
    for e in aligned:
        origin = _utc(e.origin_iso)
        stations = tuple(e.station_ids) or \
            tuple(sorted(span_by_station))
        for sta in stations:
            for ws, we in span_by_station.get(sta, ()):
                if ws <= origin < we:
                    label_map[f"{sta}|{ws}"] = "EVENT"

    # ---- controls: opportunity windows carrying no event --------------
    controls: list[dict] = []
    for o in good_opps:
        ws, we = _utc(o.window_start), _utc(o.window_end)
        event_inside = any(
            ws <= _utc(e.origin_iso) < we
            for e in aligned
            if not e.station_ids or o.station_id in e.station_ids)
        if event_inside:
            continue
        if o.coverage_fraction < 1.0:
            # A partially observed window is not a valid negative —
            # an event could hide in the unobserved part.
            censored.append(
                f"opportunity {o.station_id} {o.window_start}: "
                f"partial coverage {o.coverage_fraction}")
            continue
        controls.append({
            "station_id": o.station_id,
            "window_start": o.window_start,
            "window_end": o.window_end,
            "coverage_basis": o.coverage_basis})
        # EVENT labels take precedence — a control may never
        # overwrite an event on the same (station, window) key.
        key = f"{o.station_id}|{ws}"
        if label_map.get(key) != "EVENT":
            label_map[key] = "CONTROL"

    events_digest = sha256_canonical(
        [e.to_dict() for e in aligned])
    opp_digest = sha256_canonical(
        [o.to_dict() for o in good_opps])
    ctl_digest = sha256_canonical(controls)
    labels_digest = sha256_canonical(
        dict(sorted(label_map.items())))
    pkg = {
        "events_digest": events_digest,
        "opportunity_digest": opp_digest,
        "controls_digest": ctl_digest,
        "labels_digest": labels_digest,
        "censored": sorted(censored),
        "cascade": sorted(cascade),
        "n_aligned_events": len(aligned),
        "n_controls": len(controls),
        "cutoff": cutoff_iso,
        "dedup_seconds": dedup_seconds}
    return SeismicEventPackageV0(
        events=tuple(aligned),
        opportunities=tuple(good_opps),
        controls=tuple(controls),
        label_map=dict(label_map),
        censored=tuple(sorted(censored)),
        cascade=tuple(sorted(cascade)),
        events_digest=events_digest,
        opportunity_digest=opp_digest,
        controls_digest=ctl_digest,
        labels_digest=labels_digest,
        package_digest=sha256_canonical(pkg),
        problems=())


__all__ = [
    "SEISMIC_EVENT_TYPE", "OPPORTUNITY_WINDOW_TYPE",
    "EVENT_PACKAGE_TYPE", "EVENT_LABELS",
    "SeismicEventV0", "OpportunityWindowV0",
    "SeismicEventPackageV0", "build_event_package"]
