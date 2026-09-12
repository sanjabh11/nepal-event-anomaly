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
                         gate_input_artifact_sha256,
                         sha256_file, verify_artifact_envelope,
                         verify_gate_input,
                         write_deterministic_json)

PIPELINE_PROFILE_ID = "FRAMEWORK_V1_FULL"
PIPELINE_STATUS_COMPLETE = "PIPELINE_COMPLETE"
PIPELINE_STATUS_BLOCKED = "PIPELINE_BLOCKED"
PIPELINE_STATUS_INCOMPLETE = "PIPELINE_INCOMPLETE"
PIPELINE_STATUS_FAILED = "PIPELINE_FAILED"
PIPELINE_EXIT_CODES = frozenset({0, 2, 3, 4, 5})
RESUMABLE_PIPELINE_STATES = frozenset({"RUNNING", "INCOMPLETE", "COMPLETED"})

NO_CLAIMS = (
    "Framework implementation evidence is not scientific validation",
    "No warning, production, or authority approval is established",
    "C and D remain intentionally blocked/not run under framework v1",
)


def _is_sha256(value: Any) -> bool:
    """Return whether *value* is a lowercase hexadecimal SHA-256 digest."""
    return (isinstance(value, str) and len(value) == 64 and
            all(char in "0123456789abcdef" for char in value))


def _ready_stage_evidence_problems(
        stage_name: str,
        stage: Mapping[str, Any],
        *,
        require_gate_passed: bool = False,
        required_inner_status: Optional[str] = None,
        require_envelope_path: bool = False,
) -> list[str]:
    """Check the report-level evidence contract for a ready stage.

    The pipeline report does not receive an artifact root, so it cannot
    re-hash the stage file here.  The stage executor must verify the complete
    envelope before writing these fields; this helper prevents a report from
    declaring readiness while omitting the executor's verification marker,
    digest, or artifact reference.
    """
    problems: list[str] = []
    if stage.get("envelope_verified") is not True:
        problems.append(
            f"{stage_name} evidence must report envelope_verified=True")
    if not _is_sha256(stage.get("artifact_sha256")):
        problems.append(
            f"{stage_name} evidence must include a lowercase artifact_sha256")
    artifact = stage.get("artifact")
    if not isinstance(artifact, str) or not artifact:
        problems.append(f"{stage_name} evidence must include an artifact reference")
    file_hash_key = ("envelope_file_sha256"
                     if stage_name == "F_BRIEFING" and
                     isinstance(stage.get("envelope"), str)
                     else "artifact_file_sha256")
    if not _is_sha256(stage.get(file_hash_key)):
        problems.append(
            f"{stage_name} evidence must include {file_hash_key}")
    if require_gate_passed and stage.get("gate_passed") is not True:
        problems.append(f"{stage_name} evidence must report gate_passed=True")
    if (required_inner_status is not None and
            stage.get("screen_status") != required_inner_status):
        problems.append(
            f"{stage_name} evidence must report screen_status="
            f"{required_inner_status!r}")
    if require_envelope_path:
        envelope = stage.get("envelope")
        if not isinstance(envelope, str) or not envelope:
            problems.append(
                f"{stage_name} evidence must include an envelope reference")
    verification_errors = stage.get("verification_errors")
    if verification_errors not in (None, []):
        problems.append(f"{stage_name} evidence contains verification errors")
    return problems


def _resolve_stage_file(
        raw_path: Any,
        root: Path,
        label: str,
) -> tuple[Optional[Path], list[str]]:
    """Resolve a stage evidence path without permitting root escapes."""
    if not isinstance(raw_path, str) or not raw_path:
        return None, [f"{label} file reference is required"]
    candidate = Path(raw_path) if Path(raw_path).is_absolute() else root / raw_path
    if not Path(raw_path).is_absolute() and ".." in Path(raw_path).parts:
        return None, [f"{label} file reference contains path traversal"]
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None, [f"{label} file reference escapes the artifact root"]
    current = root
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return None, [f"{label} file reference escapes the artifact root"]
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return None, [f"{label} file reference must not traverse a symlink"]
    if not candidate.is_file():
        return None, [f"{label} file is missing or is not a regular file"]
    return candidate, []


