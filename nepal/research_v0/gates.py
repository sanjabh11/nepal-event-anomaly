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

import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from ._hashing import (hash_artifact, read_evidence_file,
                       sha256_canonical, sha256_file)
from .policy import (ForecastDataClass, parse_strict_utc,
                     require_finite_seconds)
from .records import (BLOCKER_TOLERANT_STATUSES, EXECUTION_STATUSES,
                      NEUTRAL_RESEARCH_STATUSES, RECORD_CLASSES,
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

#: The declared producer gate universe for a terminal science_v0
#: regime artifact — ``stability.required_gates`` must carry exactly
#: this set at freeze, adapter, replay, and audit boundaries.  An
#: omitted gate is not a closed gate; an extra gate is undeclared
#: evidence.
REQUIRED_REGIME_GATE_NAMES = frozenset({
    "seed_policy", "modal_k_unanimous", "seed_ari", "seed_coverage",
    "loro", "temporal_bootstrap", "season_refits", "elevation",
    "missingness", "effort", "era_drift", "shuffled_null",
    "season_matched_null"})


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
    "approver_role",
    "approver_attestation",
    "approved_at",
)

# Structured attestation floor (C01): code can require an explicit
# scope statement and named role — it cannot authenticate identity;
# that remains the human P3 gate.
EXPECTED_ATTESTATION = (
    "I reviewed only the design documents identified by their sha256 "
    "digests; this approval authorizes no data intake, no forecast "
    "execution, no warnings, and no production or authority action.")


