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
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Mapping, Optional, Sequence

from nepal.research_v0.records import (
    EventLabelV0, HoldoutPlanV0, ForecastVintageV0)

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
        test_locked: bool = True) -> HoldoutPlanV0:
    """Map a serialized science_v0.HoldoutAssignment to HoldoutPlanV0.

    ``payload`` must contain exactly the HoldoutAssignment fields:
    ``assignments`` (event_id -> "train"/"val"/"test"),
    ``basin_of_event`` (event_id -> basin), ``basin_groups``
    (group -> basins), ``evaluation_regions``, ``embargo_seconds``.
    The per-group split is *derived* from member events; a group whose
    events disagree, or a group with no events, is rejected — split
    attribution is never guessed.
    """
    p = _payload(payload, "HoldoutAssignment")
    _require_exact_keys(p, _ASSIGNMENT_KEYS, "HoldoutAssignment")
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
    bad_splits = {e for e, s in assignments.items()
                  if s not in _VALID_SPLITS}
    if bad_splits:
        raise ValueError(f"events map to invalid splits: {sorted(bad_splits)}")

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

    # event -> group via its basin; group -> split via member events.
    event_to_group: dict[str, str] = {}
    group_splits: dict[str, set] = {g: set() for g in basin_groups}
    for event_id, split in assignments.items():
        basin = basin_of_event.get(event_id)
        if not isinstance(basin, str):
            raise ValueError(f"event {event_id!r} has no basin binding")
        group = basin_to_group.get(basin)
        if group is None:
            raise ValueError(f"event {event_id!r} basin {basin!r} is in "
                             f"no declared group")
        event_to_group[event_id] = group
        group_splits[group].add(split)
    split_of_group: dict[str, str] = {}
    for group, splits in group_splits.items():
        if not splits:
            raise ValueError(f"group {group!r} has no member events — "
                             f"cannot derive its split")
        if len(splits) > 1:
            raise ValueError(f"group {group!r} events disagree on "
                             f"split: {sorted(splits)}")
        split_of_group[group] = splits.pop()

    # Evaluation regions arrive as basin names (science_v0) or group
    # names; HoldoutPlanV0 names locked test GROUPS — map basins
    # through basin_to_group and let problems() reject non-test groups.
    region_names: list[str] = []
    for r in evaluation_regions:
        if not isinstance(r, str):
            raise ValueError("HoldoutAssignment.evaluation_regions: "
                             "expected string names")
        if r in split_of_group:
            region_names.append(r)
        elif r in basin_to_group:
            region_names.append(basin_to_group[r])
        else:
            raise ValueError(f"evaluation region {r!r} matches no "
                             f"declared group or basin")

    record = HoldoutPlanV0(
        holdout_plan_id=holdout_plan_id,
        assignment_rule="basin",
        train_groups=tuple(sorted(
            g for g, s in split_of_group.items() if s == "train")),
        validation_groups=tuple(sorted(
            g for g, s in split_of_group.items() if s == "val")),
        test_groups=tuple(sorted(
            g for g, s in split_of_group.items() if s == "test")),
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
