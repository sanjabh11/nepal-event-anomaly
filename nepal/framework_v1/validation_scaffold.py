"""T2S validation scaffold — schema-only research bridge (T2S-*).

A typed, self-hashed scaffold envelope that records *how* a future
Tranche-1+ validation would be organized — typed references to a MEC
contract and an FMX envelope, split metadata (temporal embargo, geographic
holdout, group-disjoint event separation), and a research-only metric
registry — while executing **nothing**.

Guarantees:

* mode is always ``VALIDATION_SCAFFOLD_ONLY`` and the scaffold status is
  always ``BLOCKED_PENDING_FMX`` in this tranche;
* inherited state must record ``B_TO_C_BLOCKED``, ``E_BLOCKED``,
  ``F_BLOCKED``, and ``ranking_rerun=false`` — a claimed ready/passed state
  is rejected;
* references are typed digests — the B ranked array and priority values
  are never accepted as inputs;
* no caller-supplied gate/verdict booleans;
* no execution entry points exist: nothing here calls B ranking, LOO,
  GMM, Isolation Forest, change-point, anomaly, or ``run_validation`` —
  there is deliberately no such function in this module;
* metric registry entries are descriptive metadata only and may not carry
  operational thresholds.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from .provenance import bind_artifact_envelope, verify_artifact_envelope
from .research_boundaries import QUARANTINED_MODULES

SCAFFOLD_ENVELOPE_TYPE = "VALIDATION_SCAFFOLD_V1"
MODE_SCAFFOLD_ONLY = "VALIDATION_SCAFFOLD_ONLY"
BLOCKED_PENDING_FMX = "BLOCKED_PENDING_FMX"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_REQUIRED_B_STATUS = "B_TO_C_BLOCKED"
_REQUIRED_E_STATUS = "E_BLOCKED"
_REQUIRED_F_STATUS = "F_BLOCKED"
_ALLOWED_FMX_STATUSES = ("FMX_BLOCKED_PENDING_EXPLICIT_FREEZE",)

_FORBIDDEN_REFERENCE_TOKENS = QUARANTINED_MODULES
_FORBIDDEN_CALLER_KEYS = ("gate_override", "verdict", "passed",
                          "gate_passed", "force_ready")
_METRIC_CLASSES = ("calibration", "discrimination", "lead_time",
                   "false_alarm", "skill")


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA256_RE.fullmatch(value))


def _iter_strings(obj: Any, prefix: str = ""):
    if isinstance(obj, str):
        yield prefix, obj
    elif isinstance(obj, Mapping):
        for key, value in obj.items():
            yield from _iter_strings(value, f"{prefix}{key}.")
    elif isinstance(obj, (list, tuple)):
        for index, value in enumerate(obj):
            yield from _iter_strings(value, f"{prefix}[{index}].")


def _check(payload: Mapping[str, Any], problems: list[str]) -> None:
    if payload.get("mode") != MODE_SCAFFOLD_ONLY:
        problems.append(f"mode must be {MODE_SCAFFOLD_ONLY!r}")
    if payload.get("research_diagnostic_only") is not True:
        problems.append("research_diagnostic_only must be true")
    for key in _FORBIDDEN_CALLER_KEYS:
        if key in payload:
            problems.append(f"caller-supplied verdict key {key!r} is "
                            "forbidden — the scaffold cannot be promoted "
                            "by argument")

    refs = payload.get("references")
    if not isinstance(refs, Mapping):
        problems.append("references must be a mapping")
        refs = {}
    mec_ref = refs.get("mec_reference")
    if not isinstance(mec_ref, Mapping) or not _is_sha256(
            mec_ref.get("envelope_sha256")) or mec_ref.get(
            "envelope_type") != "MULTI_EVENT_CONTRACT_V1":
        problems.append("references.mec_reference must be a typed MEC "
                        "envelope digest reference")
    fmx_ref = refs.get("fmx_reference")
    if not isinstance(fmx_ref, Mapping) or not _is_sha256(
            fmx_ref.get("envelope_sha256")):
        problems.append("references.fmx_reference must be a typed FMX "
                        "envelope digest reference")
    elif fmx_ref.get("fmx_status") not in _ALLOWED_FMX_STATUSES:
        problems.append(
            "references.fmx_reference.fmx_status must be "
            "FMX_BLOCKED_PENDING_EXPLICIT_FREEZE — the scaffold cannot "
            "advance on an unverified matrix")
    b_ref = refs.get("b_reference")
    if not isinstance(b_ref, Mapping) or not _is_sha256(
            b_ref.get("ranked_array_canonical_sha256")):
        problems.append("references.b_reference must carry the "
                        "ranked_array_canonical_sha256 digest")
    elif b_ref.get("status") != _REQUIRED_B_STATUS:
        problems.append("references.b_reference.status must be the "
                        f"inherited {_REQUIRED_B_STATUS!r} state")
    for dotted, value in _iter_strings(refs):
        for tok in _FORBIDDEN_REFERENCE_TOKENS:
            if tok in value:
                problems.append(f"references field {dotted!r} names a "
                                f"quarantined legacy module {tok!r}")
    for rname, rval in refs.items():
        if not isinstance(rval, Mapping):
            problems.append(f"references.{rname} must be a digest "
                            "reference mapping")
            continue
        for rkey, rvalue in rval.items():
            if isinstance(rvalue, (list, tuple)):
                problems.append(
                    f"references.{rname}.{rkey} carries a data payload; "
                    "only scalar digest references are permitted")

    inherited = payload.get("inherited_state")
    if not isinstance(inherited, Mapping):
        problems.append("inherited_state must be a mapping")
        inherited = {}
    else:
        if inherited.get("b_status") != _REQUIRED_B_STATUS:
            problems.append(f"inherited_state.b_status must be "
                            f"{_REQUIRED_B_STATUS!r}")
        if inherited.get("e_status") != _REQUIRED_E_STATUS:
            problems.append(f"inherited_state.e_status must be "
                            f"{_REQUIRED_E_STATUS!r}")
        if inherited.get("f_status") != _REQUIRED_F_STATUS:
            problems.append(f"inherited_state.f_status must be "
                            f"{_REQUIRED_F_STATUS!r}")
        if inherited.get("ranking_rerun") is not False:
            problems.append("inherited_state.ranking_rerun must be false")

    split = payload.get("split_spec")
    if not isinstance(split, Mapping):
        problems.append("split_spec must be a mapping")
        split = {}
    else:
        if not isinstance(split.get("temporal_embargo_days"), int) or \
                split["temporal_embargo_days"] < 0:
            problems.append("split_spec.temporal_embargo_days must be a "
                            "non-negative integer")
        geo = split.get("geographic_holdout")
        if not isinstance(geo, Mapping) or not isinstance(
                geo.get("min_separation_km"), (int, float)) or \
                geo.get("min_separation_km", 0) <= 0:
            problems.append("split_spec.geographic_holdout."
                            "min_separation_km must be positive")
        sep = split.get("event_separation")
        if not isinstance(sep, Mapping) or sep.get("group_disjoint") \
                is not True:
            problems.append("split_spec.event_separation.group_disjoint "
                            "must be true")

    registry = payload.get("metric_registry")
    if not isinstance(registry, list) or not registry:
        problems.append("metric_registry must be a non-empty list")
        registry = []
    for i, m in enumerate(registry):
        label = f"metric_registry[{i}]"
        if not isinstance(m, Mapping):
            problems.append(f"{label} must be a mapping")
            continue
        if not isinstance(m.get("metric_id"), str) or not m["metric_id"]:
            problems.append(f"{label}.metric_id is required")
        if m.get("class") not in _METRIC_CLASSES:
            problems.append(f"{label}.class must be one of "
                            f"{_METRIC_CLASSES}")
        if m.get("operational_threshold") is not None:
            problems.append(f"{label}.operational_threshold must be null "
                            "— research metrics carry no operational "
                            "thresholds")


def build_scaffold_envelope(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and bind a T2S scaffold envelope.  Status is always
    ``BLOCKED_PENDING_FMX`` — no execution, no promotion."""
    if not isinstance(payload, Mapping):
        raise TypeError("scaffold payload must be a mapping")
    problems: list[str] = []
    _check(payload, problems)
    if problems:
        raise ValueError("T2S scaffold payload is not valid: "
                         + "; ".join(problems[:6]))
    envelope = dict(payload)
    envelope["envelope_type"] = SCAFFOLD_ENVELOPE_TYPE
    envelope["scaffold_status"] = BLOCKED_PENDING_FMX
    envelope["promotion_eligible"] = False
    envelope["production_authorized"] = False
    envelope["no_claims"] = [
        "validation scaffold schema only; no validation was executed",
        "no scientific validation, warning, production, or authority "
        "readiness is established"]
    return bind_artifact_envelope(envelope)


def verify_scaffold_envelope(payload: Any) -> tuple[bool, list[str]]:
    """Fail-closed verification: self-hash, structural contract, and the
    invariant that the scaffold status is the blocked state."""
    problems: list[str] = []
    ok, env_problems = verify_artifact_envelope(payload)
    if not ok:
        problems.extend(env_problems)
    if not isinstance(payload, Mapping):
        problems.append("scaffold envelope must be a mapping")
        return False, problems
    if payload.get("envelope_type") != SCAFFOLD_ENVELOPE_TYPE:
        problems.append(f"envelope_type must be {SCAFFOLD_ENVELOPE_TYPE!r}")
    if payload.get("scaffold_status") != BLOCKED_PENDING_FMX:
        problems.append(f"scaffold_status must be {BLOCKED_PENDING_FMX!r}")
    if payload.get("promotion_eligible") is not False:
        problems.append("promotion_eligible must be false")
    if payload.get("production_authorized") is not False:
        problems.append("production_authorized must be false")
    _check(payload, problems)
    return (not problems), problems
