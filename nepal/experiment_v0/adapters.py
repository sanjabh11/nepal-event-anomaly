"""Cross-package adapters: ``science_v0`` producer types -> governed
``research_v0`` record types consumed by ``experiment_v0``.

Every adapter is explicit and evidence-bound: fields the producer
record does not carry (adjudication, platform, source frames,
vertical identity) must be supplied by the caller and are NEVER
fabricated.  A claimed OBSERVED_FULL coverage without bound
``frame_ids`` degrades to UNKNOWN rather than passing a record the
contract would reject.  Adjudication state defaults to
UNADJUDICATED — the caller makes the governance decision.
"""
from __future__ import annotations

import hashlib
from typing import Mapping, Optional, Sequence

from nepal.research_v0.policy import (TargetState,
                                      derive_control_state_typed)
from nepal.research_v0.records import (ControlWindowV0, EventLabelV0,
                                       HoldoutPlanV0,
                                       ObservationOpportunityV0)

from .association import RegimeAssignmentArtifact

_SHA_LEN = 64

# science_v0 timing_class -> research_v0 event_time_precision vocabulary
TIMING_TO_PRECISION = {
    "EXACT_TIMESTAMP": "exact_timestamp",
    "EXACT_DAY": "day",
    "INTERVAL_LE_7D": "interval",
    "INTERVAL_8_30D": "interval",
    "COARSE_OR_UNRESOLVED": "unresolved",
}

# science_v0 holdout split labels -> HoldoutPlanV0 group names
SPLIT_TO_GROUP = {"train": "train", "val": "validation",
                  "validation": "validation", "test": "test"}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def event_to_label(
    ev, *, vertical_id: str, event_time_basis: str,
    geometry_role: str,
    adjudication_state: str = "UNADJUDICATED",
    reviewer_ids: Sequence[str] = (),
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
) -> EventLabelV0:
    """Adapt a science_v0 ``EventIdentity`` to ``EventLabelV0``.

    ``adjudication_state`` defaults to UNADJUDICATED — an adapter can
    never promote an event to an adjudicated state; that is a
    governance decision passed explicitly by the caller.
    """
    precision = TIMING_TO_PRECISION.get(ev.timing_class, "unresolved")
    return EventLabelV0(
        event_id=ev.event_id,
        vertical_id=vertical_id,
        source_id=ev.source_id,
        source_version=ev.source_version,
        event_time_start=ev.interval_start,
        event_time_end=ev.interval_end,
        uncertainty_seconds=float(ev.uncertainty_seconds),
        event_time_precision=precision,
        event_time_basis=event_time_basis,
        geometry_role=geometry_role,
        latitude=latitude,
        longitude=longitude,
        basin_id=ev.basin,
        cascade_group_id=ev.cascade_group_id,
        parent_event_id=ev.parent_event_id or "",
        duplicate_of=ev.duplicate_of or "",
        adjudication_state=adjudication_state,
        reviewer_ids=tuple(reviewer_ids),
    )


def opportunity_to_v0(
    opp, *, unit_id: str, platform: str,
    coverage_fraction: Optional[float] = None,
    detection_threshold: str = "",
    source_as_of: str = "",
    frame_ids: Sequence[str] = (),
) -> ObservationOpportunityV0:
    """Adapt a science_v0 ``ObservationOpportunity``.

    Coverage honesty: OBSERVED_FULL requires frame_ids AND
    coverage_fraction ~= 1.0; OBSERVED_PARTIAL requires frames AND a
    fraction in (0,1).  A claimed coverage class that cannot satisfy
    the V0 contract degrades to UNKNOWN rather than emitting a record
    that would fail validation.
    """
    cf = coverage_fraction
    if opp.coverage_class == "OBSERVED_FULL" and frame_ids and \
            cf is not None and cf >= 0.999:
        state = "OBSERVED_FULL"
    elif frame_ids and cf is not None and 0.0 < cf < 1.0:
        state = "OBSERVED_PARTIAL"
    else:
        state = "UNKNOWN"
        cf = None
        frame_ids = ()
    opp_id = opp.opportunity_id or _sha(
        f"opp|{opp.source_id}|{opp.basin}|{opp.window_start}|"
        f"{opp.window_end}")
    return ObservationOpportunityV0(
        opportunity_id=opp_id,
        unit_id=unit_id,
        platform=platform,
        window_start=opp.window_start,
        window_end=opp.window_end,
        coverage_fraction=cf,
        detection_threshold=detection_threshold,
        state=state,
        source_id=opp.source_id,
        source_as_of=source_as_of,
        frame_ids=tuple(frame_ids),
    )