def _valid_calendar_date(value: Any) -> bool:
    """Calendar-aware YYYY-MM-DD check — `2026-99-99` is rejected."""
    if not isinstance(value, str) or not _DATE_RE.match(value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


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


def _resolve_against(path_value: Any, root: Path) -> Path:
    """Resolve a declared path.  Relative paths resolve against the
    declared root, never the process CWD (D01)."""
    p = Path(str(path_value))
    if not p.is_absolute():
        p = root / p
    return p


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
        if root.resolve() == Path(root.anchor).resolve():
            problems.append("artifact_root may not be the filesystem "
                            "root")
        elif not root.is_dir():
            problems.append(f"artifact_root {root} is not a directory")
            root = None
    expected = {"matrix_path": (EXPECTED_MATRIX_NAME, "matrix_sha256"),
                "policy_path": (EXPECTED_POLICY_NAME, "policy_sha256")}
    seen_paths: dict[Path, str] = {}
    for field, (expected_name, digest_field) in expected.items():
        raw_path = binding.get(field)
        if raw_path is None:
            continue
        path = _resolve_against(raw_path, root) if root else \
            Path(str(raw_path))
        if path.resolve() in seen_paths:
            problems.append(
                f"{field} resolves to the same file as "
                f"{seen_paths[path.resolve()]} — the two artifacts "
                "must be distinct")
        else:
            seen_paths[path.resolve()] = field
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
    if review_date is not None and not _valid_calendar_date(review_date):
        problems.append("source_review_date must be a real ISO "
                        "YYYY-MM-DD calendar date")
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
    for name in ("approved_by", "approver_role"):
        value = binding.get(name)
        if value is not None and not str(value).strip():
            problems.append(f"{name} must be non-empty")
    attestation = binding.get("approver_attestation")
    if attestation is not None and attestation != EXPECTED_ATTESTATION:
        problems.append("approver_attestation must be the exact "
                        "design-scope statement — free-text "
                        "attestations are not approval")
    blockers = binding.get("unresolved_blockers")
    if blockers is not None:
        if not isinstance(blockers, (list, tuple)):
            problems.append("unresolved_blockers must be a list "
                            "(possibly empty)")
        elif any(not isinstance(b, str) or not b.strip()
                 for b in blockers):
            problems.append("every unresolved_blocker must be a "
                            "non-empty string")
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
    """Every bound record must be an instance of an approved record
    class — exact identity, not a name or duck-typed clone (C04)."""
    if not any(type(record) is cls for cls in RECORD_CLASSES.values()):
        return [f"record {name!r}: type {type(record).__name__!r} is "
                f"not an approved record class {sorted(RECORD_TYPES)}"]
    problems: list[str] = []
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


def _records_of(records: Mapping[str, Any], cls: type) -> list[Any]:
    return [r for r in records.values() if type(r) is cls]


def _cross_record_problems(records: Mapping[str, Any],
                           status: str) -> list[str]:
    """Mandatory foreign-key consistency (C05): references must point
    at actually bound parents — an empty parent set rejects, not
    skips."""
    from .records import (ControlWindowV0, CutoffRecordV0,
                          EventLabelV0, EvidenceArtifactV0,
                          ForecastExperimentV0, ForecastVintageV0,
                          HazardVerticalSpecV0, HoldoutPlanV0,
                          ObservationOpportunityV0, RegimeArtifactV0,
                          SourceRecordV0)
    from .policy import (assign_target_state_typed,
                         derive_control_state_typed)
    from .records import STATUS_REQUIRED_ARTIFACT_TYPES
    problems: list[str] = []
    sources = {r.source_id: r for r in _records_of(records,
                                                  SourceRecordV0)}
    verified = {sid for sid, r in sources.items()
                if r.posture == "EVIDENCE_VERIFIED"}
    verticals = {r.vertical_id for r in _records_of(
        records, HazardVerticalSpecV0)}
    opportunities = {r.opportunity_id: r for r in _records_of(
        records, ObservationOpportunityV0)}
    holdouts = {r.holdout_plan_id: r for r in _records_of(
        records, HoldoutPlanV0)}
    vintage_ids = {r.vintage_id for r in _records_of(
        records, ForecastVintageV0)}
    vintages = {sha256_canonical(r.to_dict())
                for r in _records_of(records, ForecastVintageV0)}
    artifact_records = _records_of(records, EvidenceArtifactV0)
    artifacts = {r.sha256 for r in artifact_records}
    # One artifact, one role (E04): the same file bytes may not be
    # bound under two different artifact types — role reuse rejects.
    if len(artifacts) != len(artifact_records):
        problems.append(
            "duplicate artifact sha256 — one byte sequence may not "
            "satisfy multiple artifact roles")
    artifacts_by_type: dict[str, set[str]] = {}
    for a in artifact_records:
        artifacts_by_type.setdefault(a.artifact_type, set()).add(
            a.sha256)
    # Pre-collect the event universe so forward lineage references
    # don't depend on iteration order.
    all_event_ids = {r.event_id for r in _records_of(records,
                                                     EventLabelV0)}
    event_ids: set[str] = set()

    # E12 — per-type primary-ID uniqueness across the bound set.
    _id_fields = {
        SourceRecordV0: "source_id",
        HazardVerticalSpecV0: "vertical_id",
        EventLabelV0: "event_id",
        ObservationOpportunityV0: "opportunity_id",
        ControlWindowV0: "control_id",
        CutoffRecordV0: "cutoff_id",
        HoldoutPlanV0: "holdout_plan_id",
        ForecastVintageV0: "vintage_id",
        RegimeArtifactV0: "regime_id",
        ForecastExperimentV0: "experiment_id",
        EvidenceArtifactV0: "artifact_id",
    }
    for cls, field_name in _id_fields.items():
        ids = [getattr(r, field_name)
               for r in _records_of(records, cls)]
        if len(set(ids)) != len(ids):
            problems.append(
                f"duplicate {field_name} values among bound "
                f"{cls.__name__} records")

    # E09 — holdout assignment keys must equal the bound event
    # universe exactly: no ghosts, no omissions.
    labels = _records_of(records, EventLabelV0)
    if labels:
        for hp in holdouts.values():
            if set(hp.event_assignments) != all_event_ids:
                missing = all_event_ids - set(hp.event_assignments)
                extra = set(hp.event_assignments) - all_event_ids
                problems.append(
                    f"holdout {hp.holdout_plan_id!r} event_assignments "
                    f"do not equal the bound event universe "
                    f"(missing={sorted(missing)}, "
                    f"extra={sorted(extra)})")
        # E10 — cascade atomicity, derived from bound labels.
        cascades: dict[str, list[str]] = {}
        for lbl in labels:
            if lbl.cascade_group_id:
                cascades.setdefault(lbl.cascade_group_id, []).append(
                    lbl.event_id)
        if cascades:
            # event → split name via group membership
            split_of_event: dict[str, str] = {}
            for hp in holdouts.values():
                for e, grp in hp.event_assignments.items():
                    for split_name, groups in (
                            ("train", hp.train_groups),
                            ("validation", hp.validation_groups),
                            ("test", hp.test_groups)):
                        if grp in groups:
                            split_of_event[e] = split_name
            problems.extend(cascade_atomicity_problems(
                split_of_event, cascades))

    # E18 — role coverage: statuses require specific artifact types.
    required_types = STATUS_REQUIRED_ARTIFACT_TYPES.get(status, set())
    missing_types = required_types - set(artifacts_by_type)
    if missing_types:
        problems.append(
            f"status {status!r} requires artifact types "
            f"{sorted(missing_types)} — none bound")

    # E14 — design-stage (blocker-tolerant) statuses may not carry
    # execution-shaped records: a draft envelope cannot smuggle a
    # forecast experiment or vintage.
    from .records import CutoffRecordV0
    if status in BLOCKER_TOLERANT_STATUSES:
        for name, record in records.items():
            if type(record) in (ForecastExperimentV0,
                                ForecastVintageV0):
                problems.append(
                    f"record {name!r}: {type(record).__name__} may not "
                    f"be bound under design-stage status {status!r}")

    execution = status in EXECUTION_STATUSES
    for name, record in records.items():
        if type(record) is EventLabelV0:
            # Labels must reference a bound source — unconditionally.
            if record.source_id not in sources:
                problems.append(
                    f"record {name!r}: source_id {record.source_id!r} "
                    "has no bound SourceRecordV0")
            elif execution and record.source_id not in verified:
                problems.append(
                    f"record {name!r}: source {record.source_id!r} is "
                    "bound but not EVIDENCE_VERIFIED — execution "
                    "statuses require verified sources (C15)")
            if verticals and record.vertical_id not in verticals:
                problems.append(
                    f"record {name!r}: vertical_id "
                    f"{record.vertical_id!r} not among bound verticals")
            if record.event_id in event_ids:
                problems.append(
                    f"record {name!r}: duplicate event_id "
                    f"{record.event_id!r}")
            event_ids.add(record.event_id)
            # Version binding (D07): the label's declared source
            # version must match the bound source record.
            src = sources.get(record.source_id)
            if src is not None and src.version and \
                    record.source_version != src.version:
                problems.append(
                    f"record {name!r}: source_version "
                    f"{record.source_version!r} != bound source "
                    f"version {src.version!r}")
            # Lineage fields must point at bound events (D08).
            for ref_name in ("parent_event_id", "duplicate_of"):
                ref = getattr(record, ref_name)
                if ref == record.event_id:
                    problems.append(
                        f"record {name!r}: {ref_name} self-references "
                        "its own event_id")
                elif ref and ref not in all_event_ids:
                    problems.append(
                        f"record {name!r}: {ref_name} {ref!r} has no "
                        "bound event label")
        elif type(record) is ControlWindowV0:
            opp = opportunities.get(record.opportunity_id)
            if opp is None:
                problems.append(
                    f"record {name!r}: opportunity_id "
                    f"{record.opportunity_id!r} has no bound "
                    "ObservationOpportunityV0")
            elif (record.window_start != opp.window_start or
                    record.window_end != opp.window_end):
                problems.append(
                    f"record {name!r}: control window does not equal "
                    "its opportunity window (C16)")
            elif record.opportunity_state != opp.state:
                problems.append(
                    f"record {name!r}: opportunity_state disagrees "
                    "with the bound opportunity record")
            else:
                # E11 — derived state is authoritative: the declared
                # control state must equal the recomputed state.
                derived = derive_control_state_typed(
                    record, opp, labels)
                if derived.value != record.state:
                    problems.append(
                        f"record {name!r}: declared state "
                        f"{record.state!r} != derived "
                        f"{derived.value!r} — control states are "
                        "computed, never asserted")
        elif type(record) is ObservationOpportunityV0:
            if record.source_id and record.source_id not in sources:
                problems.append(
                    f"record {name!r}: source_id {record.source_id!r} "
                    "has no bound SourceRecordV0")
        elif type(record) is ForecastExperimentV0:
            if record.holdout_plan_id not in holdouts:
                problems.append(
                    f"record {name!r}: holdout_plan_id "
                    f"{record.holdout_plan_id!r} has no bound "
                    "HoldoutPlanV0")
            if verticals and record.vertical_id not in verticals:
                problems.append(
                    f"record {name!r}: vertical_id "
                    f"{record.vertical_id!r} not among bound verticals")
            for digest in record.vintage_digests:
                if digest not in vintages:
                    problems.append(
                        f"record {name!r}: vintage_digest "
                        f"{digest[:16]}… not among bound vintages")
            # Role-bound digests (E04): feature digests must reference
            # feature_matrix artifacts, power must reference a
            # power_report — role reuse across types is rejected.
            for digest in record.feature_digests:
                if digest not in artifacts_by_type.get(
                        "feature_matrix", set()):
                    problems.append(
                        f"record {name!r}: feature_digest "
                        f"{digest[:16]}… is not a bound "
                        "feature_matrix artifact")
            if record.power_report_digest:
                if record.power_report_digest not in \
                        artifacts_by_type.get("power_report", set()):
                    problems.append(
                        f"record {name!r}: power_report_digest is not "
                        "a bound power_report artifact")
            # E08 — horizon must be admissible for every bound label's
            # measured precision class (optimistic zero-latency bound).
            from .policy import (EventTimeClass, _CLASS_HORIZON_ALLOWLIST,
                                 classify_event_time)
            for lbl in labels:
                measured = classify_event_time(lbl.uncertainty_seconds)
                if record.horizon not in \
                        _CLASS_HORIZON_ALLOWLIST[measured]:
                    problems.append(
                        f"record {name!r}: horizon {record.horizon!r} "
                        f"is inadmissible for event {lbl.event_id!r} "
                        f"(class {measured.value})")
        elif type(record) is HazardVerticalSpecV0:
            if record.pilot_gate_status == "PILOT_GATE_PASSED":
                for sid in record.candidate_source_ids:
                    if sid not in verified:
                        problems.append(
                            f"record {name!r}: PILOT_GATE_PASSED "
                            f"references {sid!r} which is not a bound "
                            "EVIDENCE_VERIFIED source")
        elif type(record) is RegimeArtifactV0:
            # Role-bound regime digests (E04/E19): each digest must
            # reference a bound artifact of the matching type.
            role_map = {
                "stability_report_digest": "stability_report",
                "k_selection_digest": "k_selection",
                "preprocessing_digest": "preprocessing",
                "null_model_digest": "null_model",
            }
            for field_name, artifact_type in role_map.items():
                digest = getattr(record, field_name)
                if isinstance(digest, str) and SHA256_RE.match(digest) \
                        and digest not in artifacts_by_type.get(
                            artifact_type, set()):
                    problems.append(
                        f"record {name!r}: {field_name} "
                        f"{digest[:16]}… is not a bound "
                        f"{artifact_type} artifact")
            for digest in record.source_digests:
                if digest not in artifacts:
                    problems.append(
                        f"record {name!r}: source_digest "
                        f"{digest[:16]}… not among bound "
                        "EvidenceArtifactV0 sha256 values")
        elif type(record) is CutoffRecordV0:
            # E20 — a vintage-bound cutoff must reference a bound
            # vintage, bound event, and bound source.
            if record.forecast_vintage_id and \
                    record.forecast_vintage_id not in vintage_ids:
                problems.append(
                    f"record {name!r}: forecast_vintage_id "
                    f"{record.forecast_vintage_id!r} has no bound "
                    "ForecastVintageV0")
            if record.event_id and \
                    record.event_id not in all_event_ids:
                problems.append(
                    f"record {name!r}: event_id {record.event_id!r} "
                    "has no bound EventLabelV0")
            if record.source_id and \
                    record.source_id not in sources:
                problems.append(
                    f"record {name!r}: source_id {record.source_id!r} "
                    "has no bound SourceRecordV0")
    # E20 — every bound vintage must have a bound cutoff.
    cutoff_vintages = {c.forecast_vintage_id
                       for c in _records_of(records, CutoffRecordV0)
                       if c.forecast_vintage_id}
    for vid in vintage_ids:
        if vid not in cutoff_vintages:
            problems.append(
                f"vintage {vid!r} has no bound CutoffRecordV0 — "
                "issue-time availability is unproven")
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
    problems.extend(_cross_record_problems(records, status))

    # Byte-bound evidence (C03/C19): execution statuses, verified
    # sources, and evidence artifacts all require a declared
    # evidence_root whose referenced files re-hash correctly.
    from .records import EvidenceArtifactV0
    needs_evidence = (
        status in EXECUTION_STATUSES
        or any(getattr(r, "posture", None) == "EVIDENCE_VERIFIED"
               for r in records.values())
        or any(type(r) is EvidenceArtifactV0
               for r in records.values()))
    evidence_root = approval.get("evidence_root")
    if needs_evidence:
        if not evidence_root:
            problems.append("execution statuses and byte-bound records "
                            "require approval 'evidence_root'")
        else:
            # evidence_root must live inside artifact_root — the
            # evidence directory is part of the audited artifact set
            # (D02).
            ev_root = Path(str(evidence_root)).resolve()
            art_root = Path(str(approval["artifact_root"])).resolve()
            if ev_root != art_root and not _contained(ev_root,
                                                    art_root):
                problems.append(
                    "evidence_root must resolve inside artifact_root")
            else:
                from .records import ForecastVintageV0
                for name, record in records.items():
                    problems.extend(
                        f"record {name!r}: {p}" for p in
                        source_evidence_problems(
                            record, evidence_root=evidence_root))
                    if type(record) is EvidenceArtifactV0:
                        problems.extend(
                            f"record {name!r}: {p}" for p in
                            _evidence_artifact_problems(
                                record, evidence_root))
                    elif type(record) is ForecastVintageV0:
                        # E05 — archive payload and retrieval record
                        # are real files under evidence_root whose
                        # bytes must match the declared digests.
                        for path_field, digest_field in (
                                ("archive_payload_path",
                                 "archive_payload_sha256"),
                                ("retrieval_record_path",
                                 "retrieval_record_sha256")):
                            fpath = _resolve_against(
                                getattr(record, path_field),
                                Path(str(evidence_root)))
                            try:
                                meta = hash_artifact(
                                    fpath, Path(str(evidence_root)))
                            except ValueError as exc:
                                problems.append(
                                    f"record {name!r}: {exc}")
                                continue
                            if meta["sha256"] != getattr(
                                    record, digest_field):
                                problems.append(
                                    f"record {name!r}: {digest_field} "
                                    "does not match file bytes")
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
    out["approver_role"] = str(approval["approver_role"])
    out["approver_attestation"] = str(approval["approver_attestation"])
    out["approved_at"] = str(approval["approved_at"])
    out["approval_scope"] = str(approval["approval_scope"])
    out["artifact_root"] = str(
        Path(str(approval["artifact_root"])).resolve())
    # E15 — the envelope carries canonical paths to the artifacts it
    # binds; CLI verification must resolve to these exact files.
    out["matrix_path"] = str(Path(str(
        approval["matrix_path"])).resolve())
    out["policy_path"] = str(Path(str(
        approval["policy_path"])).resolve())
    out["record_types"] = {n: type(r).__name__
                           for n, r in records.items()}
    if evidence_root:
        out["evidence_root"] = str(
            Path(str(evidence_root)).resolve())
    out["source_review_date"] = str(approval["source_review_date"])
    out["selected_pilot_rule"] = str(approval["selected_pilot_rule"])
    out["unresolved_blockers"] = blockers
    out["envelope_sha256"] = sha256_canonical(out)
    return out


def _evidence_artifact_problems(record: Any, evidence_root: Any) -> list[str]:
    """Verify an EvidenceArtifactV0 against actual bytes under
    ``evidence_root`` — path containment, non-symlink, sha256 and
    declared size must match."""
    problems: list[str] = []
    root = Path(str(evidence_root))
    if not root.is_dir():
        return [f"evidence_root {root} is not a directory"]
    path = _resolve_against(record.path, root)
    try:
        meta = hash_artifact(path, root)
    except ValueError as exc:
        return [f"artifact {record.artifact_id!r}: {exc}"]
    if meta["sha256"] != record.sha256:
        problems.append(
            f"artifact {record.artifact_id!r}: sha256 mismatch — "
            f"declared {str(record.sha256)[:16]}… != file "
            f"{meta['sha256'][:16]}…")
    if meta["size_bytes"] != record.size_bytes:
        problems.append(
            f"artifact {record.artifact_id!r}: size_bytes {meta['size_bytes']}"
            f" != declared {record.size_bytes}")
    return problems


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
    path = _resolve_against(raw, root)
    if not _contained(path, root):
        problems.append(f"evidence sidecar {path} resolves outside "
                        "evidence_root")
    # R11.2-2 — read the sidecar through the shared pinned reader:
    # the component walk rejects leaf AND intermediate symlinks
    # beneath the root, and the (inode,size,mtime) signature pins the
    # bytes so the digest and the parsed JSON are the same bytes.
    # The relpath handed to the walker must be LEXICAL — resolving
    # first would collapse a symlink alias before it can be seen.
    rel = None
    try:
        rel = str(path.relative_to(root))
    except ValueError:
        try:
            rel = str(path.absolute().relative_to(
                root.absolute()))
        except ValueError:
            pass
    if rel is None or ".." in Path(rel).parts:
        problems.append(
            f"evidence sidecar {path} does not lie lexically "
            "inside evidence_root — a path that only resolves "
            "inside via a symlink alias is inadmissible")
        return problems
    try:
        data = read_evidence_file(root.resolve(), rel,
                                  label="evidence sidecar")
    except ValueError as exc:
        problems.append(str(exc))
        return problems
    actual = hashlib.sha256(data).hexdigest()
    declared = getattr(record, "evidence_sidecar_sha256", "")
    if actual != declared:
        problems.append(
            f"evidence sidecar digest mismatch: declared "
            f"{str(declared)[:16]}… != file bytes {actual[:16]}…")
        return problems
    # E06 — byte integrity alone is not evidence: the sidecar must be
    # a JSON object binding this exact source/version with license,
    # coverage, timing, reviewer, and decision fields.
    import json as _json
    try:
        sidecar = _json.loads(data.decode("utf-8"))
    except (OSError, _json.JSONDecodeError, UnicodeDecodeError) as exc:
        problems.append(f"evidence sidecar is not parseable JSON: {exc}")
        return problems
    if not isinstance(sidecar, dict):
        problems.append("evidence sidecar must be a JSON object")
        return problems
    for key in ("source_id", "source_version", "license_id",
                "coverage", "timing_review", "reviewer_ids",
                "review_date", "decision"):
        if key not in sidecar or sidecar[key] in (None, "", []):
            problems.append(f"sidecar missing required field {key!r}")
    if sidecar.get("source_id") and \
            sidecar["source_id"] != getattr(record, "source_id", None):
        problems.append("sidecar source_id does not match the record")
    if sidecar.get("source_version") and \
            sidecar["source_version"] != getattr(record, "version", None):
        problems.append("sidecar source_version does not match the "
                        "record version")
    if sidecar.get("decision") not in (None, "VERIFIED"):
        problems.append("sidecar decision must be VERIFIED for an "
                        "EVIDENCE_VERIFIED source")
    if sidecar.get("license_id") and \
            sidecar["license_id"] != getattr(record, "license_id", ""):
        problems.append("sidecar license_id does not match the record")
    review_date = sidecar.get("review_date")
    if isinstance(review_date, str) and not _valid_calendar_date(
            review_date):
        problems.append("sidecar review_date is not a real calendar "
                        "date")
    return problems


# ---------------------------------------------------------------------
# Claim scan (B18/B36): normalized structural scanning over JSON,
# Markdown, manifests, and text.
# ---------------------------------------------------------------------

FORBIDDEN_STATUS_TOKENS = frozenset({
    "READY", "FMX_READY", "B_TO_C_READY", "WARNING_READY",
    "PRODUCTION_READY", "SCIENTIFICALLY_VALIDATED", "AUTHORITY_APPROVED",
    "OPERATIONALLY_AUTHORIZED", "PILOT_READY", "FORECAST_READY",
    "PILOT_QUALIFIED", "ELIGIBLE", "APPROVED", "VALIDATED_OPERATIONALLY",
    "PILOT_SELECTED", "PILOT_GATE_PASSED", "FORECAST_SKILL_DEMONSTRATED"})

# Flag scan tolerates JSON double quotes, single quotes, escaped
# quotes, YAML unquoted keys, `=` separators, YAML booleans, and
# numeric truthy values (C21/E16).
_AUTHORITY_FLAG_RE = re.compile(
    r"['\"\\]?"
    r"(?:warning[_\-\s]*path[_\-\s]*authorized|"
    r"production[_\-\s]*authorized|promotion[_\-\s]*eligible|"
    r"operationally[_\-\s]*authorized|authority[_\-\s]*approved)"
    r"['\"\\]?\s*[:=]\s*['\"\\]*(?:true|yes|on|1)\b",
    re.IGNORECASE)

_OPERATIONAL_PHRASE_RE = re.compile(
    r"\b(operational\W+warning|evacuation|production\W+deploy"
    r"|warning\W+threshold|alert\W+level|scientifically\W+validated|"
    r"authority\W+approved|issue\W+a\W+warning|warning\W+issued)\b",
    re.IGNORECASE)

# Captures the full raw value after a status-like key — quoted string,
# bare word, or bracketed array (E16).
_STATUS_FIELD_RE = re.compile(
    r"['\"\\]*(?:status|gate[_\-\s]*status|readiness)['\"\\]*"
    r"\s*[:=]\s*([^\n]{0,200})",
    re.IGNORECASE)

_UNICODE_ESCAPE_RE = re.compile(r"\\u([0-9a-fA-F]{4})")
_VALUE_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]+")


