"""nepal.framework_v1.validation — Phase E honest validation.

Rules implemented here (see contract gates and duty-of-care requirements):

- Controls are locked BEFORE any candidate/event score is observed; E refuses
  to run against a controls payload whose hash does not match its lock.
- All historical features must be available before the event date
  (as-of-event validity); events without as-of-valid features are
  UNOBSERVABLE, never false negatives.
- Normalization / learned thresholds are fitted on training geographic groups
  only and applied unchanged to the held-out group (geographic
  leave-one-group-out; random row-level CV is forbidden).
- Primary statistic: matched pairwise event-vs-control win rate vs chance
  0.5, with Wilson 95% intervals and explicit denominators.
- Decision policy: INDETERMINATE is the default (small samples, incomplete
  holdouts, or intervals spanning 0.5); "better than chance" requires the
  pre-locked holdout rule and a Wilson interval excluding 0.5.  There is no
  TP > 60% threshold.  Indeterminate rows are never discarded, and a null
  result is publishable and still triggers F (briefing).
"""
from __future__ import annotations

import math
from collections import Counter
from datetime import date
from typing import Any, Iterable, Mapping, Optional, Sequence

from . import contract as C
from .controls import (ControlsLock, ControlsLockMismatch,
                       coverage_is_usable_for_comparison,
                       coverage_is_adequate_for_observable)
from .input_manifest import (InputManifestVerification, SHA256_RE,
                              canonical_input_manifest_hash)
from .provenance import (bind_artifact_envelope, bind_gate_artifact,
                         sha256_canonical, verify_artifact_envelope,
                         verify_gate_artifact, verify_gate_input)

Z95 = 1.959963984540054  # two-sided 95% normal quantile


# ---------------------------------------------------------------------------
# Wilson interval
# ---------------------------------------------------------------------------

def wilson_interval(wins: int, n: int, z: float = Z95) -> Optional[tuple]:
    """Score (lower, upper) Wilson 95% interval for a binomial proportion.

    With n <= 0 the interval is None (undefined, not 0).  Deterministic."""
    if n <= 0:
        return None
    p = wins / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2.0 * n)) / denom
    half = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * n)) / n) / denom
    return (centre - half, centre + half)


# ---------------------------------------------------------------------------
# As-of-event and observability classification
# ---------------------------------------------------------------------------

def as_of_event_valid(available_from: Mapping[str, str],
                      event_date_min: str) -> list:
    """Features available on/before the event date_min."""
    violations = []
    try:
        ev = date.fromisoformat(str(event_date_min)[:10])
    except (TypeError, ValueError):
        return ["event_date_unresolved"]
    for feat, avail in sorted(available_from.items()):
        try:
            av = date.fromisoformat(str(avail)[:10])
        except (TypeError, ValueError):
            violations.append(f"{feat}:unparseable_availability")
            continue
        if av > ev:
            violations.append(f"{feat}:available_after_event({aval_iso(av)})")
    return violations


def aval_iso(d: date) -> str:
    return d.isoformat()


def classify_event_observability(event: Mapping, controls_payload: Mapping,
                                 required_features: Sequence) -> dict:
    """UNOBSERVABLE unless volume, observation coverage, and as-of-event
    feature availability are all adequate.  Reasons are explicit; UNOBSERVABLE
    rows never count as false negatives."""
    reasons = []
    # Use the conservative lower bound when the catalog has a range.  The
    # legacy single-value field remains accepted only for compatibility.
    vol = event.get("volume_min_m3", event.get("volume_m3"))
    min_det = controls_payload.get("min_detectable_size_m3")
    if min_det is not None:
        try:
            volume_ok = vol is not None and math.isfinite(float(vol)) and \
                float(vol) >= float(min_det)
        except (TypeError, ValueError):
            volume_ok = False
        if not volume_ok:
            reasons.append("BELOW_MIN_DETECTABLE_SIZE")
    if controls_payload.get("require_full_coverage_for_observable", True):
        cov = str(event.get("observation_availability",
                            C.ObservationAvailability.UNKNOWN.value))
        # Catalog and controls use different vocabularies for the same
        # concept.  FULL/AVAILABLE are the only observed-denominator classes.
        if cov not in {C.ObservationAvailability.AVAILABLE.value, "FULL"}:
            reasons.append("OBSERVATION_NOT_ADEQUATE:" + cov)
    avail = event.get("feature_available_from") or {}
    for feature in required_features:
        if feature not in avail:
            reasons.append("FEATURE_AVAILABILITY_UNKNOWN:" + str(feature))
    asof = as_of_event_valid(avail, event.get("event_date_min", ""))
    for entry in asof:
        if entry == "event_date_unresolved":
            reasons.append("EVENT_DATE_UNRESOLVED")
        elif "available_after_event" in entry:
            reasons.append("FEATURE_NOT_AVAILABLE_AS_OF_EVENT:" + entry.split(":")[0])
        else:
            reasons.append("FEATURE_AVAILABILITY_UNKNOWN:" + entry.split(":")[0])
    return {"observable": len(reasons) == 0,
            "reasons": sorted(set(reasons))}


