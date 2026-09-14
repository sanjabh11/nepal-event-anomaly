"""Approval gates and leakage scanners for the research_v0 namespace.

Fail-closed boundaries (post-audit A02–A24, B01–B42):

* **Design approval (P3).**  ``build_claim_envelope`` requires real
  D1/D2 file paths contained inside a declared ``artifact_root``, with
  expected filenames, re-hashed bytes, strict dates, an attested
  approver, a fixed pilot-rule token, and ``design_review_only`` scope.
* **Record graph.**  Only approved record classes may be bound; every
  record validates via ``problems()``; cross-record foreign keys are
  checked; each status carries a required-record set — an empty
  execution-shaped envelope fails.
* **Blocker propagation.**  Unresolved blockers are permitted only on
  design-stage/blocked statuses.
* **Leakage scans.**  Occurrence matrices require controlled
  ``field_class`` + ``source_lineage`` + vintage binding; B-derived
  names, exposure/impact denylist names, catalog labels as predictors,
  post-issue availability, and non-forecast data classes all reject.
* **Claim scan.**  Casefolded, separator-normalized scanning catches
  READY-shaped statuses, truthy authority flags, and operational
  phrasing across JSON, Markdown, and text.

A self-hash proves integrity, never external approval.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from ._hashing import hash_artifact, sha256_canonical, sha256_file
from .policy import (ForecastDataClass, parse_strict_utc,
                     require_finite_seconds)
from .records import (BLOCKER_TOLERANT_STATUSES, NEUTRAL_RESEARCH_STATUSES,
                      RECORD_TYPES, STATUS_REQUIRED_RECORDS,
                      ResearchClaimEnvelopeV0)

# ---------------------------------------------------------------------
# Design approval (P3)
# ---------------------------------------------------------------------

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# The approval must bind the intended artifacts by name, inside a
# declared artifact root — /etc/hosts cannot satisfy a digest (B02).
EXPECTED_MATRIX_NAME = "HAZARD_EVENT_INVENTORY_DECISION_MATRIX_V0.md"
EXPECTED_POLICY_NAME = "INFORMATION_CUTOFF_TARGET_POLICY_V0.md"
EXPECTED_SCOPE = "design_review_only"
ALLOWED_PILOT_RULES = frozenset({
    "FIRST_PASSING_ALL_GATES_ELSE_NO_QUALIFYING"})

APPROVAL_REQUIRED_FIELDS = (
    "artifact_root",
    "matrix_path",
    "policy_path",
    "matrix_sha256",
    "policy_sha256",
    "source_review_date",
    "selected_pilot_rule",
    "approval_scope",
    "unresolved_blockers",
    "human_approved",
    "approved_by",
    "approver_attestation",
    "approved_at",
)


class DesignApprovalError(RuntimeError):
    """Raised when an envelope is requested without a complete,
    file-bound, human design-approval binding."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = list(problems)
        super().__init__("design approval binding incomplete: "
                         + "; ".join(self.problems))


