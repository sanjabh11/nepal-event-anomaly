"""GLOF descriptive PoC orchestration (Round-11 thin-PoC contract).

Two functions composing the EXISTING event/regime machinery — no new
validators, no new schema:

- ``build_hmaglofdb_event_package`` — SourceRows -> typed
  EventLabelV0 / ObservationOpportunityV0 / ControlWindowV0 /
  HoldoutPlanV0 surfaces with canonical digests.  Every record is
  built by the existing events.py/adapters.py machinery and
  re-validated through ``deserialize_record`` + ``problems()``.
- ``run_glof_descriptive_poc`` — the thin runner: source-record
  posture gate -> holdout gate -> ``run_regimes`` -> freeze ->
  non-promotable ``GLOF_POC_RECEIPT_V0``.

Units are basin-level in this PoC (``unit_id`` is a declared basin
name); finer lake-level units are a real-fit-stage concern.

Statuses are deliberately non-promotable: the receipt can never
authorize association, forecasting, warning, or production.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Mapping, Sequence

from nepal.research_v0._hashing import sha256_canonical
from nepal.research_v0.records import (
    ObservationOpportunityV0, deserialize_record)
from nepal.experiment_v0.adapters import holdout_plan_from_assignment

from . import events as _ev
from .events import (ObservationOpportunity, SourceRow,
                     assign_holdouts, build_controls, deduplicate,
                     normalize_event, to_event_label, to_holdout_plan,
                     validate_cascade_graph, BASIN_UNIVERSE)
from .regimes import freeze_regime_artifact, run_regimes

#: The PoC event package's exact key surface (audit contract).
_EVENT_PACKAGE_KEYS = frozenset({
    "source_record", "event_labels", "opportunities", "controls",
    "holdout_plan", "source_manifest_digest", "event_digest",
    "opportunity_digest", "control_digest", "holdout_digest"})

_RECEIPT_STATUSES = frozenset({
    "RUN_ERROR", "CANDIDATE_ONLY", "UNDERPOWERED_DESCRIPTIVE_ONLY",
    "DESCRIPTIVE_REGIME_ONLY"})


def _digest(section: Any) -> str:
    return sha256_canonical(section)


def build_hmaglofdb_event_package(
        rows: Sequence[SourceRow],
        *,
        source_record,
        source_manifest: Mapping[str, Any],
        opportunity_frame: Sequence[ObservationOpportunityV0],
        group_of_basin: Mapping[str, str],
        split_of_group: Mapping[str, str],
        evaluation_regions: Sequence[str],
        embargo_seconds: float) -> dict:
    """Build the typed event package from real SourceRows.

    ``source_record`` is a ``SourceRecordV0`` (or its serialized
    dict); ``source_manifest`` is the non-fixture manifest the rows
    were loaded under — ``source_manifest_digest`` binds IT (never
    the record).  ``opportunity_frame`` are validated
    ``ObservationOpportunityV0`` records; ``unit_id`` values are
    basin-level units in this PoC.  The returned mapping carries
    exactly the contract keys; a holdout that cannot satisfy its
    gates yields ``holdout_plan = {"rejected": True, "problems":
    [...]}`` — the runner demotes, never weakens.
    """
    problems: list[str] = []
    if not isinstance(rows, (list, tuple)) or not rows:
        raise ValueError("event package requires >=1 SourceRow")
    for r in rows:
        if not isinstance(r, SourceRow):
            raise ValueError(
                f"event package requires SourceRow records; got "
                f"{type(r).__name__}")
    if not isinstance(source_manifest, Mapping):
        raise ValueError("source_manifest must be a mapping")
    for name, value in (("group_of_basin", group_of_basin),
                        ("split_of_group", split_of_group)):
        if not isinstance(value, Mapping):
            raise ValueError(f"{name} must be a mapping")
    if not isinstance(evaluation_regions, (list, tuple)):
        raise ValueError("evaluation_regions must be a sequence")
    if isinstance(embargo_seconds, bool) or \
            not isinstance(embargo_seconds, (int, float)):
        raise ValueError("embargo_seconds must be a finite number")
    # --- source record (typed either way) ---
    if isinstance(source_record, Mapping):
        source_record = deserialize_record(source_record)
    if not hasattr(source_record, "problems"):
        raise ValueError(
            "source_record must be a SourceRecordV0 or its "
            "serialized mapping")
    sr_problems = source_record.problems()
    if sr_problems:
        problems.extend(f"source_record: {p}" for p in sr_problems)
    # --- cross-binding: rows <-> record <-> manifest (R11.1-5) ---
    manifest_sid = source_manifest.get("source_id")
    if manifest_sid and source_record.source_id != manifest_sid:
        problems.append(
            f"source_record.source_id {source_record.source_id!r} "
            f"!= source_manifest.source_id {manifest_sid!r}")
    manifest_version = None
    lineage = source_manifest.get("lineage")
    if isinstance(lineage, str) and "source_version=" in lineage:
        manifest_version = lineage.split(
            "source_version=", 1)[1].split(";", 1)[0]
    manifest_units = source_manifest.get("units")
    unit_set = set(manifest_units) \
        if isinstance(manifest_units, (list, tuple)) else set()
    for r in rows:
        if r.source_id != source_record.source_id:
            problems.append(
                f"row {r.source_row_key!r}: source_id "
                f"{r.source_id!r} != source_record "
                f"{source_record.source_id!r}")
        if source_record.version and \
                r.source_version != source_record.version:
            problems.append(
                f"row {r.source_row_key!r}: source_version "
                f"{r.source_version!r} != source_record.version "
                f"{source_record.version!r}")
        if manifest_sid and r.source_id != manifest_sid:
            problems.append(
                f"row {r.source_row_key!r}: source_id "
                f"{r.source_id!r} != manifest source_id "
                f"{manifest_sid!r}")
        if manifest_version and \
                r.source_version != manifest_version:
            problems.append(
                f"row {r.source_row_key!r}: source_version "
                f"{r.source_version!r} != manifest "
                f"source_version= {manifest_version!r}")
        if unit_set and r.basin not in unit_set:
            problems.append(
                f"row {r.source_row_key!r}: basin {r.basin!r} is "
                "not declared in the manifest's units")

    # --- events ---
    identities = [normalize_event(r) for r in rows]
    identities = deduplicate(identities)
    validate_cascade_graph(identities)
    event_labels = []
    for ident in identities:
        label = to_event_label(
            ident, vertical_id="glof",
            geometry_role="lake_point",
            event_time_basis="published_report",
            adjudication_state="UNADJUDICATED")
        rec = deserialize_record(label)
        rec_problems = rec.problems()
        if rec_problems:
            problems.extend(
                f"event {ident.event_id}: {p}"
                for p in rec_problems)
        event_labels.append(label)

    # --- opportunities (typed; basin-level units) ---
    if not isinstance(opportunity_frame, (list, tuple)):
        raise ValueError("opportunity_frame must be a sequence")
    opportunities = []
    for opp in opportunity_frame:
        if isinstance(opp, Mapping):
            opp = deserialize_record(opp)
        if not hasattr(opp, "problems"):
            raise ValueError(
                "opportunity_frame members must be "
                "ObservationOpportunityV0 records or serialized "
                "mappings")
        opp_problems = opp.problems()
        if opp_problems:
            problems.extend(
                f"opportunity {opp.opportunity_id}: {p}"
                for p in opp_problems)
        if opp.unit_id not in BASIN_UNIVERSE:
            problems.append(
                f"opportunity {opp.opportunity_id}: unit_id "
                f"{opp.unit_id!r} is not a declared basin-level "
                "unit in this PoC")
        opportunities.append(opp)

    # --- controls: derived, never caller-asserted ---
    controls = []
    by_unit: dict[str, list] = {}
    for opp in opportunities:
        by_unit.setdefault(opp.unit_id, []).append(opp)
    events_by_basin: dict[str, list] = {}
    for ident in identities:
        events_by_basin.setdefault(ident.basin, []).append(ident)
    for unit, opps in sorted(by_unit.items()):
        basin = unit  # basin-level units in this PoC
        science_opps = [
            ObservationOpportunity(
                opportunity_id=o.opportunity_id,
                unit_id=o.unit_id,
                source_id=o.source_id,
                basin=basin,
                window_start=o.window_start,
                window_end=o.window_end,
                state=o.state,
                platform=o.platform,
                coverage_fraction=o.coverage_fraction,
                coverage_quality=o.coverage_quality,
                detection_threshold=o.detection_threshold,
                source_as_of=o.source_as_of,
                frame_ids=tuple(o.frame_ids))
            for o in opps]
        # candidate control windows = the declared opportunity
        # windows themselves — observability defines the frame.
        candidate_windows = [(o.window_start, o.window_end)
                             for o in opps]
        for ctl in build_controls(
                unit_id=unit,
                source_id=source_record.source_id,
                basin=basin,
                candidate_windows=candidate_windows,
                opportunities=science_opps,
                events=events_by_basin.get(basin, [])):
            ctl_problems = ctl.problems() \
                if hasattr(ctl, "problems") else []
            if ctl_problems:
                problems.extend(
                    f"control {ctl.control_id}: {p}"
                    for p in ctl_problems)
            ctl_dict = {
                "record_type": "ControlWindowV0",
                "control_id": ctl.control_id,
                "unit_id": ctl.unit_id,
                "window_start": ctl.window_start,
                "window_end": ctl.window_end,
                "opportunity_id": ctl.opportunity_id,
                "opportunity_state": ctl.opportunity_state,
                "state": ctl.state,
                "matched_covariates": tuple(
                    ctl.covering_opportunity_ids),
                "cascade_group_id": ""}
            ctl_rec = deserialize_record(ctl_dict)
            ctl_rec_problems = ctl_rec.problems()
            if ctl_rec_problems:
                problems.extend(
                    f"control {ctl.control_id}: {p}"
                    for p in ctl_rec_problems)
            controls.append(ctl_dict)

    # --- holdout (never weakened) ---
    holdout_plan: Any
    try:
        assignment = assign_holdouts(
            identities,
            dict(group_of_basin),
            dict(split_of_group),
            tuple(evaluation_regions),
            embargo_seconds)
        payload = {
            "assignments": dict(assignment.assignments),
            "basin_of_event": dict(assignment.basin_of_event),
            "basin_groups": {g: sorted(b)
                             for g, b in
                             assignment.basin_groups.items()},
            "evaluation_regions": list(
                assignment.evaluation_regions),
            "embargo_seconds": assignment.embargo_seconds}
        plan = holdout_plan_from_assignment(
            payload,
            holdout_plan_id="hmaglofdb-poc-holdout",
            split_of_group=dict(split_of_group))
        plan_problems = plan.problems()
        if plan_problems:
            holdout_plan = {"rejected": True,
                            "problems": plan_problems}
        else:
            holdout_plan = plan.to_dict()
    except ValueError as exc:
        holdout_plan = {"rejected": True, "problems": [str(exc)]}

    # Canonical ordering — the package digests must be stable under
    # input-row permutation (the inventory's byte order is not
    # semantic).  Events sort by event_id; controls/opportunities are
    # already built in deterministic unit order.
    event_labels.sort(key=lambda d: d["event_id"])
    sr_dict = source_record.to_dict() \
        if hasattr(source_record, "to_dict") else dict(source_record)
    opp_dicts = [o.to_dict() if hasattr(o, "to_dict") else dict(o)
                 for o in opportunities]
    if problems:
        raise ValueError("build_hmaglofdb_event_package: " +
                         "; ".join(problems))
    return {
        "source_record": sr_dict,
        "event_labels": event_labels,
        "opportunities": opp_dicts,
        "controls": controls,
        "holdout_plan": holdout_plan,
        "source_manifest_digest": _digest(dict(source_manifest)),
        "event_digest": _digest(event_labels),
        "opportunity_digest": _digest(opp_dicts),
        "control_digest": _digest(controls),
        "holdout_digest": _digest(holdout_plan)}


def run_glof_descriptive_poc(
        feature_frame,
        feature_cols: list[str],
        train_mask,
        regime_config,
        event_package: Mapping[str, Any]) -> dict:
    """Run the thin descriptive GLOF PoC and emit the non-promotable
    receipt.

    Gate order (most conservative wins): RUN_ERROR -> CANDIDATE_ONLY
    (source posture or artifact non-descriptive) ->
    UNDERPOWERED_DESCRIPTIVE_ONLY (valid descriptive artifact but the
    event/holdout package cannot support association) ->
    DESCRIPTIVE_REGIME_ONLY.  Association, forecasting, warning, and
    production are unreachable from this receipt.
    """
    problems: list[str] = []
    receipt: dict[str, Any] = {
        "record_type": "GLOF_POC_RECEIPT_V0",
        "status": "RUN_ERROR",
        "claim_scope": "research_only_no_operational_authorization",
        "source_manifest_digest": "",
        "event_digest": "",
        "opportunity_digest": "",
        "control_digest": "",
        "holdout_digest": "",
        "regime_artifact_digest": "",
        "report_digest": "",
        "promotion_eligible": False,
        "production_authorized": False,
        "warning_path_authorized": False,
        "problems": problems}

    if not isinstance(event_package, Mapping):
        problems.append("event_package must be a mapping")
        receipt["report_digest"] = _digest(
            {k: v for k, v in receipt.items()
             if k not in ("report_digest", "problems")})
        return receipt
    extra = set(event_package) - _EVENT_PACKAGE_KEYS
    missing = _EVENT_PACKAGE_KEYS - set(event_package)
    if extra or missing:
        if extra:
            problems.append(f"event_package carries undeclared keys "
                            f"{sorted(extra)}")
        if missing:
            problems.append(f"event_package lacks keys "
                            f"{sorted(missing)}")
        receipt["report_digest"] = _digest(
            {k: v for k, v in receipt.items()
             if k not in ("report_digest", "problems")})
        return receipt
    for k in ("source_manifest_digest", "event_digest",
              "opportunity_digest", "control_digest",
              "holdout_digest"):
        receipt[k] = event_package[k]

    # --- package integrity: carried digests are recomputed and
    # every section is re-deserialized before any fitting —
    # a stale digest or tampered section can never reach
    # run_regimes (R11.1-4) ---
    _SECTION_FIELDS = {
        "event_labels": "event_digest",
        "opportunities": "opportunity_digest",
        "controls": "control_digest",
        "holdout_plan": "holdout_digest"}
    digest_bad = False
    for section, dkey in _SECTION_FIELDS.items():
        section_val = event_package[section]
        if section == "holdout_plan" and isinstance(
                section_val, Mapping) and \
                section_val.get("rejected") is True:
            pass  # rejection mapping is a legal section
        elif section != "holdout_plan" and not isinstance(
                section_val, (list, tuple)):
            problems.append(f"{section} must be a sequence")
            digest_bad = True
            continue
        if _digest(section_val) != event_package[dkey]:
            problems.append(
                f"{dkey} does not match the recomputed "
                f"{section} digest — carried digests are never "
                "trusted")
            digest_bad = True
    for section in ("event_labels", "opportunities", "controls"):
        section_val = event_package[section]
        if not isinstance(section_val, (list, tuple)):
            continue
        for i, rec in enumerate(section_val):
            try:
                typed = deserialize_record(rec)
                rec_problems = typed.problems()
                if rec_problems:
                    problems.append(
                        f"{section}[{i}]: " +
                        "; ".join(rec_problems))
                    digest_bad = True
            except (TypeError, ValueError) as exc:
                problems.append(
                    f"{section}[{i}] does not deserialize: {exc}")
                digest_bad = True
    if digest_bad:
        receipt["status"] = "RUN_ERROR"
        receipt["report_digest"] = _digest(
            {k: v for k, v in receipt.items()
             if k not in ("report_digest", "problems")})
        return receipt

    # --- config/package manifest binding (R11.1-5) ---
    cfg_manifest = getattr(regime_config, "source_manifest", None)
    if isinstance(cfg_manifest, Mapping):
        if _digest(dict(cfg_manifest)) != \
                event_package["source_manifest_digest"]:
            problems.append(
                "event_package.source_manifest_digest does not "
                "equal the digest of "
                "regime_config.source_manifest — the package must "
                "bind the same byte-bound source the fit declares")
            receipt["status"] = "RUN_ERROR"
            receipt["report_digest"] = _digest(
                {k: v for k, v in receipt.items()
                 if k not in ("report_digest", "problems")})
            return receipt

    # --- source-record posture gate ---
    source_verified = False
    try:
        sr = deserialize_record(event_package["source_record"])
        sr_problems = sr.problems()
        if sr_problems:
            problems.extend(f"source_record: {p}"
                            for p in sr_problems)
        elif sr.posture == "REJECTED":
            problems.append("source_record posture is REJECTED")
        elif sr.posture != "EVIDENCE_VERIFIED":
            problems.append(
                f"source_record posture {sr.posture!r} is not "
                "EVIDENCE_VERIFIED — a metadata-only or unreviewed "
                "source cannot carry a descriptive result")
        else:
            source_verified = True
    except (TypeError, ValueError) as exc:
        problems.append(f"source_record does not deserialize: {exc}")

    # --- holdout gate ---
    holdout_plan = event_package["holdout_plan"]
    holdout_ok = False
    if isinstance(holdout_plan, Mapping) and \
            holdout_plan.get("rejected") is True:
        problems.append(
            "holdout plan rejected — "
            + "; ".join(str(p) for p in
                        holdout_plan.get("problems", [])))
    elif isinstance(holdout_plan, Mapping):
        try:
            hp = deserialize_record(holdout_plan)
            hp_problems = hp.problems()
            if hp_problems:
                problems.extend(f"holdout_plan: {p}"
                                for p in hp_problems)
            else:
                holdout_ok = True
        except (TypeError, ValueError) as exc:
            problems.append(f"holdout_plan does not deserialize: "
                            f"{exc}")
    else:
        problems.append("holdout_plan is not a mapping")

    # --- regime run ---
    artifact_status = None
    try:
        artifact = run_regimes(feature_frame, feature_cols,
                               train_mask, regime_config)
    except Exception as exc:
        problems.append(f"run_regimes raised "
                        f"{type(exc).__name__}: {exc}")
        artifact = {"status": "RUN_ERROR", "reason": str(exc)}
    if isinstance(artifact, Mapping) and \
            artifact.get("status") == "RUN_ERROR":
        problems.append(
            f"regime run returned RUN_ERROR: "
            f"{artifact.get('reason', 'no reason recorded')}")
        receipt["status"] = "RUN_ERROR"
        receipt["report_digest"] = _digest(
            {k: v for k, v in receipt.items()
             if k not in ("report_digest", "problems")})
        return receipt
    artifact_status = artifact.get("status") \
        if isinstance(artifact, Mapping) else None

    # --- freeze (raises, never returns RUN_ERROR) ---
    try:
        frozen = freeze_regime_artifact(dict(artifact))
        receipt["regime_artifact_digest"] = \
            frozen.get("regime_artifact_digest", "")
    except ValueError as exc:
        problems.append(f"freeze rejected the artifact: {exc}")
        receipt["status"] = "RUN_ERROR"
        receipt["report_digest"] = _digest(
            {k: v for k, v in receipt.items()
             if k not in ("report_digest", "problems")})
        return receipt

    # --- status mapping (most conservative wins) ---
    if artifact_status not in ("DESCRIPTIVE_REGIME_ONLY",
                               "CANDIDATE_ONLY",
                               "UNSUPERVISED_STRUCTURE_NOT_STABLE"):
        problems.append(
            f"artifact status {artifact_status!r} is not a "
            "PoC-admissible status")
        receipt["status"] = "RUN_ERROR"
    elif not source_verified:
        receipt["status"] = "CANDIDATE_ONLY"
    elif artifact_status != "DESCRIPTIVE_REGIME_ONLY":
        problems.append(
            f"regime artifact is {artifact_status} — not "
            "descriptive")
        receipt["status"] = "CANDIDATE_ONLY"
    elif not holdout_ok:
        receipt["status"] = "UNDERPOWERED_DESCRIPTIVE_ONLY"
    else:
        receipt["status"] = "DESCRIPTIVE_REGIME_ONLY"

    receipt["report_digest"] = _digest(
        {k: v for k, v in receipt.items()
         if k not in ("report_digest", "problems")})
    return receipt