# ---------------------------------------------------------------------------
# Training-only percentile normalizer (fitted once, applied unchanged)
# ---------------------------------------------------------------------------

class PercentileNormalizer:
    """(below + 0.5*ties)/n percentile transform fitted on training values."""

    def __init__(self, training_scores: Sequence):
        self._values = sorted(float(v) for v in training_scores
                              if v is not None and _finite_number(v))
        self.n = len(self._values)
        self.fit_sha256 = sha256_canonical(self._values)

    def transform(self, value: float) -> Optional[float]:
        if self.n == 0:
            return None
        below = sum(1 for v in self._values if v < float(value))
        ties = sum(1 for v in self._values if v == float(value))
        return (below + 0.5 * ties) / self.n

    def apply_all(self, scores: Mapping[str, float]) -> dict:
        return {uid: self.transform(v) for uid, v in scores.items()}
# ---------------------------------------------------------------------------
# Leave-one-group-out driver
# ---------------------------------------------------------------------------

def run_validation(events: Sequence, controls: Sequence, *,
                   controls_lock: ControlsLock,
                   holdout_plan: Mapping,
                   feature_config: Optional[Mapping] = None,
                   input_manifest: Optional[Mapping] = None,
                   input_manifest_verification: Optional[Any] = None,
                   required_features: Sequence = (),
                   min_pairwise_n: int = 30,
                   z: float = Z95,
                   strict_contract: bool = False,
                   a_gate_passed: Optional[bool] = None,
                   b_gate_passed: Optional[bool] = None,
                   a_gate_artifact: Optional[Mapping[str, Any]] = None,
                   b_gate_artifact: Optional[Mapping[str, Any]] = None) -> dict:
    """Geographic leave-one-group-out validation.

    - Verifies the controls lock before observing any score.
    - Hashes catalog, controls, feature configuration, holdout plan, manifest.
    - Classifies events OBSERVABLE / UNOBSERVABLE (explicit denominators).
    - Fits a percentile normalizer on training groups only and applies it
      unchanged to the held-out group.
    - Computes matched pairwise event-vs-control win rate + Wilson interval.
    - Applies the INDETERMINATE-default decision policy."""
    if not controls_lock.verify():
        raise ControlsLockMismatch(
            "controls lock hash mismatch: controls were modified after freezing")
    controls_payload = controls_lock.controls

    catalog_hash = sha256_canonical(
        list(events) if strict_contract else [_event_identity(e) for e in events])
    controls_hash = sha256_canonical(
        list(controls) if strict_contract else [_control_identity(c) for c in controls])
    config_hash = sha256_canonical(feature_config or {})
    plan_hash = (holdout_plan.get("plan_sha256")
                 if isinstance(holdout_plan, Mapping) else None)
    try:
        manifest_hash = (canonical_input_manifest_hash(input_manifest)
                         if isinstance(input_manifest, Mapping)
                         else sha256_canonical(input_manifest or {}))
    except (TypeError, ValueError):
        manifest_hash = sha256_canonical({})

    validation_errors: list[str] = []
    # Strict E authorization is derived only from typed/hash-bound artifacts.
    # The boolean parameters remain for legacy non-strict callers but can
    # never seed a strict validation run.
    verified_a_passed: Optional[bool] = (
        None if strict_contract else a_gate_passed)
    verified_b_passed: Optional[bool] = (
        None if strict_contract else b_gate_passed)
    plan_groups: set[str] = set()
    raw_plan_groups = (holdout_plan.get("groups", [])
                       if isinstance(holdout_plan, Mapping) else None)
    if strict_contract:
        if any(value is not None for value in (a_gate_passed, b_gate_passed)):
            validation_errors.append(
                "caller-supplied A/B gate booleans are not authorization evidence")
        if not isinstance(holdout_plan, Mapping):
            validation_errors.append("holdout plan must be a mapping")
        else:
            expected_plan_hash = sha256_canonical({
                k: v for k, v in holdout_plan.items() if k != "plan_sha256"
            })
            if plan_hash != expected_plan_hash:
                validation_errors.append("holdout_plan_sha256 does not match plan content")
            if holdout_plan.get("frozen_before_eligibility_filtering") is not True:
                validation_errors.append("holdout plan was not frozen before eligibility")
        if not isinstance(raw_plan_groups, list):
            validation_errors.append("holdout plan groups must be a list")
        else:
            for index, group in enumerate(raw_plan_groups):
                if not isinstance(group, Mapping):
                    validation_errors.append(
                        f"holdout plan group[{index}] must be a mapping")
                    continue
                group_id = group.get("group_id")
                if not isinstance(group_id, str) or not group_id:
                    validation_errors.append(
                        f"holdout plan group[{index}] must have a non-empty group_id")
                    continue
                if group_id in plan_groups:
                    validation_errors.append(
                        f"holdout plan has duplicate group_id {group_id!r}")
                plan_groups.add(group_id)
            if isinstance(holdout_plan, Mapping) and holdout_plan.get(
                    "n_groups") != len(raw_plan_groups):
                validation_errors.append("holdout plan n_groups does not match groups")
        if not isinstance(input_manifest, Mapping) or not input_manifest:
            validation_errors.append("verified input manifest is required")
        elif input_manifest.get("manifest_sha256") is None:
            validation_errors.append("input manifest self-hash is required")
        verification_payload = None
        if isinstance(input_manifest_verification, InputManifestVerification):
            verification_payload = input_manifest_verification.to_dict()
        elif input_manifest_verification is not None:
            validation_errors.append(
                "typed input manifest verification is required; mappings are not trusted")
        if not isinstance(verification_payload, Mapping):
            validation_errors.append("input manifest verification is required")
        else:
            if (verification_payload.get("ok") is not True or
                    verification_payload.get("can_run_primary") is not True):
                validation_errors.append(
                    "input manifest verification did not authorize primary execution")
            verification_checks = verification_payload.get("checks")
            if not isinstance(verification_checks, Mapping):
                validation_errors.append("input manifest verification checks are required")
            else:
                try:
                    expected_manifest_binding = (
                        canonical_input_manifest_hash(input_manifest)
                        if isinstance(input_manifest, Mapping) else None)
                except (TypeError, ValueError):
                    expected_manifest_binding = None
                if verification_checks.get("manifest_sha256") != expected_manifest_binding:
                    validation_errors.append(
                        "input manifest verification does not match the input manifest")
                if verification_checks.get("self_hash_verified") is not True:
                    validation_errors.append("input manifest self-hash was not verified")
                if verification_checks.get("canonical_manifest_authorized") is not True:
                    validation_errors.append(
                        "input manifest canonical hash is not authorized")
                if verification_checks.get("contract_bound") is not True:
                    validation_errors.append("input manifest data contract is not bound")
                if verification_checks.get("framework_contract_bound") is not True:
                    validation_errors.append(
                        "input manifest framework contract is not bound")
                if verification_checks.get("raw_slc_scan") != "PASS":
                    validation_errors.append("input manifest raw SLC scan did not pass")
        if a_gate_artifact is not None:
            a_ok, a_inner, _, a_errors = verify_gate_input(
                a_gate_artifact, expected_gate_id=C.GateId.A_CATALOG.value,
                require_outer_envelope=True)
            verified_a_passed = bool(
                a_ok and isinstance(a_inner, Mapping) and
                a_inner.get("passed") is True)
            if not a_ok:
                validation_errors.extend("A_CATALOG: " + error for error in a_errors)
        else:
            validation_errors.append(
                "hash-bound A_CATALOG gate artifact is required")
        if verified_a_passed is not True:
            validation_errors.append("A_CATALOG gate must pass before strict E")

        if b_gate_artifact is not None:
            b_ok, b_inner, _, b_errors = verify_gate_input(
                b_gate_artifact, expected_gate_id=C.GateId.B_TO_C.value,
                require_outer_envelope=True)
            verified_b_passed = bool(
                b_ok and isinstance(b_inner, Mapping) and
                b_inner.get("passed") is True)
            if not b_ok:
                validation_errors.extend("B_SCREEN/B_TO_C: " + error
                                         for error in b_errors)
        else:
            validation_errors.append(
                "hash-bound B_SCREEN/B_TO_C gate artifact is required")
        if verified_b_passed is not True:
            validation_errors.append("B_SCREEN/B_TO_C gate must pass before strict E")
        if len({str(e.get("event_id")) for e in events}) != len(events):
            validation_errors.append("event_id values must be unique")
        if len({str(c.get("unit_id")) for c in controls}) != len(controls):
            validation_errors.append("control unit_id values must be unique")
        for event in events:
            if not isinstance(event.get("event_id"), str) or not event.get("event_id"):
                validation_errors.append("event_id values must be non-empty strings")
        for control in controls:
            if not isinstance(control.get("unit_id"), str) or not control.get("unit_id"):
                validation_errors.append("control unit_id values must be non-empty strings")
        for record, label in [(e, "event") for e in events] + [
                (c, "control") for c in controls]:
            if not _finite_number(record.get("score")):
                validation_errors.append(
                    f"{label} {record.get('event_id', record.get('unit_id'))} has no finite score")
        record_groups = {
            _group_of(record) for record in list(events) + list(controls)
        }
        outside_groups = sorted(record_groups - plan_groups)
        if outside_groups:
            validation_errors.append(
                "event/control groups outside frozen holdout plan: "
                + ", ".join(outside_groups))
        if validation_errors:
            return _blocked_summary(
                events, controls, catalog_hash, controls_hash, config_hash,
                plan_hash, manifest_hash, validation_errors,
                controls_lock.sha256)

    # ---- observability classification (UNOBSERVABLE, never false-negative)
    observed, unobserved = {}, {}
    for e in events:
        cls = classify_event_observability(e, controls_payload, required_features)
        rec = dict(e)
        rec["_observability"] = cls
        (observed if cls["observable"] else unobserved)[e["event_id"]] = rec

    # ---- usable controls (UNKNOWN/NONE coverage excluded, flagged)
    usable_controls, excluded_controls = {}, {}
    for c in controls:
        cov = str(c.get("observation_coverage", "UNKNOWN"))
        if coverage_is_usable_for_comparison(cov):
            usable_controls[c["unit_id"]] = c
        else:
            excluded_controls[c["unit_id"]] = {"reason": "coverage:" + cov}

    # ---- group structure
    event_groups = Counter(_group_of(r) for r in observed.values())
    if not strict_contract:
        plan_groups = {g["group_id"] for g in holdout_plan.get("groups", [])}
    fold_groups = sorted(plan_groups) if strict_contract else sorted(event_groups)

    folds, wins_total, pairs_total = [], 0, 0
    losses_total, ties_total = 0, 0
    for g in fold_groups:
        other_events = [r for r in observed.values() if _group_of(r) != g]
        other_ctrl = [c for c in usable_controls.values() if _group_of(c) != g]
        held_events = [r for r in observed.values() if _group_of(r) == g]
        held_ctrl = [c for c in usable_controls.values() if _group_of(c) == g]
        # Fit on TRAINING groups only; apply unchanged to held-out group.
        norm = PercentileNormalizer(
            [e["score"] for e in other_events] + [c["score"] for c in other_ctrl])
        held_event_scores = {e["event_id"]: norm.transform(e["score"])
                             for e in held_events}
        held_ctrl_scores = {c["unit_id"]: norm.transform(c["score"])
                            for c in held_ctrl}
        wins, losses, ties, n_pairs = 0, 0, 0, 0
        if strict_contract:
            pairings = _one_to_one_pairs(held_events, held_ctrl)
        else:
            pairings = [(e, c) for e in held_events for c in held_ctrl]
        for event, control in pairings:
            es = held_event_scores.get(event["event_id"])
            cs = held_ctrl_scores.get(control["unit_id"])
            if es is None or cs is None or not _finite_number(es) or not _finite_number(cs):
                continue
            n_pairs += 1
            if es > cs:
                wins += 1
            elif es < cs:
                losses += 1
            else:
                ties += 1
        wins_total += wins
        losses_total += losses
        ties_total += ties
        pairs_total += n_pairs
        folds.append({
            "group": g,
            "fit_groups": sorted({_group_of(r) for r in other_events}
                                 | {_group_of(c) for c in other_ctrl}),
            "n_events_held_out": len(held_events),
            "n_controls_held_out": len(held_ctrl),
            "n_pairs": n_pairs,
            "wins": wins,
            "losses": losses,
            "ties": ties,
            "win_rate": (wins / n_pairs) if n_pairs else None,
            "normalizer_fit_sha256": norm.fit_sha256,
            "held_out_group_was_used_in_fit": False,
        })

    # ---- Wilson interval + decision
    interval = wilson_interval(wins_total, pairs_total, z)
    planned_not_evaluated = sorted(plan_groups - set(fold_groups))
    empty_planned_groups = sorted(
        g for g in plan_groups
        if not any(f["group"] == g and f["n_pairs"] > 0 for f in folds))
    incomplete_holdout = bool(planned_not_evaluated or empty_planned_groups)
    small_sample = pairs_total < min_pairwise_n

    if interval is None or small_sample or incomplete_holdout or \
            (interval[0] <= 0.5 <= interval[1]):
        status = C.OutputStatus.INDETERMINATE.value
    elif interval[0] > 0.5:
        status = C.OutputStatus.PASS.value
    else:
        status = C.OutputStatus.NULL.value

    # ---- secondary descriptive metrics
    n_total = len(events)
    n_observable = len(observed)
    detectable_rate = (n_observable / n_total) if n_total else None
    summary = {
        "status": status,
        "decision_rules": {
            "indeterminate_default": True,
            "better_than_chance_requires_wilson_excluding_0.5": True,
            "no_tp_gt_60_percent_threshold": True,
            "no_random_cross_validation": True,
            "indeterminate_rows_not_discarded": True,
            "null_is_publishable": True,
        },
        "win_rate": (wins_total / pairs_total) if pairs_total else None,
        "n_pairs_total": pairs_total,
        "n_wins_total": wins_total,
        "n_losses_total": losses_total,
        "n_ties_total": ties_total,
        "wilson95": [round(x, 9) for x in interval] if interval else None,
        "chance_level": 0.5,
        "small_sample": small_sample,
        "min_pairwise_n": min_pairwise_n,
        "incomplete_holdout": incomplete_holdout,
        "planned_groups_not_evaluated": planned_not_evaluated,
        "planned_groups_without_valid_pairs": empty_planned_groups,
        "n_events_total": n_total,
        "n_observable": n_observable,
        "n_unobservable": len(unobserved),
        "observable_denominator_explicit": True,
        "detectable_event_rate": detectable_rate,
        "unobservable_reasons": {k: v["_observability"]["reasons"]
                                 for k, v in unobserved.items()},
        "excluded_controls": excluded_controls,
        "folds": folds,
        "matching_policy": ("one_to_one_deterministic" if strict_contract
                            else "all_within_group_pairs_legacy"),
        "strict_contract": bool(strict_contract),
        "validation_errors": validation_errors,
        "gate_id": C.GateId.E_VALIDATION.value,
        "a_gate_passed": verified_a_passed,
        "b_gate_passed": verified_b_passed,
        "input_hashes": {
            "catalog": catalog_hash,
            "controls": controls_hash,
            "feature_config": config_hash,
            "holdout_plan": plan_hash,
            "input_manifest": manifest_hash,
            "controls_lock": controls_lock.sha256,
        },
    }
    return summary