def _contained(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def design_approval_problems(binding: Mapping[str, Any]) -> list[str]:
    """Validate a P3 approval binding (B01/B02 hardened).

    Required: a declared ``artifact_root``; matrix/policy paths that
    resolve inside it with the expected D1/D2 filenames; declared
    sha256 digests matching the actual file bytes; strict ISO dates;
    an attested approver; a fixed pilot-rule token; and
    ``design_review_only`` scope.  Missing files, symlinks,
    outside-root paths, wrong filenames, digest mismatches, malformed
    dates, and missing attestation are all problems.
    """
    problems: list[str] = []
    for name in APPROVAL_REQUIRED_FIELDS:
        if name not in binding:
            problems.append(f"approval field {name!r} is missing")
    for name in ("matrix_sha256", "policy_sha256"):
        value = binding.get(name)
        if value is not None and not SHA256_RE.match(str(value)):
            problems.append(f"{name} must be a 64-hex sha256 digest")

    root_raw = binding.get("artifact_root")
    root: Optional[Path] = None
    if root_raw is not None:
        root = Path(str(root_raw))
        if not root.is_dir():
            problems.append(f"artifact_root {root} is not a directory")
            root = None
    expected = {"matrix_path": (EXPECTED_MATRIX_NAME, "matrix_sha256"),
                "policy_path": (EXPECTED_POLICY_NAME, "policy_sha256")}
    for field, (expected_name, digest_field) in expected.items():
        raw_path = binding.get(field)
        if raw_path is None:
            continue
        path = Path(str(raw_path))
        if path.name != expected_name:
            problems.append(
                f"{field} must reference {expected_name!r}, got "
                f"{path.name!r}")
        if root is not None and not _contained(path, root):
            problems.append(
                f"{field} {path} resolves outside artifact_root")
        if not path.is_file():
            problems.append(f"{field} {path} does not resolve to a "
                            "regular file")
            continue
        if path.is_symlink():
            problems.append(f"{field} {path} is a symlink — hash the "
                            "artifact itself")
            continue
        actual = sha256_file(path)
        declared_hex = str(binding.get(digest_field, ""))
        if SHA256_RE.match(declared_hex) and actual != declared_hex:
            problems.append(
                f"{field} digest mismatch: declared "
                f"{declared_hex[:16]}… != file bytes {actual[:16]}…")

    review_date = binding.get("source_review_date")
    if review_date is not None and not _DATE_RE.match(str(review_date)):
        problems.append("source_review_date must be an ISO YYYY-MM-DD "
                        "date")
    if binding.get("approved_at") is not None and \
            parse_strict_utc(binding.get("approved_at")) is None:
        problems.append("approved_at must be an explicit-UTC timestamp")
    if binding.get("approval_scope") is not None and \
            binding.get("approval_scope") != EXPECTED_SCOPE:
        problems.append(f"approval_scope must be {EXPECTED_SCOPE!r} — "
                        "design approval never authorizes intake, "
                        "warnings, or production")
    rule = binding.get("selected_pilot_rule")
    if rule is not None and str(rule) not in ALLOWED_PILOT_RULES:
        problems.append(
            f"selected_pilot_rule {rule!r} is not an approved rule "
            f"token {sorted(ALLOWED_PILOT_RULES)}")
    if binding.get("human_approved") is not True:
        problems.append("human_approved must be an explicit true")
    for name in ("approved_by", "approver_attestation"):
        value = binding.get(name)
        if value is not None and not str(value).strip():
            problems.append(f"{name} must be non-empty")
    blockers = binding.get("unresolved_blockers")
    if blockers is not None and not isinstance(blockers, (list, tuple)):
        problems.append("unresolved_blockers must be a list (possibly "
                        "empty)")
    return problems


# ---------------------------------------------------------------------
# Envelope construction: status graph + record protocol + foreign keys
# ---------------------------------------------------------------------

def envelope_status_problems(status: str) -> list[str]:
    if status not in NEUTRAL_RESEARCH_STATUSES:
        return [f"status {status!r} is not an approved neutral research "
                "status"]
    return []


def _record_class_name(record: Any) -> str:
    return type(record).__name__


def _validate_record(name: str, record: Any) -> list[str]:
    """Every bound record must be an approved record class, implement
    the protocol, and validate clean before hashing."""
    problems: list[str] = []
    cls = _record_class_name(record)
    if cls not in RECORD_TYPES:
        return [f"record {name!r}: type {cls!r} is not an approved "
                f"record class {sorted(RECORD_TYPES)}"]
    if not (hasattr(record, "problems") and hasattr(record, "to_dict")):
        return [f"record {name!r} does not implement the record "
                "protocol (problems()+to_dict())"]
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


def _cross_record_problems(records: Mapping[str, Any]) -> list[str]:
    """Foreign-key consistency across the bound record set (B05)."""
    problems: list[str] = []
    sources = {r.source_id for r in records.values()
               if _record_class_name(r) == "SourceRecordV0"}
    verified = {r.source_id for r in records.values()
                if _record_class_name(r) == "SourceRecordV0"
                and r.posture == "EVIDENCE_VERIFIED"}
    verticals = {r.vertical_id for r in records.values()
                 if _record_class_name(r) == "HazardVerticalSpecV0"}
    opportunities = {r.opportunity_id for r in records.values()
                     if _record_class_name(r) ==
                     "ObservationOpportunityV0"}
    holdouts = {r.holdout_plan_id for r in records.values()
                if _record_class_name(r) == "HoldoutPlanV0"}
    event_ids: set[str] = set()
    for name, record in records.items():
        cls = _record_class_name(record)
        if cls == "EventLabelV0":
            if sources and record.source_id not in sources:
                problems.append(
                    f"record {name!r}: source_id "
                    f"{record.source_id!r} not among bound sources")
            if verticals and record.vertical_id not in verticals:
                problems.append(
                    f"record {name!r}: vertical_id "
                    f"{record.vertical_id!r} not among bound verticals")
            if record.event_id in event_ids:
                problems.append(
                    f"record {name!r}: duplicate event_id "
                    f"{record.event_id!r}")
            event_ids.add(record.event_id)
        elif cls == "ControlWindowV0":
            if opportunities and \
                    record.opportunity_id not in opportunities:
                problems.append(
                    f"record {name!r}: opportunity_id "
                    f"{record.opportunity_id!r} not among bound "
                    "opportunities")
        elif cls == "ObservationOpportunityV0":
            if sources and record.source_id and \
                    record.source_id not in sources:
                problems.append(
                    f"record {name!r}: source_id "
                    f"{record.source_id!r} not among bound sources")
        elif cls == "ForecastExperimentV0":
            if holdouts and record.holdout_plan_id not in holdouts:
                problems.append(
                    f"record {name!r}: holdout_plan_id "
                    f"{record.holdout_plan_id!r} not among bound "
                    "holdouts")
            if verticals and record.vertical_id not in verticals:
                problems.append(
                    f"record {name!r}: vertical_id "
                    f"{record.vertical_id!r} not among bound verticals")
            bound_vintages = {
                sha256_canonical(r.to_dict())
                for r in records.values()
                if _record_class_name(r) == "ForecastVintageV0"}
            for digest in record.vintage_digests:
                if bound_vintages and digest not in bound_vintages:
                    problems.append(
                        f"record {name!r}: vintage_digest "
                        f"{digest[:16]}… not among bound vintages")
        elif cls == "HazardVerticalSpecV0":
            if record.pilot_gate_status == "PILOT_GATE_PASSED":
                for sid in record.candidate_source_ids:
                    if sid not in verified:
                        problems.append(
                            f"record {name!r}: PILOT_GATE_PASSED "
                            f"references {sid!r} which is not a bound "
                            "EVIDENCE_VERIFIED source")
    return problems


def _status_record_problems(status: str,
                            records: Mapping[str, Any]) -> list[str]:
    required = STATUS_REQUIRED_RECORDS.get(status, ())
    if not required:
        return []
    problems: list[str] = []
    present = {_record_class_name(r) for r in records.values()}
    for needed in required:
        if needed not in present:
            problems.append(
                f"status {status} requires a bound {needed} record")
    if required and not records:
        problems.append(f"status {status} cannot be emitted with an "
                        "empty record set")
    return problems


def build_claim_envelope(
        envelope_id: str,
        status: str,
        records: Mapping[str, Any],
        approval: Mapping[str, Any]) -> dict[str, Any]:
    """Bind validated records into a research-only claim envelope.

    Raises :class:`DesignApprovalError` on incomplete/file-mismatched
    approval; :class:`ValueError` on non-neutral status, invalid or
    unapproved records, broken cross-record references, missing
    required-record coverage, or unresolved blockers on an execution
    status.
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
    problems.extend(_status_record_problems(status, records))
    for name, record in records.items():
        problems.extend(_validate_record(name, record))
    problems.extend(_cross_record_problems(records))
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
    out["approver_attestation"] = str(approval["approver_attestation"])
    out["approved_at"] = str(approval["approved_at"])
    out["approval_scope"] = str(approval["approval_scope"])
    out["source_review_date"] = str(approval["source_review_date"])
    out["selected_pilot_rule"] = str(approval["selected_pilot_rule"])
    out["unresolved_blockers"] = blockers
    out["envelope_sha256"] = sha256_canonical(out)
    return out


# ---------------------------------------------------------------------
# Byte-bound source-evidence verification (B06)
# ---------------------------------------------------------------------

def source_evidence_problems(record: Any, *,
                             evidence_root: Any) -> list[str]:
    """Verify a SourceRecordV0's evidence sidecar against actual bytes.

    Required when ``posture == "EVIDENCE_VERIFIED"``: the sidecar must
    be a real, non-symlink file contained under ``evidence_root`` whose
    sha256 matches the declared digest.
    """
    problems: list[str] = []
    if getattr(record, "posture", None) != "EVIDENCE_VERIFIED":
        return problems
    root = Path(str(evidence_root))
    if not root.is_dir():
        return [f"evidence_root {root} is not a directory"]
    raw = getattr(record, "evidence_sidecar_path", "")
    if not raw:
        return ["evidence_sidecar_path is required for "
                "EVIDENCE_VERIFIED"]
    path = Path(str(raw))
    if not _contained(path, root):
        problems.append(f"evidence sidecar {path} resolves outside "
                        "evidence_root")
    if not path.is_file():
        problems.append(f"evidence sidecar {path} is not a regular "
                        "file")
        return problems
    if path.is_symlink():
        problems.append(f"evidence sidecar {path} is a symlink")
        return problems
    actual = sha256_file(path)
    declared = getattr(record, "evidence_sidecar_sha256", "")
    if actual != declared:
        problems.append(
            f"evidence sidecar digest mismatch: declared "
            f"{str(declared)[:16]}… != file bytes {actual[:16]}…")
    return problems


# ---------------------------------------------------------------------
# Claim scan (B18/B36): normalized structural scanning over JSON,
# Markdown, manifests, and text.
# ---------------------------------------------------------------------

FORBIDDEN_STATUS_TOKENS = frozenset({
    "READY", "FMX_READY", "B_TO_C_READY", "WARNING_READY",
    "PRODUCTION_READY", "SCIENTIFICALLY_VALIDATED", "AUTHORITY_APPROVED",
    "OPERATIONALLY_AUTHORIZED", "PILOT_READY", "FORECAST_READY"})

_AUTHORITY_FLAG_RE = re.compile(
    r'"(?:warning[_\-\s]*path[_\-\s]*authorized|'
    r'production[_\-\s]*authorized|promotion[_\-\s]*eligible|'
    r'operationally[_\-\s]*authorized|authority[_\-\s]*approved)"'
    r'\s*:\s*true', re.IGNORECASE)

_OPERATIONAL_PHRASE_RE = re.compile(
    r"\b(operational\W+warning|evacuation|production\W+deploy"
    r"|warning\W+threshold|alert\W+level|scientifically\W+validated|"
    r"authority\W+approved|issue\W+a\W+warning|warning\W+issued)\b",
    re.IGNORECASE)

_STATUS_FIELD_RE = re.compile(
    r'"(?:status|gate[_\-\s]*status|readiness)"\s*:\s*"([^"]+)"',
    re.IGNORECASE)


def _normalize_token(text: str) -> str:
    return re.sub(r"[\s\-]+", "_", text.strip()).upper()


def scan_claims_text(text: str) -> list[str]:
    """Scan raw text/JSON/Markdown for forbidden claim content.

    Casefolded and separator-normalized: ``fmx-ready``,
    ``"warning path authorized": true`` and ``Operational Warning``
    phrasing are all detected.  ``B_TO_C_BLOCKED`` (factual blocked
    status) is allowed.  Returns findings; empty means clean.
    """
    findings: list[str] = []
    for match in _STATUS_FIELD_RE.finditer(text):
        token = _normalize_token(match.group(1))
        if token in FORBIDDEN_STATUS_TOKENS:
            findings.append(f"forbidden status value {token!r}")
    for match in _AUTHORITY_FLAG_RE.finditer(text):
        findings.append(f"truthy authority flag: {match.group(0)}")
    for match in _OPERATIONAL_PHRASE_RE.finditer(text):
        findings.append(f"operational phrase: {match.group(0)!r}")
    return findings


# ---------------------------------------------------------------------
# Leakage scanners
# ---------------------------------------------------------------------

_B_DERIVED_NAME_RE = re.compile(
    r"(priority|rank|ranked|ranking|top_?\d+|screen_?score|"
    r"screen_?rank|b_?score|exposure_?rank)", re.IGNORECASE)

_DENIED_OCCURRENCE_NAMES = frozenset({
    "ghsl", "worldpop", "population", "built_up", "builtup",
    "building_count", "fatality", "fatalities", "deaths", "damage",
    "loss", "affected_population", "exposure", "impact",
    "road_exposure", "settlement"})

# Controlled physical-occurrence field classes.  ``catalog_label`` is
# admissible in occurrence matrices as a LABEL channel only — it is
# never a predictor and is excluded from forecast features (B17).
OCCURRENCE_FIELD_CLASSES = frozenset({
    "meteorological_reforecast", "meteorological_archived_operational",
    "terrain_static", "cryosphere_state", "hydrology_state",
    "observation_metadata", "catalog_label"})

_FORECAST_ADMISSIBLE_CLASSES = frozenset({
    ForecastDataClass.REFORECAST.value,
    ForecastDataClass.ARCHIVED_OPERATIONAL.value,
})

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

    Required per feature: ``name``, ``namespace="occurrence"``,
    ``field_class`` (controlled registry), ``data_class``,
    ``source_lineage`` (non-empty), ``availability_time`` (finite,
    <= issue_time), ``valid_start``/``valid_end`` (finite, ordered),
    and ``vintage_digest`` (64-hex) for forecast-class features.
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
                "in the controlled occurrence registry")
        if not str(feat.get("source_lineage") or "").strip():
            problems.append(
                f"feature {name!r}: source_lineage is required")
        lowered = name.lower()
        if _B_DERIVED_NAME_RE.search(lowered):
            problems.append(
                f"feature {name!r}: B-derived rank/priority/score "
                "payloads are prohibited; only provenance digests may "
                "cross the boundary")
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
        # Valid-window binding (B17/B20).
        vs = require_finite_seconds(feat.get("valid_start"))
        ve = require_finite_seconds(feat.get("valid_end"))
        if vs is None or ve is None:
            problems.append(
                f"feature {name!r}: valid_start/valid_end are required "
                "finite timestamps")
        elif ve <= vs:
            problems.append(
                f"feature {name!r}: valid_end must be after valid_start")
        data_class = feat.get("data_class")
        allowed = _FIELD_CLASS_ALLOWED_DATA.get(field_class)
        if field_class in OCCURRENCE_FIELD_CLASSES and \
                allowed is not None and data_class not in allowed:
            problems.append(
                f"feature {name!r}: data_class {data_class!r} is "
                f"inconsistent with field_class {field_class!r}")
        if field_class in ("meteorological_reforecast",
                           "meteorological_archived_operational",
                           "cryosphere_state", "hydrology_state"):
            vd = feat.get("vintage_digest")
            if not isinstance(vd, str) or not SHA256_RE.match(vd):
                problems.append(
                    f"feature {name!r}: forecast/cryosphere/hydrology "
                    "features must bind a 64-hex vintage_digest")
    return problems


def forecast_feature_problems(
        features: Sequence[Mapping[str, Any]], *,
        issue_time: Optional[float]) -> list[str]:
    """Occurrence rules plus the forecast-data-class gate.

    In a forecast experiment every feature must be REFORECAST or
    ARCHIVED_OPERATIONAL (terrain/observation context may be static);
    reanalysis, rolling feeds, and catalog/event labels used as
    predictors are all rejected.
    """
    if not features:
        return ["forecast feature set is empty — an empty matrix "
                "cannot be an experiment"]
    problems = occurrence_feature_problems(features, issue_time=issue_time)
    for feat in features:
        name = str(feat.get("name", "<unnamed>"))
        if feat.get("field_class") == "catalog_label":
            problems.append(
                f"feature {name!r}: catalog/event labels are targets, "
                "never predictors")
            continue
        data_class = feat.get("data_class")
        if data_class not in _FORECAST_ADMISSIBLE_CLASSES and \
                feat.get("field_class") not in (
                    "terrain_static", "observation_metadata"):
            problems.append(
                f"feature {name!r}: data_class {data_class!r} is not "
                "admissible in a forecast experiment — only REFORECAST "
                "or ARCHIVED_OPERATIONAL issue-time data may be used")
    return problems


def cascade_atomicity_problems(
        split_by_event: Mapping[str, str],
        cascade_groups: Mapping[str, Sequence[str]]) -> list[str]:
    """Every cascade/compound member must map to exactly one shared,
    validly named split; duplicate IDs and invalid splits reject."""
    problems: list[str] = []
    seen: dict[str, str] = {}
    for group_id, event_ids in cascade_groups.items():
        if not event_ids:
            problems.append(f"cascade group {group_id!r} is empty")
            continue
        for event_id in event_ids:
            if event_id in seen and seen[event_id] != group_id:
                problems.append(
                    f"event {event_id!r} appears in multiple cascade "
                    f"groups ({seen[event_id]!r}, {group_id!r})")
            seen[event_id] = group_id
        missing = [e for e in event_ids if e not in split_by_event]
        if missing:
            problems.append(
                f"cascade group {group_id!r} has unmapped members: "
                f"{sorted(missing)} — every member must be assigned")
        splits = {split_by_event[e] for e in event_ids
                  if e in split_by_event}
        invalid = splits - {"train", "validation", "test"}
        if invalid:
            problems.append(
                f"cascade group {group_id!r} maps to invalid split "
                f"names: {sorted(invalid)}")
        elif len(splits) > 1:
            problems.append(
                f"cascade group {group_id!r} spans multiple splits: "
                f"{sorted(splits)} — cascades are atomic")
    return problems