def control_to_v0(
    ctrl, opportunity: ObservationOpportunityV0,
    labels: Sequence[EventLabelV0],
) -> ControlWindowV0:
    """Adapt a science_v0 ``ControlWindow`` to ``ControlWindowV0``.

    The window must equal the linked opportunity's window
    (derive_control_state_typed enforces this); the state is derived
    from the typed records — never caller-asserted.
    """
    control = ControlWindowV0(
        control_id=ctrl.control_digest or _sha(
            f"ctrl|{ctrl.source_id}|{ctrl.basin}|"
            f"{ctrl.window_start}|{ctrl.window_end}"),
        unit_id=ctrl.unit_id or ctrl.basin,
        window_start=ctrl.window_start,
        window_end=ctrl.window_end,
        opportunity_id=ctrl.opportunity_id or opportunity.opportunity_id,
        opportunity_state=opportunity.state,
        state=TargetState.CENSORED_OR_AMBIGUOUS.value,
        cascade_group_id="",
    )
    derived = derive_control_state_typed(control, opportunity, labels)
    object.__setattr__(control, "state", derived.value)
    return control


def holdout_to_v0(
    h, *, holdout_plan_id: str,
    assignment_rule: str = "basin",
) -> HoldoutPlanV0:
    """Adapt a science_v0 ``HoldoutAssignment`` to ``HoldoutPlanV0``.

    V0 semantics differ from science_v0: ``event_assignments`` maps
    event_id -> GROUP name (not split), and ``evaluation_region_names``
    are test-group names.  Science_v0 evaluation regions are basins;
    they resolve to their containing test-group names here (each must
    land in a distinct test group to satisfy the >=2-regions rule).
    """
    if not getattr(h, "split_of_group", None):
        raise ValueError("HoldoutAssignment lacks split_of_group — "
                         "cannot reconstruct group-level splits")
    basin_to_group = {b: g for g, basins in h.basin_groups.items()
                      for b in basins}
    by_split: dict[str, list[str]] = {}
    for group, split in h.split_of_group.items():
        by_split.setdefault(SPLIT_TO_GROUP.get(split, split),
                            []).append(group)
    event_assignments = {eid: basin_to_group[h.basin_of_event[eid]]
                         for eid in h.assignments}
    eval_groups = sorted({basin_to_group[b]
                          for b in h.evaluation_regions})
    return HoldoutPlanV0(
        holdout_plan_id=holdout_plan_id,
        assignment_rule=assignment_rule,
        train_groups=tuple(sorted(by_split.get("train", ()))),
        validation_groups=tuple(sorted(by_split.get("validation", ()))),
        test_groups=tuple(sorted(by_split.get("test", ()))),
        event_assignments=event_assignments,
        evaluation_region_names=tuple(eval_groups),
        assigned_before_filtering=True,
        test_locked=True,
        embargo_seconds=float(h.embargo_seconds),
    )


def regime_to_assignment_artifact(
    artifact: Mapping, *, artifact_id: str,
) -> RegimeAssignmentArtifact:
    """Adapt a frozen science_v0 regime artifact to the
    ``RegimeAssignmentArtifact`` consumed by the association harness.

    Requires per-unit-day ``assignments`` — a summary-statistics-only
    artifact cannot be adapted (the association contract is
    unit-day-bound)."""
    assignments = artifact.get("assignments") or []
    if not assignments:
        raise ValueError(
            "regime artifact carries no per-unit-day assignments — "
            "rerun run_regimes with unit_col/date_col configured")
    digest = artifact.get("regime_artifact_digest") or \
        artifact.get("freeze_digest") or ""
    return RegimeAssignmentArtifact(
        artifact_id=artifact_id,
        regime_digest=digest,
        assignments=tuple(tuple(r) for r in assignments),
        fitted_on=artifact.get("fitted_on", "TRAIN_ONLY"),
        label_blinding=bool(artifact.get("label_blinding", True)),
        seeds=tuple(int(s) for s in artifact.get("seeds", ())),
        mode=artifact.get("mode", "RETROSPECTIVE_REGIME"),
    )


def unit_basins(units: Sequence[str],
                basin_of_unit: Optional[Mapping[str, str]] = None
                ) -> dict[str, str]:
    """unit_id -> basin map.  In the science_v0 axis the unit IS the
    basin; callers may supply an explicit mapping when units are
    finer-grained."""
    if basin_of_unit is not None:
        return {str(u): str(basin_of_unit[u]) for u in units}
    return {str(u): str(u) for u in units}


def region_basins(h) -> dict[str, frozenset]:
    """evaluation_region -> basins map for run_association.

    Region names are the test-group names produced by
    ``holdout_to_v0``; each maps to that group's basins.
    """
    test_groups = {g for g, s in (h.split_of_group or {}).items()
                   if SPLIT_TO_GROUP.get(s) == "test"}
    eval_groups = sorted({g for g in test_groups
                          if set(h.basin_groups.get(g, ()))
                          & set(h.evaluation_regions)})
    return {g: frozenset(h.basin_groups[g]) for g in eval_groups}