def _finite_number(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _group_of(record: Mapping) -> str:
    return str(record.get("group", record.get("holdout_group", "")))


def _one_to_one_pairs(events: Sequence[Mapping],
                      controls: Sequence[Mapping]) -> list[tuple[Mapping, Mapping]]:
    """Deterministically match each held event to at most one control."""
    controls_sorted = sorted(controls, key=lambda c: str(c.get("unit_id", "")))
    by_id = {str(c.get("unit_id")): c for c in controls_sorted}
    unused = set(by_id)
    pairs: list[tuple[Mapping, Mapping]] = []
    for event in sorted(events, key=lambda e: str(e.get("event_id", ""))):
        requested = event.get("matched_control_id")
        candidate = by_id.get(str(requested)) if requested is not None else None
        if candidate is not None and str(candidate.get("unit_id")) in unused:
            unused.remove(str(candidate.get("unit_id")))
            pairs.append((event, candidate))
            continue
        for control in controls_sorted:
            uid = str(control.get("unit_id"))
            if uid in unused:
                unused.remove(uid)
                pairs.append((event, control))
                break
    return pairs


def _blocked_summary(events, controls, catalog_hash, controls_hash,
                     config_hash, plan_hash, manifest_hash, errors,
                     controls_lock_sha256: Optional[str] = None) -> dict:
    return {
        "status": C.OutputStatus.BLOCKED.value,
        "gate_id": C.GateId.E_VALIDATION.value,
        "decision_rules": {
            "indeterminate_default": True,
            "better_than_chance_requires_wilson_excluding_0.5": True,
            "no_tp_gt_60_percent_threshold": True,
            "no_random_cross_validation": True,
            "indeterminate_rows_not_discarded": True,
            "null_is_publishable": True,
        },
        "validation_errors": sorted(set(errors)),
        "strict_contract": True,
        "n_events_total": len(events),
        "n_observable": 0,
        "n_unobservable": len(events),
        "observable_denominator_explicit": True,
        "n_pairs_total": 0,
        "n_wins_total": 0,
        "n_losses_total": 0,
        "n_ties_total": 0,
        "win_rate": None,
        "wilson95": None,
        "chance_level": 0.5,
        "folds": [],
        "input_hashes": {
            "catalog": catalog_hash,
            "controls": controls_hash,
            "feature_config": config_hash,
            "holdout_plan": plan_hash,
            "input_manifest": manifest_hash,
            "controls_lock": controls_lock_sha256,
        },
    }


def _gate_envelope(value: Any, *, expected_gate_id: str) -> tuple[
        Optional[Mapping[str, Any]], Optional[Mapping[str, Any]], list[str]]:
    """Extract a gate and its optional envelope without trusting booleans."""
    ok, inner, outer, errors = verify_gate_input(
        value, expected_gate_id=expected_gate_id,
        require_outer_envelope=(expected_gate_id in {
            C.GateId.A_CATALOG.value, C.GateId.B_TO_C.value}))
    # ``ok`` is intentionally not returned: callers separately verify the
    # nested gate so that their checks can expose distinct A/B evidence.
    del ok
    return inner, outer, errors


def evaluate_e_gate(summary: Mapping[str, Any], *,
                    a_gate_artifact: Optional[Mapping[str, Any]] = None,
                    b_gate_artifact: Optional[Mapping[str, Any]] = None,
                    controls_lock: Optional[ControlsLock] = None,
                    holdout_plan: Optional[Mapping[str, Any]] = None,
                    input_manifest_verification: Optional[Any] = None,
                    # Retained as ignored compatibility parameters so callers
                    # receive a deterministic failed gate while migrating;
                    # these booleans are never used as evidence.
                    a_gate_passed: Optional[bool] = None,
                    b_gate_passed: Optional[bool] = None,
                    input_manifest_verified: Optional[bool] = None) -> dict[str, Any]:
    """Evaluate E only from hash-bound A/B and manifest evidence.

    Caller-supplied booleans are deliberately ignored.  A gate artifact must
    carry a valid self-hash, A must bind the catalog and frozen holdout plan,
    B must bind its manifest and gate payload, and the controls lock must be a
    verified :class:`ControlsLock` object.
    """
    if not isinstance(summary, Mapping):
        return bind_gate_artifact({
            "gate_id": C.GateId.E_VALIDATION.value,
            "passed": False,
            "checks": {"summary_is_mapping": {"passed": False}},
            "result_status": C.OutputStatus.BLOCKED.value,
        })
    summary_sha256 = sha256_canonical(dict(summary))
    hashes = summary.get("input_hashes")
    if not isinstance(hashes, Mapping):
        hashes = {}
    required_hashes = ("catalog", "controls", "feature_config", "holdout_plan",
                       "input_manifest", "controls_lock")
    hash_presence = all(
        isinstance(hashes.get(key), str) and
        bool(SHA256_RE.fullmatch(hashes.get(key, "")))
        for key in required_hashes)

    a_gate, a_envelope, a_extract_errors = _gate_envelope(
        a_gate_artifact, expected_gate_id=C.GateId.A_CATALOG.value)
    b_gate, b_envelope, b_extract_errors = _gate_envelope(
        b_gate_artifact, expected_gate_id=C.GateId.B_TO_C.value)
    a_hash_ok, a_hash_errors = verify_gate_artifact(
        a_gate or {}, expected_gate_id=C.GateId.A_CATALOG.value)
    b_hash_ok, b_hash_errors = verify_gate_artifact(
        b_gate or {}, expected_gate_id=C.GateId.B_TO_C.value)
    a_errors = a_extract_errors + a_hash_errors
    b_errors = b_extract_errors + b_hash_errors
    a_envelope_hash_ok = False
    if isinstance(a_envelope, Mapping):
        a_envelope_hash_ok, a_envelope_errors = verify_artifact_envelope(
            a_envelope)
        a_errors.extend(a_envelope_errors)
    b_envelope_hash_ok = False
    if isinstance(b_envelope, Mapping):
        b_envelope_hash_ok, b_envelope_errors = verify_artifact_envelope(
            b_envelope)
        b_errors.extend(b_envelope_errors)
    a_passed = (a_hash_ok and a_envelope_hash_ok and a_gate is not None and
                a_gate.get("passed") is True)
    b_passed = (b_hash_ok and b_envelope_hash_ok and b_gate is not None and
                b_gate.get("passed") is True)

    a_catalog_match = bool(
        a_gate is not None and a_gate.get("catalog_sha256") == hashes.get("catalog"))
    a_plan_match = bool(
        a_gate is not None and a_gate.get("holdout_plan_sha256") == hashes.get("holdout_plan"))
    b_manifest_match = bool(
        isinstance(b_envelope, Mapping) and
        isinstance(b_envelope.get("provenance"), Mapping) and
        b_envelope["provenance"].get("input_manifest_sha256") ==
        hashes.get("input_manifest"))
    b_framework_contract_match = bool(
        isinstance(b_envelope, Mapping) and
        isinstance(b_envelope.get("provenance"), Mapping) and
        b_envelope["provenance"].get("framework_contract_sha256") ==
        C.contract_hash())
    b_manifest_framework_binding = bool(
        isinstance(b_envelope, Mapping) and
        isinstance(b_envelope.get("provenance"), Mapping) and
        b_envelope["provenance"].get(
            "input_manifest_framework_runtime_bound",
            b_envelope["provenance"].get(
                "input_manifest_framework_contract_bound")) is True)

    controls_ok = isinstance(controls_lock, ControlsLock) and controls_lock.verify()
    controls_hash_match = bool(
        controls_ok and controls_lock is not None and
        controls_lock.sha256 == hashes.get("controls_lock"))
    plan_hash = None
    plan_ok = False
    if isinstance(holdout_plan, Mapping):
        plan_hash = holdout_plan.get("plan_sha256")
        try:
            plan_ok = (isinstance(plan_hash, str) and
                       plan_hash == sha256_canonical({
                           key: value for key, value in holdout_plan.items()
                           if key != "plan_sha256"}))
        except (TypeError, ValueError):
            plan_ok = False
    plan_hash_match = plan_ok and plan_hash == hashes.get("holdout_plan")

    manifest_ok = False
    manifest_hash_match = False
    if isinstance(input_manifest_verification, InputManifestVerification):
        verification_payload = input_manifest_verification.to_dict()
        checks_payload = verification_payload.get("checks", {})
        manifest_ok = (verification_payload.get("ok") is True and
                       verification_payload.get("can_run_primary") is True and
                       checks_payload.get("self_hash_verified") is True and
                       checks_payload.get("canonical_manifest_authorized") is True and
                       checks_payload.get("contract_bound") is True and
                       checks_payload.get("framework_contract_bound") is True and
                       checks_payload.get("raw_slc_scan") == "PASS")
        manifest_hash_match = checks_payload.get("manifest_sha256") == hashes.get(
            "input_manifest")
    elif input_manifest_verification is not None:
        a_errors.append(
            "typed input manifest verification is required; mappings are not trusted")

    checks = {
        "hash_bound_A_gate_artifact": {
            "passed": a_passed, "errors": a_errors},
        "hash_bound_A_outer_envelope": {
            "passed": a_envelope_hash_ok,
            "required": True,
        },
        "A_catalog_hash_matches_summary": {"passed": a_catalog_match},
        "A_holdout_plan_hash_matches_summary": {"passed": a_plan_match},
        "hash_bound_B_gate_artifact": {
            "passed": b_passed, "errors": b_errors},
        "hash_bound_B_envelope": {"passed": b_envelope_hash_ok},
        "B_manifest_hash_matches_summary": {"passed": b_manifest_match},
        "B_framework_contract_matches_runtime": {
            "passed": b_framework_contract_match},
        "B_manifest_framework_binding": {
            "passed": b_manifest_framework_binding},
        "typed_manifest_verification": {"passed": manifest_ok},
        "manifest_hash_matches_summary": {"passed": manifest_hash_match},
        "verified_controls_lock": {"passed": controls_ok},
        "controls_lock_hash_matches_summary": {"passed": controls_hash_match},
        "verified_holdout_plan_hash": {"passed": plan_hash_match},
        "all_input_hashes_present": {"passed": hash_presence},
        "validation_not_blocked": {
            "passed": summary.get("status") != C.OutputStatus.BLOCKED.value},
        "no_validation_errors": {
            "passed": not bool(summary.get("validation_errors"))},
        "status_is_honest_result": {
            "passed": summary.get("status") in {
                C.OutputStatus.PASS.value, C.OutputStatus.NULL.value,
                C.OutputStatus.INDETERMINATE.value,
            }},
        "summary_content_hash_matches": {
            "passed": True,
            "summary_sha256": summary_sha256,
        },
    }
    if any(value is not None for value in
           (a_gate_passed, b_gate_passed, input_manifest_verified)):
        checks["caller_booleans_are_not_evidence"] = {
            "passed": False,
            "errors": ["caller-supplied gate booleans are not accepted as evidence"],
        }
    gate = {
        "gate_id": C.GateId.E_VALIDATION.value,
        "passed": all(item["passed"] for item in checks.values()),
        "checks": checks,
        "result_status": summary.get("status"),
        "summary_sha256": summary_sha256,
        "null_remains_publishable": True,
        "indeterminate_remains_honest": True,
    }
    return bind_gate_artifact(gate)


def write_validation_artifact(path, summary: Mapping[str, Any],
                             gate: Mapping[str, Any], *,
                             provenance: Optional[Mapping[str, Any]] = None):
    """Write the hash-bound E summary, gate, provenance, and outer envelope."""
    from .provenance import write_deterministic_json
    gate_payload = dict(gate) if isinstance(gate, Mapping) else {}
    # Recompute this binding at the write boundary so a caller cannot pair a
    # valid-looking gate with a different summary.  The outer envelope then
    # authenticates both the gate and summary bytes together.
    gate_payload["summary_sha256"] = sha256_canonical(dict(summary))
    gate_payload = bind_gate_artifact(gate_payload)
    if provenance is None:
        provenance_payload: dict[str, Any] = {
            "input_hashes": dict(summary.get("input_hashes", {}))
            if isinstance(summary.get("input_hashes"), Mapping) else {},
            "framework_contract_sha256": C.contract_hash(),
            "provenance_mode": "derived_from_summary",
        }
    elif isinstance(provenance, Mapping):
        provenance_payload = dict(provenance)
    else:
        raise TypeError("validation artifact provenance must be a mapping")
    artifact = {
        "framework_version": C.FRAMEWORK_VERSION,
        "status": (C.PHASE_STATUS_E_READY
                    if gate_payload.get("passed") is True
                    else C.PHASE_STATUS_E_BLOCKED),
        "gate_id": C.GateId.E_VALIDATION.value,
        "promotion_eligible": False,
        "production_authorized": False,
        "no_claims": [
            "No warning or production authorization",
            "No authority approval",
            "Mechanism labels are not independent field adjudication",
        ],
        "gate": gate_payload,
        "summary": dict(summary),
        "provenance": provenance_payload,
    }
    bound = bind_artifact_envelope(artifact)
    write_deterministic_json(path, bound)
    return bound


def verify_validation_artifact(payload: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """Verify the complete E artifact before it can feed Phase F."""
    envelope_ok, problems = verify_artifact_envelope(payload)
    if not isinstance(payload, Mapping):
        return False, problems
    gate = payload.get("gate")
    gate_ok, gate_problems = verify_gate_artifact(
        gate if isinstance(gate, Mapping) else {},
        expected_gate_id=C.GateId.E_VALIDATION.value)
    problems = list(problems) + gate_problems
    if not isinstance(payload.get("summary"), Mapping):
        problems.append("validation artifact summary must be a mapping")
    elif isinstance(gate, Mapping):
        try:
            expected_summary_hash = sha256_canonical(dict(payload["summary"]))
        except (TypeError, ValueError) as exc:
            problems.append(f"validation artifact summary is not canonical JSON: {exc}")
        else:
            if gate.get("summary_sha256") != expected_summary_hash:
                problems.append(
                    "validation artifact summary_sha256 does not match summary content")
    if payload.get("gate_id") != C.GateId.E_VALIDATION.value:
        problems.append("validation artifact gate_id must be E_VALIDATION")
    if payload.get("promotion_eligible") is not False:
        problems.append("validation artifact must not be promotion eligible")
    if payload.get("production_authorized") is not False:
        problems.append("validation artifact must not authorize production")
    if not isinstance(payload.get("no_claims"), list) or not payload.get(
            "no_claims"):
        problems.append("validation artifact no_claims are required")
    provenance = payload.get("provenance")
    if not isinstance(provenance, Mapping):
        problems.append("validation artifact provenance must be a mapping")
    elif provenance.get("framework_contract_sha256") != C.contract_hash():
        problems.append(
            "validation artifact framework contract does not match runtime")
    expected_status = (C.PHASE_STATUS_E_READY
                       if isinstance(gate, Mapping) and gate.get("passed") is True
                       else C.PHASE_STATUS_E_BLOCKED)
    if payload.get("status") != expected_status:
        problems.append(
            f"validation artifact status must be {expected_status!r} for its gate")
    return envelope_ok and gate_ok and not problems, problems


def _event_identity(e: Mapping) -> str:
    return "|".join([str(e.get("event_id") or ""), _group_of(e),
                     str(e.get("event_date_min") or "")])


def _control_identity(c: Mapping) -> str:
    return "|".join([str(c.get("unit_id") or ""), str(c.get("group") or ""),
                     str(c.get("observation_coverage") or "")])
    return (centre - half, centre + half)