def _outer_framework_contract_problems(
        name: str,
        outer: Optional[Mapping[str, Any]],
) -> list[str]:
    if not isinstance(outer, Mapping):
        return [f"{name} outer artifact envelope is required"]
    provenance = outer.get("provenance")
    if (not isinstance(provenance, Mapping) or
            provenance.get("framework_contract_sha256") != C.contract_hash()):
        return [f"{name} outer envelope framework contract does not match runtime"]
    return []


def verify_typed_handoff(
        a_gate: Any,
        b_gate: Any = None,
        e_gate: Any = None,
        *,
        summary: Optional[Mapping[str, Any]] = None,
        require_strict_e: bool = False,
) -> tuple[bool, list[str]]:
    """Verify typed A/B/E handoff identity and cross-stage bindings.

    This is the shared in-memory boundary used by E, F, and the pipeline
    report verifier.  It validates the complete outer envelopes and their
    cross-links; file-byte evidence is added by
    :func:`verify_stage_file_evidence` when a report root is available.
    """
    problems: list[str] = []
    a_inner: Optional[Mapping[str, Any]] = None
    b_inner: Optional[Mapping[str, Any]] = None
    a_outer: Optional[Mapping[str, Any]] = None
    b_outer: Optional[Mapping[str, Any]] = None

    a_ok, a_inner, a_outer, a_errors = verify_gate_input(
        a_gate, expected_gate_id=C.GateId.A_CATALOG.value,
        require_outer_envelope=True)
    problems.extend(f"A_CATALOG: {error}" for error in a_errors)
    problems.extend(_outer_framework_contract_problems("A_CATALOG", a_outer))
    if a_ok and (not isinstance(a_inner, Mapping) or
                 a_inner.get("passed") is not True):
        problems.append("A_CATALOG: verified gate is not passed")

    if b_gate is not None:
        b_ok, b_inner, b_outer, b_errors = verify_gate_input(
            b_gate, expected_gate_id=C.GateId.B_TO_C.value,
            require_outer_envelope=True)
        problems.extend(f"B_SCREEN: {error}" for error in b_errors)
        problems.extend(_outer_framework_contract_problems("B_SCREEN", b_outer))
        if b_ok and (not isinstance(b_inner, Mapping) or
                     b_inner.get("passed") is not True):
            problems.append("B_SCREEN: verified gate is not passed")
        if isinstance(b_outer, Mapping):
            if b_outer.get("status") != C.PHASE_STATUS_SCREEN_RANKED:
                problems.append("B_SCREEN: status must be SCREEN_RANKED")
            if b_outer.get("phase_status") != C.PHASE_STATUS_B_TO_C_READY:
                problems.append("B_SCREEN: phase_status must be B_TO_C_READY")
            if b_outer.get("gate_passed") is not True:
                problems.append("B_SCREEN: gate_passed must be true")
            b_provenance = b_outer.get("provenance")
            expected_a = (gate_input_artifact_sha256(a_gate)
                          if isinstance(a_gate, Mapping) else None)
            if (not isinstance(b_provenance, Mapping) or
                    b_provenance.get("a_gate_artifact_sha256") != expected_a):
                problems.append("B_SCREEN: A gate identity does not match")

    if e_gate is not None:
        from .validation import verify_validation_artifact

        if not isinstance(e_gate, Mapping):
            problems.append("E_VALIDATION: artifact envelope must be a mapping")
        else:
            e_ok, e_errors = verify_validation_artifact(e_gate)
            problems.extend(f"E_VALIDATION: {error}" for error in e_errors)
            if require_strict_e and e_gate.get("strict_contract") is not True:
                problems.append("E_VALIDATION: strict authenticated envelope is required")
            if e_gate.get("status") != C.PHASE_STATUS_E_READY:
                problems.append("E_VALIDATION: status must be E_READY")
            e_inner = e_gate.get("gate")
            if (e_ok and (not isinstance(e_inner, Mapping) or
                          e_inner.get("passed") is not True)):
                problems.append("E_VALIDATION: verified gate is not passed")
            if summary is not None and e_gate.get("summary") != dict(summary):
                problems.append("E_VALIDATION: summary does not match handoff summary")
            e_provenance = e_gate.get("provenance")
            expected_a = (gate_input_artifact_sha256(a_gate)
                          if isinstance(a_gate, Mapping) else None)
            expected_b = (gate_input_artifact_sha256(b_gate)
                          if isinstance(b_gate, Mapping) else None)
            if (not isinstance(e_provenance, Mapping) or
                    e_provenance.get("a_gate_artifact_sha256") != expected_a):
                problems.append("E_VALIDATION: A gate identity does not match")
            if (not isinstance(e_provenance, Mapping) or
                    e_provenance.get("b_artifact_sha256") != expected_b):
                problems.append("E_VALIDATION: B artifact identity does not match")

    return not problems, problems


