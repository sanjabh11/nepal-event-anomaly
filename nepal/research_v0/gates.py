"""Approval gates and leakage scanners for the research_v0 namespace.

Fail-closed boundaries:

* **Design approval (P3).**  ``build_claim_envelope`` requires the
  matrix and policy *file paths* plus their declared sha256 digests —
  the files are re-hashed from disk and a mismatch, missing file, or
  symlink is rejected.  A bare 64-hex string proves nothing.
* **Record protocol.**  Every bound record must implement
  ``problems()``/``to_dict()`` and validate clean before hashing;
  arbitrary mappings are rejected.
* **Blocker propagation.**  Unresolved blockers are permitted only on
  design-stage/blocked statuses; execution statuses
  (intake/regime/forecast) refuse while blockers remain.
* **Leakage scans.**  Occurrence feature matrices require a controlled
  ``field_class`` registry + source lineage, and reject B-derived
  payload names, denied exposure/impact names, post-issue availability,
  and — in forecast experiments — reanalysis or rolling current feeds.

A self-hash proves integrity, never external approval.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from ._hashing import sha256_canonical, sha256_file
from .policy import ForecastDataClass, require_finite_seconds
from .records import (BLOCKER_TOLERANT_STATUSES, NEUTRAL_RESEARCH_STATUSES,
                      ResearchClaimEnvelopeV0)

# ---------------------------------------------------------------------
# Design approval (P3)
# ---------------------------------------------------------------------

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

APPROVAL_REQUIRED_FIELDS = (
    "matrix_path",
    "policy_path",
    "matrix_sha256",
    "policy_sha256",
    "source_review_date",
    "selected_pilot_rule",
    "unresolved_blockers",
    "human_approved",
    "approved_by",
    "approved_at",
)


class DesignApprovalError(RuntimeError):
    """Raised when an envelope is requested without a complete,
    file-bound, human design-approval binding."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = list(problems)
        super().__init__("design approval binding incomplete: "
                         + "; ".join(self.problems))


def design_approval_problems(binding: Mapping[str, Any]) -> list[str]:
    """Validate a P3 approval binding.

    The binding must carry real filesystem paths for the decision matrix
    and cutoff policy documents plus their declared sha256 digests.  Each
    file is re-hashed from disk: missing files, symlinks, and digest
    mismatches are problems.  ``human_approved`` must be an explicit
    true with named approver and timestamp — a self-hash is not
    approval.
    """
    problems: list[str] = []
    for name in APPROVAL_REQUIRED_FIELDS:
        if name not in binding:
            problems.append(f"approval field {name!r} is missing")
    for name in ("matrix_sha256", "policy_sha256"):
        value = binding.get(name)
        if value is not None and not SHA256_RE.match(str(value)):
            problems.append(f"{name} must be a 64-hex sha256 digest")
    for name, declared in (("matrix_path", "matrix_sha256"),
                           ("policy_path", "policy_sha256")):
        raw_path = binding.get(name)
        if raw_path is None:
            continue
        path = Path(str(raw_path))
        if not path.is_file():
            problems.append(f"{name} {path} does not resolve to a "
                            "regular file")
            continue
        if path.is_symlink():
            problems.append(f"{name} {path} is a symlink — hash the "
                            "artifact itself")
            continue
        actual = sha256_file(path)
        declared_hex = str(binding.get(declared, ""))
        if SHA256_RE.match(declared_hex) and actual != declared_hex:
            problems.append(
                f"{name} digest mismatch: declared {declared_hex[:16]}… "
                f"!= file bytes {actual[:16]}…")
    if binding.get("human_approved") is not True:
        problems.append("human_approved must be an explicit true")
    for name in ("approved_by", "approved_at", "source_review_date",
                 "selected_pilot_rule"):
        value = binding.get(name)
        if value is not None and not str(value).strip():
            problems.append(f"{name} must be non-empty")
    blockers = binding.get("unresolved_blockers")
    if blockers is not None and not isinstance(blockers, (list, tuple)):
        problems.append("unresolved_blockers must be a list (possibly "
                        "empty)")
    return problems


