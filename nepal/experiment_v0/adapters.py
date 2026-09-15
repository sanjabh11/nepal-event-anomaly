"""Canonical serialized boundaries between producer packages and
``nepal.research_v0`` contract records.

Swarm A (``nepal.science_v0``) produces normalized intermediate
structures; swarm B (``nepal.experiment_v0``) consumes contract records.
The boundary is a *validated mapping* — ``dataclasses.asdict(...)`` or an
equivalent plain dict — never duck typing: every adapter enumerates the
exact required keys, rejects unknown keys, rejects wrong primitive
types, and validates the produced record's ``problems()``.

Boundaries:
- ``event_label_from_identity`` — science_v0.EventIdentity payload ->
  EventLabelV0.
- ``holdout_plan_from_assignment`` — science_v0.HoldoutAssignment
  payload -> HoldoutPlanV0.
- ``vintage_from_request`` — VintageRequest (or its serialized mapping)
  -> ForecastVintageV0.
- ``opportunity_from_science`` — science_v0.ObservationOpportunity
  payload -> ObservationOpportunityV0.
- ``control_from_science`` — science_v0.ControlWindow payload ->
  ControlWindowV0.
- ``regime_assignment_from_artifact`` — science_v0 frozen regime
  artifact dict -> RegimeAssignmentArtifact.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Mapping, Optional, Sequence

from nepal.research_v0.records import (
    ControlWindowV0, EventLabelV0, ForecastVintageV0, HoldoutPlanV0,
    ObservationOpportunityV0)

from .association import RegimeAssignmentArtifact
from .vintages import VintageRequest, build_vintage

# science_v0 timing classes -> EventLabelV0 precision terms (PRECISION_TERMS).
_TIMING_CLASS_TO_PRECISION = {
    "EXACT_TIMESTAMP": "exact_timestamp",
    "EXACT_DAY": "day",
    "INTERVAL_LE_7D": "interval",
    "INTERVAL_8_30D": "interval",
    "COARSE_OR_UNRESOLVED": "unresolved",
}

_IDENTITY_KEYS = frozenset({
    "event_id", "source_id", "source_version", "mechanism",
    "interval_start", "interval_end", "uncertainty_seconds",
    "timing_class", "basin", "cascade_group_id", "parent_event_id",
    "duplicate_of", "timing_variants"})

_ASSIGNMENT_KEYS = frozenset({
    "assignments", "basin_of_event", "basin_groups",
    "evaluation_regions", "embargo_seconds"})

_VALID_SPLITS = frozenset({"train", "val", "test"})


def _payload(obj: Any, name: str) -> Mapping[str, Any]:
    """Accept a plain Mapping or a dataclass instance (asdict boundary)."""
    if isinstance(obj, Mapping):
        return obj
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    raise TypeError(f"{name}: expected a serialized mapping or "
                    f"dataclass payload, got {type(obj).__name__}")


def _require_exact_keys(payload: Mapping[str, Any], required: frozenset,
                        name: str) -> None:
    keys = set(payload)
    missing = sorted(required - keys)
    unknown = sorted(keys - required)
    if missing:
        raise ValueError(f"{name}: missing required keys: {missing}")
    if unknown:
        raise ValueError(f"{name}: unknown keys: {unknown}")


def _req_str(payload: Mapping[str, Any], key: str, name: str) -> str:
    v = payload[key]
    if not isinstance(v, str):
        raise ValueError(f"{name}.{key}: expected str, got "
                         f"{type(v).__name__}")
    return v


def _opt_str(payload: Mapping[str, Any], key: str, name: str) -> str:
    v = payload[key]
    if v is None:
        return ""
    if not isinstance(v, str):
        raise ValueError(f"{name}.{key}: expected str or None, got "
                         f"{type(v).__name__}")
    return v


def event_label_from_identity(
        payload: Any, *,
        vertical_id: str,
        geometry_role: str,
        coordinate_uncertainty: str = "",
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        adjudication_state: str = "UNADJUDICATED",
        adjudication_notes: str = "",
        reviewer_ids: Sequence[str] = ()) -> EventLabelV0:
    """Map a serialized science_v0.EventIdentity payload to EventLabelV0.

    ``payload`` must contain exactly the EventIdentity fields.  Fields
    the identity does not carry (vertical, geometry, adjudication) are
    explicit adapter parameters — they are never guessed.  The produced
    record is validated with ``problems()``; any problem raises.
    """
    p = _payload(payload, "EventIdentity")
    _require_exact_keys(p, _IDENTITY_KEYS, "EventIdentity")
    timing_class = _req_str(p, "timing_class", "EventIdentity")
    if timing_class not in _TIMING_CLASS_TO_PRECISION:
        raise ValueError(f"EventIdentity.timing_class {timing_class!r} "
                         f"not in {sorted(_TIMING_CLASS_TO_PRECISION)}")
    uncertainty = p["uncertainty_seconds"]
    if isinstance(uncertainty, bool) or \
            not isinstance(uncertainty, (int, float)):
        raise ValueError("EventIdentity.uncertainty_seconds: expected "
                         "a finite number")
    variants = p["timing_variants"]
    if not isinstance(variants, (list, tuple)) or len(variants) != 3 or \
            any(not isinstance(v, str) for v in variants):
        raise ValueError("EventIdentity.timing_variants: expected "
                         "(min, central, max) ISO instants")
    record = EventLabelV0(
        event_id=_req_str(p, "event_id", "EventIdentity"),
        vertical_id=vertical_id,
        source_id=_req_str(p, "source_id", "EventIdentity"),
        source_version=_req_str(p, "source_version", "EventIdentity"),
        event_time_start=_req_str(p, "interval_start", "EventIdentity"),
        event_time_end=_req_str(p, "interval_end", "EventIdentity"),
        uncertainty_seconds=float(uncertainty),
        event_time_precision=_TIMING_CLASS_TO_PRECISION[timing_class],
        event_time_basis=f"timing_class:{timing_class}",
        geometry_role=geometry_role,
        coordinate_uncertainty=coordinate_uncertainty,
        latitude=latitude,
        longitude=longitude,
        basin_id=_req_str(p, "basin", "EventIdentity"),
        cascade_group_id=_req_str(p, "cascade_group_id", "EventIdentity"),
        parent_event_id=_opt_str(p, "parent_event_id", "EventIdentity"),
        duplicate_of=_opt_str(p, "duplicate_of", "EventIdentity"),
        adjudication_state=adjudication_state,
        adjudication_notes=adjudication_notes,
        reviewer_ids=tuple(reviewer_ids),
    )
    problems = record.problems()
    if problems:
        raise ValueError("adapted EventLabelV0 has problems: "
                         + "; ".join(problems))
    return record


def holdout_plan_from_assignment(
        payload: Any, *,
        holdout_plan_id: str,
        split_of_group: Mapping[str, str],
        assignment_rule: str = "basin",
        test_locked: bool = True) -> HoldoutPlanV0:
    """Map a serialized science_v0.HoldoutAssignment to HoldoutPlanV0.

    ``payload`` must contain exactly the HoldoutAssignment fields as
    produced by ``science_v0.assign_holdouts``: ``assignments``
    (event_id -> geographic GROUP — the producer assigns each event to
    its basin's group; the split role lives on the group lists),
    ``basin_of_event`` (event_id -> basin), ``basin_groups``
    (group -> basins), ``evaluation_regions``, ``embargo_seconds``.

    ``split_of_group`` is an explicit adapter parameter — the group ->
    split map the producer used — never derived from or guessed at.
    Every declared group must have a split, every event's assigned
    group must be declared in ``basin_groups``, and the event's basin
    must be a member of that group.
    """
    p = _payload(payload, "HoldoutAssignment")
    _require_exact_keys(p, _ASSIGNMENT_KEYS, "HoldoutAssignment")
    if assignment_rule not in ("basin", "catchment", "macroregion",
                               "fixed_spatial"):
        raise ValueError(f"assignment_rule {assignment_rule!r} "
                         "not in contract vocabulary")
    assignments = p["assignments"]
    basin_of_event = p["basin_of_event"]
    basin_groups = p["basin_groups"]
    evaluation_regions = p["evaluation_regions"]
    embargo = p["embargo_seconds"]
    for k, v, nm in (("assignments", assignments, "dict[str,str]"),
                     ("basin_of_event", basin_of_event, "dict[str,str]"),
                     ("basin_groups", basin_groups, "dict[str,collection]")):
        if not isinstance(v, Mapping):
            raise ValueError(f"HoldoutAssignment.{k}: expected {nm}")
    if not isinstance(evaluation_regions, (list, tuple)):
        raise ValueError("HoldoutAssignment.evaluation_regions: "
                         "expected a sequence")
    if isinstance(embargo, bool) or \
            not isinstance(embargo, (int, float)):
        raise ValueError("HoldoutAssignment.embargo_seconds: expected "
                         "a finite number")
    if not isinstance(split_of_group, Mapping):
        raise ValueError("split_of_group: expected group -> split map")
    normalized: dict[str, str] = {}
    for g, s in split_of_group.items():
        if not isinstance(g, str) or not isinstance(s, str):
            raise ValueError("split_of_group: expected str -> str")
        s = "val" if s == "validation" else s
        if s not in _VALID_SPLITS:
            raise ValueError(f"split_of_group[{g!r}] = {s!r} "
                             f"not in {sorted(_VALID_SPLITS)}")
        normalized[g] = s
    missing = set(basin_groups) - set(normalized)
    if missing:
        raise ValueError(f"declared groups lack a split: "
                         f"{sorted(missing)}")

    # basin -> group inversion (fail on a basin claimed by two groups).
    basin_to_group: dict[str, str] = {}
    for group, basins in basin_groups.items():
        if not isinstance(group, str) or \
                not isinstance(basins, (list, tuple, set, frozenset)):
            raise ValueError("HoldoutAssignment.basin_groups: expected "
                             "group -> collection of basin names")
        for basin in basins:
            if basin in basin_to_group and basin_to_group[basin] != group:
                raise ValueError(f"basin {basin!r} appears in multiple "
                                 f"groups")
            basin_to_group[basin] = group

    # assignments are already event -> group; verify basin membership.
    event_to_group: dict[str, str] = {}
    for event_id, group in assignments.items():
        if group not in basin_groups:
            raise ValueError(f"event {event_id!r} maps to undeclared "
                             f"group {group!r}")
        basin = basin_of_event.get(event_id)
        if not isinstance(basin, str):
            raise ValueError(f"event {event_id!r} has no basin binding")
        if basin not in basin_groups[group]:
            raise ValueError(f"event {event_id!r} basin {basin!r} is "
                             f"not a member of assigned group {group!r}")
        event_to_group[event_id] = group

    # Evaluation regions arrive as basin names (science_v0) or group
    # names; HoldoutPlanV0 names locked test GROUPS — map basins
    # through basin_to_group and let problems() reject non-test groups.
    region_names: list[str] = []
    for r in evaluation_regions:
        if not isinstance(r, str):
            raise ValueError("HoldoutAssignment.evaluation_regions: "
                             "expected string names")
        if r in normalized:
            region_names.append(r)
        elif r in basin_to_group:
            region_names.append(basin_to_group[r])
        else:
            raise ValueError(f"evaluation region {r!r} matches no "
                             f"declared group or basin")

    record = HoldoutPlanV0(
        holdout_plan_id=holdout_plan_id,
        assignment_rule=assignment_rule,
        train_groups=tuple(sorted(
            g for g, s in normalized.items() if s == "train")),
        validation_groups=tuple(sorted(
            g for g, s in normalized.items() if s == "val")),
        test_groups=tuple(sorted(
            g for g, s in normalized.items() if s == "test")),
        event_assignments=dict(event_to_group),
        evaluation_region_names=tuple(dict.fromkeys(region_names)),
        assigned_before_filtering=True,
        test_locked=test_locked,
        embargo_seconds=float(embargo),
    )
    problems = record.problems()
    if problems:
        raise ValueError("adapted HoldoutPlanV0 has problems: "
                         + "; ".join(problems))
    return record


def vintage_from_request(payload: Any, *,
                         vintage_id: str) -> ForecastVintageV0:
    """Map a VintageRequest (or its serialized mapping) to
    ForecastVintageV0 via the B2 admission adapter."""
    if isinstance(payload, VintageRequest):
        req = payload
    else:
        req = VintageRequest.from_dict(_payload(payload, "VintageRequest"))
    return build_vintage(req, vintage_id)


_OPPORTUNITY_KEYS = frozenset({
    "opportunity_id", "unit_id", "source_id", "basin",
    "window_start", "window_end", "state", "platform",
    "coverage_fraction", "coverage_quality", "detection_threshold",
    "source_as_of", "frame_ids"})

_CONTROL_KEYS = frozenset({
    "control_id", "unit_id", "source_id", "basin",
    "window_start", "window_end", "opportunity_id",
    "opportunity_state", "state", "covering_opportunity_ids",
    "control_digest"})


def opportunity_from_science(payload: Any) -> ObservationOpportunityV0:
    """Map a serialized science_v0.ObservationOpportunity payload to
    ObservationOpportunityV0.

    ``payload`` must contain exactly the ObservationOpportunity
    fields.  ``basin`` is science-axis metadata and is dropped — the
    V0 record binds the opportunity to ``unit_id``; the unit -> basin
    map is carried separately by ``unit_basins``.  OBSERVED_FULL still
    requires coverage ~1.0 and real frame_ids — enforced by
    ``problems()``, never relaxed here.
    """
    p = _payload(payload, "ObservationOpportunity")
    _require_exact_keys(p, _OPPORTUNITY_KEYS, "ObservationOpportunity")
    coverage = p["coverage_fraction"]
    if coverage is not None and (isinstance(coverage, bool)
                                 or not isinstance(coverage,
                                                   (int, float))):
        raise ValueError("ObservationOpportunity.coverage_fraction: "
                         "expected a finite number or null")
    frames = p["frame_ids"]
    if not isinstance(frames, (list, tuple)) or \
            any(not isinstance(f, str) for f in frames):
        raise ValueError("ObservationOpportunity.frame_ids: expected "
                         "a sequence of strings")
    record = ObservationOpportunityV0(
        opportunity_id=_req_str(p, "opportunity_id",
                                "ObservationOpportunity"),
        unit_id=_req_str(p, "unit_id", "ObservationOpportunity"),
        platform=_req_str(p, "platform", "ObservationOpportunity"),
        window_start=_req_str(p, "window_start",
                              "ObservationOpportunity"),
        window_end=_req_str(p, "window_end", "ObservationOpportunity"),
        coverage_fraction=(None if coverage is None
                           else float(coverage)),
        coverage_quality=_opt_str(p, "coverage_quality",
                                  "ObservationOpportunity"),
        detection_threshold=_opt_str(p, "detection_threshold",
                                     "ObservationOpportunity"),
        state=_req_str(p, "state", "ObservationOpportunity"),
        source_id=_opt_str(p, "source_id", "ObservationOpportunity"),
        source_as_of=_opt_str(p, "source_as_of",
                              "ObservationOpportunity"),
        frame_ids=tuple(frames),
    )
    problems = record.problems()
    if problems:
        raise ValueError("adapted ObservationOpportunityV0 has "
                         "problems: " + "; ".join(problems))
    return record


def control_from_science(payload: Any, *,
                         matched_covariates: Sequence[str] = (),
                         cascade_group_id: str = ""
                         ) -> ControlWindowV0:
    """Map a serialized science_v0.ControlWindow payload to
    ControlWindowV0.

    ``payload`` must contain exactly the ControlWindow fields.  The
    producer already derived ``state`` from the linked opportunity
    (NEGATIVE only on OBSERVED_FULL); the adapter validates the record
    but never re-derives or overrides state.  ``basin`` and
    ``covering_opportunity_ids`` are producer lineage metadata not
    carried by the V0 record — the link that matters is
    ``opportunity_id`` + ``opportunity_state``.
    """
    p = _payload(payload, "ControlWindow")
    _require_exact_keys(p, _CONTROL_KEYS, "ControlWindow")
    record = ControlWindowV0(
        control_id=_req_str(p, "control_id", "ControlWindow"),
        unit_id=_req_str(p, "unit_id", "ControlWindow"),
        window_start=_req_str(p, "window_start", "ControlWindow"),
        window_end=_req_str(p, "window_end", "ControlWindow"),
        opportunity_id=_req_str(p, "opportunity_id", "ControlWindow"),
        opportunity_state=_req_str(p, "opportunity_state",
                                   "ControlWindow"),
        state=_req_str(p, "state", "ControlWindow"),
        matched_covariates=tuple(matched_covariates),
        cascade_group_id=cascade_group_id,
    )
    problems = record.problems()
    if problems:
        raise ValueError("adapted ControlWindowV0 has problems: "
                         + "; ".join(problems))
    return record


def regime_assignment_from_artifact(
        payload: Any, *,
        artifact_id: str,
        fitted_on: str = "TRAIN_ONLY",
        mode: str = "RETROSPECTIVE_REGIME"
        ) -> RegimeAssignmentArtifact:
    """Map a serialized science_v0 frozen regime artifact dict to
    RegimeAssignmentArtifact.

    ``payload`` must be a mapping carrying at least ``assignments``
    (``(unit_id, date, regime_id)`` triples emitted by
    ``science_v0.run_regimes``) and ``regime_artifact_digest`` (or
    ``freeze_digest`` once ``freeze_regime_artifact`` has run — the
    freeze digest is preferred because it covers the frozen surface).
    A summary-only artifact without the assignment sidecar is
    rejected; assignments are never re-derived here, because
    re-prediction would violate freeze semantics.

    ``fitted_on`` and ``mode`` are explicit provenance assertions by
    the caller — the contract requires ``TRAIN_ONLY`` fitting and
    ``RETROSPECTIVE_REGIME`` mode; the produced record's ``problems()``
    enforces them.
    """
    p = _payload(payload, "regime artifact")
    if not isinstance(p.get("assignments"), (list, tuple)) or \
            not p["assignments"]:
        raise ValueError("regime artifact carries no assignment "
                         "sidecar — summary-only artifacts cannot "
                         "bind to association")
    regime_digest = p.get("freeze_digest") or \
        p.get("regime_artifact_digest")
    if not isinstance(regime_digest, str) or not regime_digest:
        raise ValueError("regime artifact lacks "
                         "freeze_digest/regime_artifact_digest")
    seeds = p.get("seeds", ())
    if not isinstance(seeds, (list, tuple)):
        raise ValueError("regime artifact seeds: expected a sequence")
    # Serialized assignments may carry regime ids as ints (the producer
    # emits raw GMM labels); the contract requires (str, str, str)
    # triples — normalize deterministically, never re-derive.
    triples = []
    for row in p["assignments"]:
        if not isinstance(row, (list, tuple)) or len(row) != 3:
            raise ValueError("regime assignment rows must be "
                             "(unit_id, date, regime_id) triples")
        triples.append([str(row[0]), str(row[1]), str(row[2])])
    record = RegimeAssignmentArtifact.from_dict({
        "artifact_id": artifact_id,
        "regime_digest": regime_digest,
        "assignments": triples,
        "fitted_on": fitted_on,
        "label_blinding": True,
        "seeds": list(seeds),
        "mode": mode,
    })
    return record