def _normalize_token(text: str) -> str:
    return re.sub(r"[\s\-]+", "_", text.strip()).upper()


def _decode_escapes(text: str) -> str:
    """Decode \\uXXXX escapes and escaped quotes so obfuscated claim
    text cannot slip past the scanner (E16)."""
    decoded = _UNICODE_ESCAPE_RE.sub(
        lambda m: chr(int(m.group(1), 16)), text)
    return decoded.replace('\\"', '"').replace("\\'", "'")


def scan_claims_text(text: str) -> list[str]:
    """Scan raw text/JSON/Markdown for forbidden claim content.

    Casefolded and separator-normalized; scans the raw text and a
    unicode-unescaped variant; status keys accept quoted, bare, and
    array values with every member checked; truthy authority flags
    accept true/yes/on/1.  ``B_TO_C_BLOCKED`` (factual blocked status)
    is allowed.  Returns findings; empty means clean.
    """
    findings: list[str] = []
    for variant in (text, _decode_escapes(text)):
        for match in _STATUS_FIELD_RE.finditer(variant):
            for piece in _VALUE_TOKEN_RE.findall(match.group(1)):
                if _normalize_token(piece) in FORBIDDEN_STATUS_TOKENS:
                    findings.append(
                        f"forbidden status value {piece!r}")
        for match in _AUTHORITY_FLAG_RE.finditer(variant):
            findings.append(
                f"truthy authority flag: {match.group(0)}")
        for match in _OPERATIONAL_PHRASE_RE.finditer(variant):
            findings.append(
                f"operational phrase: {match.group(0)!r}")
    return sorted(set(findings))


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
        if len(set(event_ids)) != len(event_ids):
            problems.append(
                f"cascade group {group_id!r} contains duplicate "
                "member IDs")
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