# ---------------------------------------------------------------------
# Neutral research statuses (defined in records.py) — the only statuses
# an envelope may carry.
# ---------------------------------------------------------------------

def envelope_status_problems(status: str) -> list[str]:
    if status not in NEUTRAL_RESEARCH_STATUSES:
        return [f"status {status!r} is not an approved neutral research "
                "status"]
    return []


def _validate_record(name: str, record: Any) -> list[str]:
    """Every bound record must implement the record protocol and
    validate clean before it is hashed."""
    problems: list[str] = []
    if not (hasattr(record, "problems") and hasattr(record, "to_dict")):
        return [f"record {name!r} does not implement the record "
                "protocol (problems()+to_dict()) — arbitrary payloads "
                "cannot be bound"]
    try:
        rec_problems = list(record.problems())
    except Exception as exc:  # validation must fail closed
        return [f"record {name!r} validation raised {exc!r}"]
    problems.extend(f"record {name!r}: {p}" for p in rec_problems)
    try:
        record.to_dict()
    except Exception as exc:
        problems.append(f"record {name!r} serialization raised {exc!r}")
    return problems


def build_claim_envelope(
        envelope_id: str,
        status: str,
        records: Mapping[str, Any],
        approval: Mapping[str, Any]) -> dict[str, Any]:
    """Bind validated records into a research-only claim envelope.

    Raises :class:`DesignApprovalError` if the approval binding is
    incomplete or file hashes mismatch; :class:`ValueError` if the
    status is non-neutral, any record fails validation, or execution
    statuses are requested while unresolved blockers remain.
    """
    approval_problems = design_approval_problems(approval)
    if approval_problems:
        raise DesignApprovalError(approval_problems)
    problems = envelope_status_problems(status)
    blockers = list(approval.get("unresolved_blockers") or [])
    if blockers and status not in BLOCKER_TOLERANT_STATUSES:
        problems.append(
            f"status {status!r} is an execution status but "
            f"{len(blockers)} unresolved blocker(s) remain — blocked "
            "statuses only until blockers clear")
    for name, record in records.items():
        problems.extend(_validate_record(name, record))
    if problems:
        raise ValueError("; ".join(problems))
    digests: dict[str, str] = {}
    for name, record in records.items():
        digests[name] = sha256_canonical(record.to_dict())
    envelope = ResearchClaimEnvelopeV0(
        envelope_id=envelope_id,
        status=status,
        matrix_sha256=str(approval["matrix_sha256"]),
        policy_sha256=str(approval["policy_sha256"]),
        record_digests=digests,
    )
    out = envelope.to_dict()
    out["human_approved"] = True
    out["approved_by"] = str(approval["approved_by"])
    out["approved_at"] = str(approval["approved_at"])
    out["source_review_date"] = str(approval["source_review_date"])
    out["selected_pilot_rule"] = str(approval["selected_pilot_rule"])
    out["unresolved_blockers"] = blockers
    out["envelope_sha256"] = sha256_canonical(out)
    return out


# ---------------------------------------------------------------------
# Claim scan (A24): explicit forbidden-status/phrase scan over JSON
# payloads or text.  This IS the research-specific claim lint; if this
# module is absent, no lint exists and none may be claimed.
# ---------------------------------------------------------------------

FORBIDDEN_STATUS_VALUES = (
    "READY", "FMX_READY", "B_TO_C_READY", "WARNING_READY",
    "PRODUCTION_READY", "SCIENTIFICALLY_VALIDATED", "AUTHORITY_APPROVED",
    "OPERATIONALLY_AUTHORIZED")

# Truthy-authority JSON fragments that must never appear.
_FORBIDDEN_JSON_RE = re.compile(
    r'"(?:warning_path_authorized|production_authorized|'
    r'promotion_eligible|operationally_authorized|authority_approved)"'
    r'\s*:\s*true', re.IGNORECASE)

_FORBIDDEN_PHRASE_RE = re.compile(
    r"\b(operational warning|evacuation|production deploy|"
    r"warning threshold|alert level|scientifically validated|"
    r"authority approved)\b", re.IGNORECASE)