def verify_stage_file_evidence(
        stage_name: str,
        stage: Any,
        *,
        artifact_root: str | Path | None,
) -> tuple[Optional[Mapping[str, Any]], list[str]]:
    """Verify ready-stage paths, raw bytes, envelope identity, and stage type."""
    if not isinstance(stage, Mapping):
        return None, [f"{stage_name} stage evidence must be a mapping"]
    if artifact_root is None:
        return None, [f"{stage_name} artifact root is required for file evidence"]
    try:
        root = Path(artifact_root).resolve(strict=True)
    except (OSError, RuntimeError):
        return None, [f"{stage_name} artifact root is unavailable"]
    if not root.is_dir():
        return None, [f"{stage_name} artifact root is not a directory"]

    envelope_ref = stage.get("envelope") if stage_name == "F_BRIEFING" \
        else stage.get("artifact")
    envelope_path, problems = _resolve_stage_file(
        envelope_ref, root, f"{stage_name} envelope")
    if envelope_path is None:
        return None, problems
    file_hash_key = ("envelope_file_sha256"
                     if stage_name == "F_BRIEFING" and
                     isinstance(stage.get("envelope"), str)
                     else "artifact_file_sha256")
    expected_file_hash = stage.get(file_hash_key)
    if not _is_sha256(expected_file_hash):
        problems.append(f"{stage_name} evidence {file_hash_key} is invalid")
    else:
        try:
            actual_file_hash = sha256_file(envelope_path)
        except OSError as exc:
            problems.append(f"{stage_name} envelope file could not be hashed: {exc}")
        else:
            if actual_file_hash != expected_file_hash:
                problems.append(
                    f"{stage_name} envelope file sha256 does not match evidence")
    try:
        loaded = json.loads(envelope_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        return None, problems + [f"{stage_name} envelope is not valid JSON: {exc}"]
    if not isinstance(loaded, Mapping):
        return None, problems + [f"{stage_name} envelope must be a mapping"]
    _, envelope_errors = verify_artifact_envelope(loaded)
    problems.extend(f"{stage_name} envelope: {error}" for error in envelope_errors)
    if loaded.get("artifact_sha256") != stage.get("artifact_sha256"):
        problems.append(f"{stage_name} semantic artifact identity does not match evidence")

    if stage_name == "A_CATALOG":
        a_ok, a_inner, _, a_errors = verify_gate_input(
            loaded, expected_gate_id=C.GateId.A_CATALOG.value,
            require_outer_envelope=True)
        problems.extend(f"A_CATALOG envelope: {error}" for error in a_errors)
        if a_ok and (not isinstance(a_inner, Mapping) or
                     a_inner.get("passed") is not True):
            problems.append("A_CATALOG envelope gate is not passed")
    elif stage_name == "B_SCREEN":
        b_ok, b_inner, _, b_errors = verify_gate_input(
            loaded, expected_gate_id=C.GateId.B_TO_C.value,
            require_outer_envelope=True)
        problems.extend(f"B_SCREEN envelope: {error}" for error in b_errors)
        if b_ok and (not isinstance(b_inner, Mapping) or
                     b_inner.get("passed") is not True):
            problems.append("B_SCREEN envelope gate is not passed")
        if loaded.get("phase_status") != stage.get("status"):
            problems.append("B_SCREEN phase_status does not match report status")
        if loaded.get("status") != stage.get("screen_status"):
            problems.append("B_SCREEN screen_status does not match envelope")
        if loaded.get("gate_passed") != stage.get("gate_passed"):
            problems.append("B_SCREEN gate_passed does not match envelope")
    elif stage_name == "E_VALIDATION":
        from .validation import verify_validation_artifact

        _, e_errors = verify_validation_artifact(loaded)
        problems.extend(f"E_VALIDATION envelope: {error}" for error in e_errors)
        if loaded.get("strict_contract") is not True:
            problems.append("E_VALIDATION envelope must use strict_contract=True")
        if loaded.get("status") != stage.get("status"):
            problems.append("E_VALIDATION status does not match report status")
        gate = loaded.get("gate")
        gate_passed = (gate.get("passed")
                       if isinstance(gate, Mapping) else None)
        if gate_passed != stage.get("gate_passed"):
            problems.append("E_VALIDATION gate_passed does not match envelope")
    elif stage_name == "F_BRIEFING":
        from .briefing import verify_briefing_artifact

        _, f_errors = verify_briefing_artifact(loaded)
        problems.extend(f"F_BRIEFING envelope: {error}" for error in f_errors)
        if loaded.get("strict_contract") is not True:
            problems.append("F_BRIEFING envelope must use strict_contract=True")
        if loaded.get("status") != stage.get("status"):
            problems.append("F_BRIEFING status does not match report status")
        artifact_path, artifact_errors = _resolve_stage_file(
            stage.get("artifact"), root, "F_BRIEFING briefing")
        problems.extend(artifact_errors)
        expected_artifact_hash = stage.get("artifact_file_sha256")
        if not _is_sha256(expected_artifact_hash):
            problems.append("F_BRIEFING evidence artifact_file_sha256 is invalid")
        elif artifact_path is not None:
            try:
                actual_artifact_hash = sha256_file(artifact_path)
            except OSError as exc:
                problems.append(f"F_BRIEFING briefing file could not be hashed: {exc}")
            else:
                if actual_artifact_hash != expected_artifact_hash:
                    problems.append("F_BRIEFING briefing file sha256 does not match evidence")

    return loaded, problems


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
    if exit_code == 5:
        return PIPELINE_STATUS_FAILED
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
    provenance = bound.get("provenance")
    bound["provenance"] = (dict(provenance)
                            if isinstance(provenance, Mapping) else {})
    bound["provenance"].setdefault(
        "framework_contract_sha256", C.contract_hash())
    bound["no_claims"] = list(NO_CLAIMS)
    bound["stage_statuses"] = {
        stage: (bound.get(stage, {}).get("status")
                if isinstance(bound.get(stage), Mapping) else None)
        for stage in ("A_CATALOG", "B_SCREEN", "E_VALIDATION", "F_BRIEFING")
    }
    if input_fingerprint is not None:
        bound["input_fingerprint"] = input_fingerprint
    return bind_artifact_envelope(bound)


def verify_pipeline_report(
        payload: Mapping[str, Any],
        *,
        artifact_root: str | Path | None = None,
) -> tuple[bool, list[str]]:
    """Verify a complete pipeline report before it is used for handoff.

    A report with any ready stage must provide the output root so the stage
    references can be resolved and their actual bytes can be checked.  The
    optional root remains useful for reports containing only blocked stages,
    which have no stage artifacts to inspect.
    """
    ok, problems = verify_artifact_envelope(payload)
    if not isinstance(payload, Mapping):
        return False, problems
    if payload.get("profile_id") != PIPELINE_PROFILE_ID:
        problems.append("pipeline report profile_id is not FRAMEWORK_V1_FULL")
    if payload.get("promotion_eligible") is not False:
        problems.append("pipeline report must not be promotion eligible")
    if payload.get("production_authorized") is not False:
        problems.append("pipeline report must not authorize production")
    provenance = payload.get("provenance")
    if not isinstance(provenance, Mapping):
        problems.append("pipeline report provenance is required")
    elif provenance.get("framework_contract_sha256") != C.contract_hash():
        problems.append(
            "pipeline report framework contract does not match runtime")
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
        if exit_code not in PIPELINE_EXIT_CODES:
            problems.append(
                "pipeline report exit_code is not a supported framework code")
        else:
            expected_status = _pipeline_status(exit_code, payload)
            if payload.get("pipeline_status") != expected_status:
                problems.append(
                    f"pipeline report pipeline_status must be {expected_status!r}")
    stages = ("A_CATALOG", "B_SCREEN", "E_VALIDATION", "F_BRIEFING")
    for stage in stages:
        if not isinstance(payload.get(stage), Mapping):
            problems.append(f"pipeline report stage {stage} is required")
    a_stage = payload.get("A_CATALOG")
    if (isinstance(a_stage, Mapping) and
            a_stage.get("status") == C.PHASE_STATUS_A_READY):
        a_ok, a_inner, a_outer, a_errors = verify_gate_input(
            a_stage.get("gate"),
            expected_gate_id=C.GateId.A_CATALOG.value,
            require_outer_envelope=True,
        )
        if not a_ok:
            problems.extend("A_CATALOG evidence: " + error
                            for error in a_errors)
        elif not isinstance(a_inner, Mapping) or a_inner.get("passed") is not True:
            problems.append("A_CATALOG evidence: verified gate is not passed")
        if isinstance(a_outer, Mapping):
            a_provenance = a_outer.get("provenance")
            if (not isinstance(a_provenance, Mapping) or
                    a_provenance.get("framework_contract_sha256") !=
                    C.contract_hash()):
                problems.append(
                    "A_CATALOG evidence: outer envelope framework contract "
                    "does not match runtime")
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
            "A_CATALOG": (C.PHASE_STATUS_A_READY, C.PHASE_STATUS_A_BLOCKED),
            "B_SCREEN": (C.PHASE_STATUS_B_TO_C_READY,
                          C.PHASE_STATUS_B_TO_C_BLOCKED),
            "E_VALIDATION": (C.PHASE_STATUS_E_READY,
                              C.PHASE_STATUS_E_BLOCKED),
            "F_BRIEFING": (C.PHASE_STATUS_F_READY,
                            C.PHASE_STATUS_F_BLOCKED),
        }
        for stage, allowed in allowed_stage_statuses.items():
            if stage_statuses.get(stage) not in allowed:
                problems.append(
                    f"pipeline report {stage} has an invalid stage status")

        ready_payloads: dict[str, Mapping[str, Any]] = {}
        for stage in stages:
            stage_payload = payload.get(stage)
            if (not isinstance(stage_payload, Mapping) or
                    stage_statuses.get(stage) not in (
                        C.PHASE_STATUS_A_READY,
                        C.PHASE_STATUS_B_TO_C_READY,
                        C.PHASE_STATUS_E_READY,
                        C.PHASE_STATUS_F_READY,
                    )):
                continue
            loaded, file_errors = verify_stage_file_evidence(
                stage, stage_payload, artifact_root=artifact_root)
            problems.extend(file_errors)
            if loaded is not None:
                ready_payloads[stage] = loaded

        if ("A_CATALOG" in ready_payloads and
                "B_SCREEN" in ready_payloads):
            typed_ok, typed_errors = verify_typed_handoff(
                ready_payloads["A_CATALOG"], ready_payloads["B_SCREEN"],
                ready_payloads.get("E_VALIDATION"),
                summary=(ready_payloads["E_VALIDATION"].get("summary")
                         if "E_VALIDATION" in ready_payloads else None),
                require_strict_e="E_VALIDATION" in ready_payloads)
            if not typed_ok:
                problems.extend("typed handoff: " + error
                                for error in typed_errors)

        b_stage = payload.get("B_SCREEN")
        if (stage_statuses.get("B_SCREEN") == C.PHASE_STATUS_B_TO_C_READY and
                isinstance(b_stage, Mapping)):
            problems.extend(_ready_stage_evidence_problems(
                "B_SCREEN", b_stage,
                require_gate_passed=True,
                required_inner_status=C.PHASE_STATUS_SCREEN_RANKED,
            ))
        e_stage = payload.get("E_VALIDATION")
        if (stage_statuses.get("E_VALIDATION") == C.PHASE_STATUS_E_READY and
                isinstance(e_stage, Mapping)):
            problems.extend(_ready_stage_evidence_problems(
                "E_VALIDATION", e_stage, require_gate_passed=True))
        f_stage = payload.get("F_BRIEFING")
        if (stage_statuses.get("F_BRIEFING") == C.PHASE_STATUS_F_READY and
                isinstance(f_stage, Mapping)):
            problems.extend(_ready_stage_evidence_problems(
                "F_BRIEFING", f_stage, require_envelope_path=True))

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
