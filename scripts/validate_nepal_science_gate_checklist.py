"""Fail-closed validator for Nepal-derived safeguards applied to India Phase 0."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHECKLIST = ROOT / "docs/science/NEPAL_SCIENCE_GATE_CHECKLIST_V1.json"
SCHEMA = "NEPAL_SCIENCE_GATE_CHECKLIST_V1"
AUTHORITY_FLAGS = {
    "bulk_acquisition_authorized": False,
    "weather_download_authorized": False,
    "satellite_bulk_authorized": False,
    "seismic_waveform_authorized": False,
    "forecast_authorized": False,
    "warning_authorized": False,
    "detector_authorized": False,
    "odds_authorized": False,
    "causal_authorized": False,
    "operational_authorized": False,
}
REQUIRED_INVARIANTS = {
    "GATE_PIPELINE_ORDER", "GATE_BYTE_BOUND_PROVENANCE",
    "GATE_NO_BULK_BEFORE_BOUNDED", "GATE_PRESERVE_NEGATIVE",
    "GATE_CLAIM_SEPARATION", "GATE_NO_NEPAL_PRIOR_TRANSFER",
}
REQUIRED_PHASE0_CONDITIONS = {
    "PINNED_SOURCE_VERSION_AND_TERMS",
    "EVENT_ROWS_RETAINED_AND_ADJUDICATION_COMPLETE",
    "CANONICAL_LAKE_FRAME_AND_TERRITORY_RESOLVED",
    "OBSERVATION_COVERAGE_AND_MISSINGNESS_REPORTED",
    "VERIFIED_NON_EVENT_STATUS_NOT_INFERRED_FROM_ABSENCE",
}
REQUIRED_DEFERRED = {
    "GATE_HUMAN_ADJUDICATION",
    "GATE_LEAKAGE_AUDIT",
    "GATE_CONTROLS_PACKAGE",
    "GATE_TEMPORAL_GEOGRAPHIC_HOLDOUT",
    "GATE_STABILITY_NULLS",
    "GATE_FORECAST_VINTAGES",
}
_HEX64 = re.compile(r"[0-9a-f]{64}").fullmatch


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_checklist(document: object) -> list[str]:
    problems: list[str] = []
    if not isinstance(document, dict):
        return ["checklist root must be an object"]
    if document.get("schema") != SCHEMA or document.get("version") != 1:
        problems.append("schema/version must be NEPAL_SCIENCE_GATE_CHECKLIST_V1/1")
    if document.get("scope") != "INDIA_PHASE0":
        problems.append("scope must be INDIA_PHASE0")
    if document.get("claim_scope") != "research_only_no_operational_authorization":
        problems.append("claim_scope must remain research-only")
    if document.get("authority") != AUTHORITY_FLAGS:
        problems.append("all authority flags must be present and false")

    invariants = document.get("policy_invariants")
    if not isinstance(invariants, list):
        problems.append("policy_invariants must be a list")
        invariants = []
    ids: list[str] = []
    for i, item in enumerate(invariants):
        if not isinstance(item, dict):
            problems.append(f"policy_invariants[{i}] must be an object")
            continue
        gate_id = item.get("id")
        if not isinstance(gate_id, str):
            problems.append(f"policy_invariants[{i}].id must be a string")
            continue
        ids.append(gate_id)
        if item.get("status") != "ENFORCED":
            problems.append(f"{gate_id} must remain ENFORCED")
        if not isinstance(item.get("rule"), str) or not item["rule"].strip():
            problems.append(f"{gate_id} requires a non-empty rule")
    if set(ids) != REQUIRED_INVARIANTS or len(ids) != len(REQUIRED_INVARIANTS):
        problems.append("policy invariant IDs must exactly match the required set")

    conditions = document.get("phase0_required_conditions")
    if not isinstance(conditions, list):
        problems.append("phase0_required_conditions must be a list")
        conditions = []
    condition_ids = []
    for i, item in enumerate(conditions):
        if not isinstance(item, dict):
            problems.append(f"phase0_required_conditions[{i}] must be an object")
            continue
        item_id = item.get("id")
        if isinstance(item_id, str):
            condition_ids.append(item_id)
        else:
            problems.append(f"phase0_required_conditions[{i}].id must be a string")
        if item.get("required_before_decision_ready") is not True:
            problems.append("every Phase-0 condition must be required before decision readiness")
    if set(condition_ids) != REQUIRED_PHASE0_CONDITIONS or len(condition_ids) != len(
            REQUIRED_PHASE0_CONDITIONS):
        problems.append("Phase-0 required-condition IDs must exactly match the required set")

    deferred = document.get("deferred_science_gates")
    if not isinstance(deferred, list):
        problems.append("deferred_science_gates must be a list")
        deferred = []
    deferred_ids = []
    for i, item in enumerate(deferred):
        if not isinstance(item, dict):
            problems.append(f"deferred_science_gates[{i}] must be an object")
            continue
        item_id = item.get("id")
        if isinstance(item_id, str):
            deferred_ids.append(item_id)
        else:
            problems.append(f"deferred_science_gates[{i}].id must be a string")
        if item.get("status") not in {
                "DEFERRED_TO_PHASE0_EVIDENCE_REVIEW",
                "DEFERRED_UNTIL_A_SCIENCE_PROTOCOL_EXISTS",
                "DEFERRED_UNTIL_OBSERVATION_EVIDENCE_EXISTS",
                "NOT_APPLICABLE_TO_PHASE0"}:
            problems.append(f"deferred gate {item_id!r} has invalid status")
    if set(deferred_ids) != REQUIRED_DEFERRED or len(deferred_ids) != len(REQUIRED_DEFERRED):
        problems.append("deferred science gate IDs must exactly match the required set")
    return problems


def load_verified_checklist(path: Path = DEFAULT_CHECKLIST) -> tuple[dict[str, Any], str]:
    path = Path(path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    problems = validate_checklist(doc)
    sidecar = Path(str(path) + ".sha256")
    if not sidecar.is_file():
        problems.append("checklist SHA-256 sidecar is missing")
    else:
        fields = sidecar.read_text(encoding="utf-8").split()
        if (not fields or fields[0] != sha256_file(path)
                or (len(fields) > 1 and fields[1] != path.name)):
            problems.append("checklist SHA-256 sidecar does not match bytes")
    if problems:
        raise ValueError("science-gate checklist invalid: " + "; ".join(problems))
    return doc, sha256_file(path)


def phase0_readiness(denominators: dict[str, int]) -> dict[str, Any]:
    """Report readiness for owner review without conflating it with authority."""
    if not isinstance(denominators, dict):
        return {
            "status": "BLOCKED",
            "blocking_reasons": ["INVALID_DENOMINATOR_OBJECT"],
            "required_condition_status": {
                condition: "UNRESOLVED"
                for condition in sorted(REQUIRED_PHASE0_CONDITIONS)},
            "meaning": "Phase-0 feasibility decision readiness only; never acquisition or operational authority.",
        }
    blockers: list[str] = []
    keys = (
        "catalog_rows", "mapped_lake_rows", "verified_source_bytes",
        "unreviewed_event_rows", "eligible_unverified_evidence",
        "unverified_control_candidates", "uncertain_territory_lakes",
        "unresolved_lake_identities", "in_country_canonical_lakes",
        "observable_lake_years", "observation_unknown_lake_rows",
        "observation_partial_lake_rows", "observation_known_breach_lake_rows",
        "observation_verified_non_event_lake_rows",
    )
    counts: dict[str, int] = {}
    for key in keys:
        value = denominators.get(key, 0)
        if type(value) is not int or value < 0:
            blockers.append(f"INVALID_DENOMINATOR:{key}")
            counts[key] = 0
        else:
            counts[key] = value
    catalog_rows = counts["catalog_rows"]
    lake_rows = counts["mapped_lake_rows"]
    observed_state_rows = sum(counts.get(key, 0) for key in (
        "observation_unknown_lake_rows", "observation_partial_lake_rows",
        "observation_known_breach_lake_rows",
        "observation_verified_non_event_lake_rows"))
    observation_partitioned = observed_state_rows == lake_rows
    conditions = {
        "PINNED_SOURCE_VERSION_AND_TERMS":
            counts["verified_source_bytes"] > 0,
        "EVENT_ROWS_RETAINED_AND_ADJUDICATION_COMPLETE":
            catalog_rows > 0
            and counts["unreviewed_event_rows"] == 0
            and counts["eligible_unverified_evidence"] == 0,
        "CANONICAL_LAKE_FRAME_AND_TERRITORY_RESOLVED":
            counts["in_country_canonical_lakes"] > 0
            and counts["uncertain_territory_lakes"] == 0
            and counts["unresolved_lake_identities"] == 0,
        "OBSERVATION_COVERAGE_AND_MISSINGNESS_REPORTED":
            observation_partitioned
            and counts["observable_lake_years"] > 0,
        # Zero verified controls is a reportable feasibility result. The
        # gate here is that control labels are explicit and evidence-backed,
        # never inferred from missing catalogue rows.
        "VERIFIED_NON_EVENT_STATUS_NOT_INFERRED_FROM_ABSENCE":
            observation_partitioned
            and counts["unverified_control_candidates"] == 0,
    }
    if catalog_rows == 0:
        blockers.append("NO_EVENT_CROSSWALK_ROWS")
    if lake_rows == 0:
        blockers.append("NO_LAKE_FRAME_ROWS")
    if not observation_partitioned:
        blockers.append("OBSERVATION_STATUS_DENOMINATOR_MISMATCH")
    if counts["verified_source_bytes"] == 0:
        blockers.append("NO_PINNED_SOURCE_BYTES")
    if counts["unreviewed_event_rows"] > 0:
        blockers.append("EVENT_ADJUDICATION_INCOMPLETE")
    if counts["eligible_unverified_evidence"] > 0:
        blockers.append("EVENT_EVIDENCE_UNVERIFIED")
    if counts["unverified_control_candidates"] > 0:
        blockers.append("CONTROL_EVIDENCE_UNVERIFIED")
    if counts["uncertain_territory_lakes"] > 0:
        blockers.append("TERRITORY_UNRESOLVED")
    if counts["unresolved_lake_identities"] > 0:
        blockers.append("LAKE_IDENTITY_UNRESOLVED")
    if counts["observable_lake_years"] == 0:
        blockers.append("OBSERVATION_COVERAGE_NOT_ESTABLISHED")
    if not conditions["CANONICAL_LAKE_FRAME_AND_TERRITORY_RESOLVED"]:
        blockers.append("CANONICAL_LAKE_FRAME_OR_TERRITORY_UNRESOLVED")
    return {
        "status": "BLOCKED" if blockers else "READY_FOR_OWNER_REVIEW",
        "blocking_reasons": blockers,
        "required_condition_status": {
            condition: "SATISFIED" if satisfied else "UNRESOLVED"
            for condition, satisfied in conditions.items()},
        "meaning": "Phase-0 feasibility decision readiness only; never acquisition or operational authority.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checklist", type=Path, default=DEFAULT_CHECKLIST)
    args = parser.parse_args(argv)
    try:
        doc, digest = load_verified_checklist(args.checklist)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"SCIENCE_GATE_CHECKLIST_BLOCKED: {exc}")
        return 1
    print(json.dumps({"status": "SCIENCE_GATE_POLICY_OK",
                      "schema": doc["schema"], "sha256": digest,
                      "authority": doc["authority"]}, indent=2,
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