_STATUS_KEY_RE = re.compile(r'"(?:status|gate_status|readiness)"\s*:\s*'
                            r'"([A-Z_]+)"')


def scan_claims_text(text: str) -> list[str]:
    """Scan raw text/JSON for forbidden claim content.

    Returns a list of findings; empty means clean.  Detects
    READY-shaped statuses, truthy authority flags, and operational
    phrases.  ``B_TO_C_BLOCKED`` (factual blocked status) is allowed.
    """
    findings: list[str] = []
    for match in _STATUS_KEY_RE.finditer(text):
        value = match.group(1)
        if value in FORBIDDEN_STATUS_VALUES:
            findings.append(f"forbidden status value {value!r}")
    for match in _FORBIDDEN_JSON_RE.finditer(text):
        findings.append(f"truthy authority flag: {match.group(0)}")
    for match in _FORBIDDEN_PHRASE_RE.finditer(text):
        findings.append(f"operational phrase: {match.group(0)!r}")
    return findings


# ---------------------------------------------------------------------
# Leakage scanners
# ---------------------------------------------------------------------

# B-screen outputs may appear only as provenance digests, never as
# feature payloads.  Any feature name shaped like a rank/priority/score
# is rejected.
_B_DERIVED_NAME_RE = re.compile(
    r"(priority|rank|ranked|ranking|top_?\d+|screen_?score|"
    r"screen_?rank|b_?score|exposure_?rank)", re.IGNORECASE)

# Known exposure/impact field names that must never appear in an
# occurrence matrix even if renamed to look innocuous (A14).
_DENIED_OCCURRENCE_NAMES = frozenset({
    "ghsl", "worldpop", "population", "built_up", "builtup",
    "building_count", "fatality", "fatalities", "deaths", "damage",
    "loss", "affected_population", "exposure", "impact",
    "road_exposure", "settlement"})

# Controlled physical-occurrence field classes.  A feature must declare
# one of these plus a non-empty source_lineage; anything else rejects.
OCCURRENCE_FIELD_CLASSES = frozenset({
    "meteorological_reforecast", "meteorological_archived_operational",
    "terrain_static", "cryosphere_state", "hydrology_state",
    "observation_metadata", "catalog_label"})

_FORECAST_ADMISSIBLE_CLASSES = frozenset({
    ForecastDataClass.REFORECAST.value,
    ForecastDataClass.ARCHIVED_OPERATIONAL.value,
})

# Data-class to required field-class consistency (A15/G15): reforecast
# features must carry a forecast-class lineage, terrain may be static.
_FIELD_CLASS_ALLOWED_DATA = {
    "meteorological_reforecast": {ForecastDataClass.REFORECAST.value},
    "meteorological_archived_operational":
        {ForecastDataClass.ARCHIVED_OPERATIONAL.value},
    "terrain_static": {"STATIC"},
    "cryosphere_state": {ForecastDataClass.REFORECAST.value,
                         ForecastDataClass.ARCHIVED_OPERATIONAL.value,
                         "REANALYSIS_RETROSPECTIVE_ONLY"},
    "hydrology_state": {ForecastDataClass.REFORECAST.value,
                        ForecastDataClass.ARCHIVED_OPERATIONAL.value,
                        "REANALYSIS_RETROSPECTIVE_ONLY"},
    "observation_metadata": {"STATIC", "OBSERVATION"},
    "catalog_label": {"LABEL"},
}


