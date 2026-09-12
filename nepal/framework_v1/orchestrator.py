"""Authenticated, resumable boundaries for the framework v1 pipeline.

This module deliberately contains orchestration evidence only.  It does not
make a scientific decision and it does not turn a stage status into authority.
The CLI can therefore persist a report or checkpoint without allowing a
partially written file to masquerade as a completed pipeline.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from . import contract as C
from .provenance import (bind_artifact_envelope, canonical_json,
                         sha256_file, verify_artifact_envelope,
                         write_deterministic_json)

PIPELINE_PROFILE_ID = "FRAMEWORK_V1_FULL"
PIPELINE_STATUS_COMPLETE = "PIPELINE_COMPLETE"
PIPELINE_STATUS_BLOCKED = "PIPELINE_BLOCKED"
PIPELINE_STATUS_INCOMPLETE = "PIPELINE_INCOMPLETE"
RESUMABLE_PIPELINE_STATES = frozenset({"RUNNING", "INCOMPLETE"})

NO_CLAIMS = (
    "Framework implementation evidence is not scientific validation",
    "No warning, production, or authority approval is established",
    "C and D remain intentionally blocked/not run under framework v1",
)


def pipeline_input_fingerprint(
    paths: Iterable[str | Path],
    *,
    values: Optional[Mapping[str, Any]] = None,
) -> str:
    """Hash the immutable pipeline inputs used for safe checkpoint replay.

    Files are streamed through the existing SHA-256 helper.  Directories are
    represented by their resolved path only; the canonical manifest and its
    own artifact hashes remain the authority for a data root, avoiding a
    second bulk traversal of a large handoff on every checkpoint.
    """
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_path in paths:
        path = Path(raw_path).resolve()
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        entry: dict[str, Any] = {"path": key, "exists": path.exists()}
        if path.is_file():
            entry.update({"kind": "file", "bytes": path.stat().st_size,
                          "sha256": sha256_file(path)})
        elif path.is_dir():
            entry["kind"] = "directory"
        else:
            entry["kind"] = "missing"
        entries.append(entry)
    payload = {"paths": sorted(entries, key=lambda item: item["path"]),
               "values": dict(values or {})}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _pipeline_status(exit_code: int, report: Mapping[str, Any]) -> str:
    if exit_code == 0:
        return PIPELINE_STATUS_COMPLETE
    if exit_code == 4:
        return PIPELINE_STATUS_INCOMPLETE
    preflight = report.get("preflight")
    if (isinstance(preflight, Mapping) and
            preflight.get("status") == "BASELINE_READY"):
        return PIPELINE_STATUS_BLOCKED
    return PIPELINE_STATUS_BLOCKED


def bind_pipeline_report(
    report: Mapping[str, Any],
    *,
    exit_code: int,
    input_fingerprint: Optional[str] = None,
) -> dict[str, Any]:
    """Bind the complete pipeline report, including status and provenance."""
    if not isinstance(report, Mapping):
        raise TypeError("pipeline report must be a mapping")
    bound = dict(report)
    bound.setdefault("profile_id", PIPELINE_PROFILE_ID)
    bound["pipeline_status"] = _pipeline_status(exit_code, bound)
    bound["exit_code"] = int(exit_code)
    bound["promotion_eligible"] = False
    bound["production_authorized"] = False
    bound["no_claims"] = list(NO_CLAIMS)
    bound["stage_statuses"] = {
        stage: (bound.get(stage, {}).get("status")
                if isinstance(bound.get(stage), Mapping) else None)
        for stage in ("A_CATALOG", "B_SCREEN", "E_VALIDATION", "F_BRIEFING")
    }
    if input_fingerprint is not None:
        bound["input_fingerprint"] = input_fingerprint
    return bind_artifact_envelope(bound)


def verify_pipeline_report(payload: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """Verify a complete pipeline report before it is used for handoff."""
    ok, problems = verify_artifact_envelope(payload)
    if not isinstance(payload, Mapping):
        return False, problems
    if payload.get("profile_id") != PIPELINE_PROFILE_ID:
        problems.append("pipeline report profile_id is not FRAMEWORK_V1_FULL")
    if payload.get("promotion_eligible") is not False:
        problems.append("pipeline report must not be promotion eligible")
    if payload.get("production_authorized") is not False:
        problems.append("pipeline report must not authorize production")
    if not isinstance(payload.get("no_claims"), list) or not payload.get(
            "no_claims"):
        problems.append("pipeline report no_claims is required")
    if ("preflight" in payload and
            not isinstance(payload.get("preflight"), Mapping)):
        problems.append("pipeline report preflight must be an object")
    exit_code = payload.get("exit_code")
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        problems.append("pipeline report exit_code must be an integer")
    else:
        expected_status = _pipeline_status(exit_code, payload)
        if payload.get("pipeline_status") != expected_status:
            problems.append(
                f"pipeline report pipeline_status must be {expected_status!r}")
    stages = ("A_CATALOG", "B_SCREEN", "E_VALIDATION", "F_BRIEFING")
    for stage in stages:
        if not isinstance(payload.get(stage), Mapping):
            problems.append(f"pipeline report stage {stage} is required")
    stage_statuses = payload.get("stage_statuses")
    if not isinstance(stage_statuses, Mapping):
        problems.append("pipeline report stage_statuses are required")
    else:
        for stage in stages:
            stage_payload = payload.get(stage)
            stage_status = (stage_payload.get("status")
                            if isinstance(stage_payload, Mapping) else None)
            if stage_statuses.get(stage) != stage_status:
                problems.append(
                    f"pipeline report stage_statuses does not match {stage}")
        allowed_stage_statuses = {
            "A_CATALOG": {C.PHASE_STATUS_A_READY, C.PHASE_STATUS_A_BLOCKED},
            "B_SCREEN": {C.PHASE_STATUS_B_TO_C_READY,
                          C.PHASE_STATUS_B_TO_C_BLOCKED},
            "E_VALIDATION": {C.PHASE_STATUS_E_READY,
                              C.PHASE_STATUS_E_BLOCKED},
            "F_BRIEFING": {C.PHASE_STATUS_F_READY,
                            C.PHASE_STATUS_F_BLOCKED},
        }
        for stage, allowed in allowed_stage_statuses.items():
            if stage_statuses.get(stage) not in allowed:
                problems.append(
                    f"pipeline report {stage} has an invalid stage status")

        a_status = stage_statuses.get("A_CATALOG")
        b_status = stage_statuses.get("B_SCREEN")
        e_status = stage_statuses.get("E_VALIDATION")
        f_status = stage_statuses.get("F_BRIEFING")
        if a_status == C.PHASE_STATUS_A_BLOCKED and any(
                status != C.PHASE_STATUS_B_TO_C_BLOCKED
                for status in (b_status,)
        ):
            problems.append(
                "stage dependency: B cannot be ready when A is blocked")
        if a_status == C.PHASE_STATUS_A_BLOCKED and any(
                status != blocked for status, blocked in (
                    (e_status, C.PHASE_STATUS_E_BLOCKED),
                    (f_status, C.PHASE_STATUS_F_BLOCKED),
                )):
            problems.append(
                "stage dependency: E/F cannot be ready when A is blocked")
        if b_status == C.PHASE_STATUS_B_TO_C_BLOCKED and any(
                status != blocked for status, blocked in (
                    (e_status, C.PHASE_STATUS_E_BLOCKED),
                    (f_status, C.PHASE_STATUS_F_BLOCKED),
                )):
            problems.append(
                "stage dependency: E/F cannot be ready when B is blocked")
        if e_status == C.PHASE_STATUS_E_BLOCKED and \
                f_status == C.PHASE_STATUS_F_READY:
            problems.append(
                "stage dependency: F cannot be ready when E is blocked")
        if e_status == C.PHASE_STATUS_E_READY and any(
                status != ready for status, ready in (
                    (a_status, C.PHASE_STATUS_A_READY),
                    (b_status, C.PHASE_STATUS_B_TO_C_READY),
                )):
            problems.append(
                "stage dependency: E cannot be ready before A and B")
        if f_status == C.PHASE_STATUS_F_READY and any(
                status != ready for status, ready in (
                    (a_status, C.PHASE_STATUS_A_READY),
                    (b_status, C.PHASE_STATUS_B_TO_C_READY),
                    (e_status, C.PHASE_STATUS_E_READY),
                )):
            problems.append(
                "stage dependency: F cannot be ready before A, B, and E")
        if exit_code == 0 and any(
                status != ready for status, ready in (
                    (a_status, C.PHASE_STATUS_A_READY),
                    (b_status, C.PHASE_STATUS_B_TO_C_READY),
                    (e_status, C.PHASE_STATUS_E_READY),
                    (f_status, C.PHASE_STATUS_F_READY),
                )):
            problems.append(
                "pipeline exit 0 requires every stage to be ready")
        if (a_status == C.PHASE_STATUS_A_READY and
                b_status == C.PHASE_STATUS_B_TO_C_READY and
                e_status == C.PHASE_STATUS_E_READY and
                f_status == C.PHASE_STATUS_F_READY and exit_code != 0):
            problems.append(
                "all stages ready requires pipeline exit 0")
    return ok and not problems, problems


def bind_pipeline_checkpoint(
    payload: Mapping[str, Any],
    *,
    input_fingerprint: str,
) -> dict[str, Any]:
    """Create a self-hashed checkpoint for a safe replay boundary."""
    if not isinstance(payload, Mapping):
        raise TypeError("pipeline checkpoint must be a mapping")
    checkpoint = dict(payload)
    checkpoint.setdefault("profile_id", PIPELINE_PROFILE_ID)
    checkpoint["checkpoint_type"] = "PIPELINE_CHECKPOINT_V1"
    checkpoint["input_fingerprint"] = input_fingerprint
    checkpoint["promotion_eligible"] = False
    return bind_artifact_envelope(checkpoint)


def write_pipeline_checkpoint(
    path: str | Path,
    payload: Mapping[str, Any],
    *,
    input_fingerprint: str,
) -> dict[str, Any]:
    checkpoint = bind_pipeline_checkpoint(payload, input_fingerprint=input_fingerprint)
    write_deterministic_json(path, checkpoint)
    return checkpoint


def load_verified_pipeline_checkpoint(
    path: str | Path,
    *,
    input_fingerprint: str,
) -> tuple[Optional[dict[str, Any]], list[str]]:
    """Load a checkpoint only when its envelope and input fingerprint agree."""
    checkpoint_path = Path(path)
    try:
        payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, [f"pipeline checkpoint is unreadable: {exc}"]
    ok, problems = verify_artifact_envelope(payload)
    if not isinstance(payload, Mapping):
        problems.append("pipeline checkpoint must be a mapping")
        return None, problems
    if payload.get("profile_id") != PIPELINE_PROFILE_ID:
        problems.append("pipeline checkpoint profile_id is invalid")
    if payload.get("checkpoint_type") != "PIPELINE_CHECKPOINT_V1":
        problems.append("pipeline checkpoint type is invalid")
    if payload.get("promotion_eligible") is not False:
        problems.append("pipeline checkpoint must not be promotion eligible")
    if payload.get("input_fingerprint") != input_fingerprint:
        problems.append("pipeline checkpoint input fingerprint does not match")
    run_state = payload.get("run_state")
    if run_state not in RESUMABLE_PIPELINE_STATES:
        problems.append(
            "pipeline checkpoint run_state is not resumable: "
            f"{run_state!r}")
    return (dict(payload) if ok and not problems else None), problems
