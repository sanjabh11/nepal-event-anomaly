#!/usr/bin/env python3
"""Validate the byte-bound model re-execution proof record.

This validator is intentionally independent of the re-execution runner.  It
checks the published proof boundary: schema and status vocabulary, logical
paths, digest-equality semantics, gate-map shape, fidelity classification,
and the all-false authority surface.  It does not execute a model, read an
evidence root, or rewrite the report.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import PurePosixPath
from pathlib import Path
from typing import Any, Mapping


SCHEMA = "P5_MODEL_REEXECUTION_PROOF_V0"
OVERALL_STATUSES = frozenset({
    "MODEL_REEXECUTED",
    "MODEL_REEXECUTION_MISMATCH",
    "MODEL_REEXECUTION_PARTIAL",
    "MODEL_REEXECUTION_UNAVAILABLE",
})
LANE_VERDICTS = frozenset({"REPRODUCED", "MISMATCH", "PARTIAL", "UNAVAILABLE"})
FIDELITIES = frozenset({"exact", "roundtrip_drift", "mismatch"})
LANES = ("daily", "seasonal")
AUTHORITY_FIELDS = (
    "operational_claim",
    "production_authorized",
    "promotion_eligible",
    "warning_path_authorized",
)
REQUIRED_GATES = frozenset({
    "seed_policy",
    "modal_k_unanimous",
    "seed_ari",
    "seed_coverage",
    "loro",
    "temporal_bootstrap",
    "season_refits",
    "elevation",
    "missingness",
    "effort",
    "era_drift",
    "shuffled_null",
    "season_matched_null",
})
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _is_sha(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def _is_safe_relpath(value: Any) -> bool:
    if not isinstance(value, str) or not value or value.startswith("/"):
        return False
    if "\\" in value:
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and all(part not in {"", ".", ".."} for part in path.parts)
        and path.as_posix() == value
    )


def _is_utc(value: Any) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.utcoffset() is not None


def _mapping(value: Any, label: str, problems: list[str]) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        problems.append(f"{label} must be an object")
        return None
    return value


def _required(mapping: Mapping[str, Any], names: tuple[str, ...], label: str,
              problems: list[str]) -> None:
    for name in names:
        if name not in mapping:
            problems.append(f"{label}.{name} is missing")


def _validate_gate_map(value: Any, label: str, problems: list[str]) -> None:
    gates = _mapping(value, label, problems)
    if gates is None:
        return
    if set(gates) != set(REQUIRED_GATES):
        problems.append(
            f"{label} must contain exactly the declared 13 regime gates")
    for name, result in gates.items():
        if not isinstance(result, bool):
            problems.append(f"{label}.{name} must be boolean")


def _validate_digest_fields(value: Mapping[str, Any], label: str,
                            problems: list[str]) -> None:
    for name in (
        "config_digest",
        "environment_digest",
        "freeze_digest",
        "regime_artifact_digest",
        "train_mask_digest",
    ):
        if name in value and not _is_sha(value[name]):
            problems.append(f"{label}.{name} must be a SHA-256 digest")


def _validate_recorded_or_recomputed(
    value: Any,
    label: str,
    problems: list[str],
) -> Mapping[str, Any] | None:
    record = _mapping(value, label, problems)
    if record is None:
        return None
    _validate_digest_fields(record, label, problems)
    if "status" not in record or not isinstance(record.get("status"), str):
        problems.append(f"{label}.status must be a non-empty string")
    elif not record["status"].strip():
        problems.append(f"{label}.status must be a non-empty string")
    if "associable" in record and record["associable"] is not False:
        problems.append(f"{label}.associable must remain false")
    if "required_gates" in record:
        _validate_gate_map(record["required_gates"], f"{label}.required_gates",
                           problems)
    return record


def _validate_lane(
    name: str,
    lane: Any,
    root_ids: Mapping[str, Any],
    overall_status: str,
    problems: list[str],
) -> None:
    label = f"lanes.{name}"
    record = _mapping(lane, label, problems)
    if record is None:
        return
    _required(record, (
        "lane", "artifact", "artifact_sha256", "frame", "frame_sha256",
        "checks", "differences", "recomputed", "recorded", "root_id",
        "sidecars", "verdict",
    ), label, problems)
    if record.get("lane") != name:
        problems.append(f"{label}.lane must equal {name!r}")
    for path_name in ("artifact", "frame"):
        if path_name in record and not _is_safe_relpath(record[path_name]):
            problems.append(f"{label}.{path_name} must be a logical relative path")
    for digest_name in ("artifact_sha256", "frame_sha256"):
        if digest_name in record and not _is_sha(record[digest_name]):
            problems.append(f"{label}.{digest_name} must be a SHA-256 digest")
    root_id = record.get("root_id")
    if not isinstance(root_id, str) or not root_id.strip():
        problems.append(f"{label}.root_id must be non-empty")
    elif root_ids.get(name) != root_id:
        problems.append(f"{label}.root_id must match root_ids.{name}")

    sidecars = _mapping(record.get("sidecars"), f"{label}.sidecars", problems)
    if sidecars is not None:
        if not sidecars:
            problems.append(f"{label}.sidecars must not be empty")
        for sidecar_name, present in sidecars.items():
            if not isinstance(present, bool):
                problems.append(f"{label}.sidecars.{sidecar_name} must be boolean")
        if record.get("verdict") == "REPRODUCED" and not all(sidecars.values()):
            problems.append(f"{label}.sidecars must all be true for REPRODUCED")

    verdict = record.get("verdict")
    if verdict not in LANE_VERDICTS:
        problems.append(f"{label}.verdict is not an allowed lane verdict")

    checks = _mapping(record.get("checks"), f"{label}.checks", problems)
    recorded = _validate_recorded_or_recomputed(
        record.get("recorded"), f"{label}.recorded", problems)
    raw_recomputed = record.get("recomputed")
    if verdict == "UNAVAILABLE" and raw_recomputed == {}:
        # v1 deliberately records that no model output was available for the
        # lane.  An empty recomputed object is valid historical lineage; it
        # must not be mistaken for a completed re-execution.
        recomputed: Mapping[str, Any] | None = {}
    else:
        recomputed = _validate_recorded_or_recomputed(
            raw_recomputed, f"{label}.recomputed", problems)

    differences = _mapping(record.get("differences"), f"{label}.differences",
                           problems)
    if differences is not None:
        n_fields = differences.get("n_differing_fields")
        if n_fields is not None and (
            not isinstance(n_fields, int) or isinstance(n_fields, bool) or n_fields < 0
        ):
            problems.append(f"{label}.differences.n_differing_fields must be a non-negative integer")
        for key in ("semantic", "volatile"):
            if key in differences and not isinstance(differences[key], list):
                problems.append(f"{label}.differences.{key} must be a list")

    lane_problems = record.get("problems")
    if lane_problems is not None and not isinstance(lane_problems, list):
        problems.append(f"{label}.problems must be a list")

    if checks is not None:
        required_checks = (
            "authority_flags_all_false_recomputed",
            "config_digest_match",
            "environment_digest_match",
            "freeze_ok",
            "recorded_envelope_digest_match",
            "recorded_freeze_digest_match",
            "reconstructed_config_digest_match",
            "source_manifest_byte_verified",
            "status_match",
            "train_mask_digest_match",
        )
        if verdict == "REPRODUCED":
            for check_name in required_checks:
                if checks.get(check_name) is not True:
                    problems.append(f"{label}.checks.{check_name} must be true for REPRODUCED")
            if ("manifest_substitution_verified" in checks and
                    checks["manifest_substitution_verified"] is not True):
                problems.append(
                    f"{label}.checks.manifest_substitution_verified must be true when present"
                )
            gate_equality = checks.get("required_gates_equality")
            _validate_gate_map(gate_equality, f"{label}.checks.required_gates_equality",
                               problems)
            fidelity = checks.get("input_fidelity")
            legacy_unavailable = (
                fidelity is None and
                overall_status == "MODEL_REEXECUTION_UNAVAILABLE"
            )
            if fidelity not in {"exact", "roundtrip_drift"} and not legacy_unavailable:
                problems.append(
                    f"{label}.checks.input_fidelity must be exact or roundtrip_drift"
                )
            if fidelity == "roundtrip_drift" and not isinstance(
                    checks.get("input_fidelity_note"), str):
                problems.append(
                    f"{label}.checks.input_fidelity_note is required for roundtrip_drift"
                )
        elif "input_fidelity" in checks and checks["input_fidelity"] not in FIDELITIES:
            problems.append(f"{label}.checks.input_fidelity is not an allowed fidelity")

    if verdict == "REPRODUCED":
        if not isinstance(recomputed, Mapping) or not recomputed:
            problems.append(f"{label}.recomputed must be populated for REPRODUCED")
        if isinstance(differences, Mapping):
            if differences.get("n_differing_fields") != 0:
                problems.append(f"{label}.differences must report zero differing fields")
            for key in ("semantic", "volatile"):
                if differences.get(key) != []:
                    problems.append(f"{label}.differences.{key} must be empty for REPRODUCED")
        if lane_problems not in (None, []):
            problems.append(f"{label}.problems must be empty for REPRODUCED")
        if isinstance(recorded, Mapping) and isinstance(recomputed, Mapping):
            for digest_name in (
                "config_digest", "environment_digest", "freeze_digest",
                "regime_artifact_digest", "train_mask_digest",
            ):
                if recorded.get(digest_name) != recomputed.get(digest_name):
                    problems.append(
                        f"{label}.{digest_name} differs between recorded and recomputed"
                    )
            if recorded.get("status") != recomputed.get("status"):
                problems.append(f"{label}.status differs between recorded and recomputed")
            if recorded.get("required_gates") != recomputed.get("required_gates"):
                problems.append(f"{label}.required_gates differs between recorded and recomputed")
    elif overall_status == "MODEL_REEXECUTED":
        problems.append(
            f"{label}.verdict {verdict!r} cannot accompany MODEL_REEXECUTED"
        )


def validate_report(document: Any) -> list[str]:
    """Return fail-closed validation problems for a proof report."""

    problems: list[str] = []
    report = _mapping(document, "report", problems)
    if report is None:
        return problems
    _required(report, (
        "schema", "status", "proof_scope", "root_ids", "lanes", "authority",
        "claim_scope", "activity_id", "started_utc", "completed_utc",
        "execution_context",
    ), "report", problems)
    if report.get("schema") != SCHEMA:
        problems.append(f"report.schema must be {SCHEMA!r}")
    overall_status = report.get("status")
    if overall_status not in OVERALL_STATUSES:
        problems.append("report.status is not an allowed model-reexecution status")
        overall_status = "MODEL_REEXECUTION_MISMATCH"
    if report.get("proof_scope") != "model_reexecution":
        problems.append("report.proof_scope must be 'model_reexecution'")
    if report.get("claim_scope") != "research_only_no_operational_authorization":
        problems.append("report.claim_scope exceeds the research-only boundary")
    if not isinstance(report.get("activity_id"), str) or not report["activity_id"].strip():
        problems.append("report.activity_id must be non-empty")
    if not _is_utc(report.get("started_utc")):
        problems.append("report.started_utc must be an explicit UTC timestamp")
    if not _is_utc(report.get("completed_utc")):
        problems.append("report.completed_utc must be an explicit UTC timestamp")
    if _is_utc(report.get("started_utc")) and _is_utc(report.get("completed_utc")):
        start = datetime.fromisoformat(report["started_utc"][:-1] + "+00:00")
        end = datetime.fromisoformat(report["completed_utc"][:-1] + "+00:00")
        if end < start:
            problems.append("report.completed_utc precedes started_utc")

    authority = _mapping(report.get("authority"), "report.authority", problems)
    if authority is not None:
        if set(authority) != set(AUTHORITY_FIELDS):
            problems.append("report.authority must contain exactly the four authority flags")
        for name in AUTHORITY_FIELDS:
            if authority.get(name) is not False:
                problems.append(f"report.authority.{name} must be exactly false")

    root_ids = _mapping(report.get("root_ids"), "report.root_ids", problems)
    if root_ids is not None:
        if set(root_ids) != set(LANES):
            problems.append("report.root_ids must contain daily and seasonal only")
        for name in LANES:
            if not isinstance(root_ids.get(name), str) or not root_ids.get(name, "").strip():
                problems.append(f"report.root_ids.{name} must be non-empty")

    context = _mapping(report.get("execution_context"), "report.execution_context", problems)
    if context is not None:
        note = context.get("note")
        if note is not None and not isinstance(note, str):
            problems.append("report.execution_context.note must be text")
        for key, value in context.items():
            if key.startswith("host_") and not isinstance(value, str):
                problems.append(f"report.execution_context.{key} must be text")

    lanes = _mapping(report.get("lanes"), "report.lanes", problems)
    if lanes is not None:
        if set(lanes) != set(LANES):
            problems.append("report.lanes must contain daily and seasonal only")
        if root_ids is not None:
            for name in LANES:
                if name in lanes:
                    _validate_lane(name, lanes[name], root_ids, overall_status, problems)

    if overall_status == "MODEL_REEXECUTED" and isinstance(lanes, Mapping):
        if any(lanes.get(name, {}).get("verdict") != "REPRODUCED" for name in LANES
               if isinstance(lanes.get(name), Mapping)):
            problems.append("MODEL_REEXECUTED requires REPRODUCED verdicts for both lanes")
    return problems


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate a P5 model-reexecution proof without executing a model.")
    parser.add_argument("report", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        document = json.loads(args.report.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(json.dumps({
            "status": "REEXECUTION_REPORT_FAIL",
            "problems": [f"cannot read JSON report: {exc}"],
        }, sort_keys=True))
        return 2
    problems = validate_report(document)
    status = "REEXECUTION_REPORT_OK" if not problems else "REEXECUTION_REPORT_FAIL"
    print(json.dumps({
        "status": status,
        "path": str(args.report),
        "problems": problems,
    }, indent=2, sort_keys=True))
    return 0 if not problems else 2


if __name__ == "__main__":
    raise SystemExit(main())