def occurrence_feature_problems(
        features: Sequence[Mapping[str, Any]], *,
        issue_time: Optional[float]) -> list[str]:
    """Validate an occurrence-experiment feature matrix description.

    Each feature is a mapping with ``name``, ``namespace``
    (occurrence only), ``field_class`` (controlled registry),
    ``data_class`` (ForecastDataClass value or STATIC/OBSERVATION/LABEL),
    ``source_lineage`` (non-empty provenance string), and
    ``availability_time`` (provider availability — local retrieval never
    substitutes).
    """
    problems: list[str] = []
    issue = require_finite_seconds(issue_time)
    if issue_time is not None and issue is None:
        problems.append("issue_time is not a finite timestamp")
    for feat in features:
        name = str(feat.get("name", "<unnamed>"))
        namespace = feat.get("namespace")
        if namespace != "occurrence":
            problems.append(
                f"feature {name!r}: namespace {namespace!r} is not "
                "'occurrence' — exposure and impact variables are "
                "prohibited in physical-occurrence experiments")
        field_class = feat.get("field_class")
        if field_class not in OCCURRENCE_FIELD_CLASSES:
            problems.append(
                f"feature {name!r}: field_class {field_class!r} is not "
                "in the controlled occurrence registry — semantic "
                "classification is required, not just a safe name")
        if not str(feat.get("source_lineage") or "").strip():
            problems.append(
                f"feature {name!r}: source_lineage is required — "
                "provenance, not just a clean name")
        lowered = name.lower()
        if _B_DERIVED_NAME_RE.search(lowered):
            problems.append(
                f"feature {name!r}: B-derived rank/priority/score "
                "payloads are prohibited in feature matrices; only "
                "provenance digests may cross the boundary")
        if any(denied in lowered for denied in _DENIED_OCCURRENCE_NAMES):
            problems.append(
                f"feature {name!r}: name matches the exposure/impact "
                "denylist — cannot appear under the occurrence "
                "namespace")
        availability = require_finite_seconds(
            feat.get("availability_time"))
        if availability is None:
            problems.append(
                f"feature {name!r}: availability_time is missing or "
                "non-finite")
        elif issue is not None and availability > issue:
            problems.append(
                f"feature {name!r}: availability_time "
                f"({availability}) is after forecast issue_time "
                f"({issue}) — post-issue/future-published values "
                "are prohibited")
        data_class = feat.get("data_class")
        allowed = _FIELD_CLASS_ALLOWED_DATA.get(field_class)
        if field_class in OCCURRENCE_FIELD_CLASSES and \
                allowed is not None and data_class not in allowed:
            problems.append(
                f"feature {name!r}: data_class {data_class!r} is "
                f"inconsistent with field_class {field_class!r}")
    return problems


def forecast_feature_problems(
        features: Sequence[Mapping[str, Any]], *,
        issue_time: Optional[float]) -> list[str]:
    """Occurrence rules plus the forecast-data-class gate.

    In a forecast experiment every feature must be REFORECAST or
    ARCHIVED_OPERATIONAL; reanalysis and rolling current feeds are
    rejected outright.
    """
    problems = occurrence_feature_problems(features, issue_time=issue_time)
    for feat in features:
        name = str(feat.get("name", "<unnamed>"))
        data_class = feat.get("data_class")
        if data_class not in _FORECAST_ADMISSIBLE_CLASSES and \
                feat.get("field_class") not in (
                    "terrain_static", "observation_metadata",
                    "catalog_label"):
            problems.append(
                f"feature {name!r}: data_class {data_class!r} is not "
                "admissible in a forecast experiment — only REFORECAST "
                "or ARCHIVED_OPERATIONAL issue-time data may be used")
    return problems


def cascade_atomicity_problems(
        split_by_event: Mapping[str, str],
        cascade_groups: Mapping[str, Sequence[str]]) -> list[str]:
    """Every cascade/compound member must be mapped to exactly one
    shared split — a partially mapped cascade is a defect, not a pass."""
    problems: list[str] = []
    for group_id, event_ids in cascade_groups.items():
        if not event_ids:
            problems.append(f"cascade group {group_id!r} is empty")
            continue
        missing = [e for e in event_ids if e not in split_by_event]
        if missing:
            problems.append(
                f"cascade group {group_id!r} has unmapped members: "
                f"{sorted(missing)} — every member must be assigned")
        splits = {split_by_event[e] for e in event_ids
                  if e in split_by_event}
        if len(splits) > 1:
            problems.append(
                f"cascade group {group_id!r} spans multiple splits: "
                f"{sorted(splits)} — cascades are atomic")
    return problems
