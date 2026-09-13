"""RUN_INDEX_V1 — active-generation index for a run root (R05).

A run root accumulates many candidate generations and pipeline output
directories over time.  Without an index, report discovery and handoff
consumers can silently select stale or incompatible evidence.  The run index
is a small, self-hashed document that names exactly ONE active generation
and binds it to the candidate manifest hashes, the official pipeline report,
the active audit packet, and the current handoff.

Canonical fields use paths relative to the run root — never absolute paths —
so the index stays verifiable after relocation (R09).  Machine-specific
absolute paths may appear only under ``diagnostics``, which verifiers treat
as non-authoritative.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping, Optional

from .provenance import (bind_artifact_envelope, sha256_file,
                         verify_artifact_envelope,
                         write_deterministic_json)

RUN_INDEX_TYPE = "RUN_INDEX_V1"
RUN_INDEX_FILENAME = "run_index.json"

_SHA256_RE = re.compile(r"[0-9a-f]{64}")

#: Index fields whose values must be run-root-relative paths, never absolute.
_RELATIVE_PATH_FIELDS = ("candidate_root", "pipeline_root", "handoff_path",
                         "active_audit_packet")


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA256_RE.fullmatch(value))


def _iter_strings(obj: Any, prefix: str = ""):
    if isinstance(obj, str):
        yield prefix, obj
    elif isinstance(obj, Mapping):
        for key, value in obj.items():
            yield from _iter_strings(value, f"{prefix}{key}.")
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            yield from _iter_strings(value, f"{prefix}[{index}].")


def build_run_index(*, run_root_name: str,
                    active_generation_id: str,
                    candidate_root: str,
                    manifest_sha256: str,
                    manifest_file_sha256: str,
                    pipeline_run_id: str,
                    pipeline_root: str,
                    pipeline_report_sha256: str,
                    active_audit_packet: str,
                    active_audit_packet_sha256: str,
                    handoff_path: str,
                    handoff_sha256: Optional[str] = None,
                    superseded_generations: tuple | list = (),
                    diagnostics: Optional[Mapping[str, Any]] = None
                    ) -> dict[str, Any]:
    """Build a self-hashed RUN_INDEX_V1 document.

    ``candidate_root``, ``pipeline_root``, ``active_audit_packet`` and
    ``handoff_path`` are paths relative to the run root.  ``superseded_*``
    lists are informational: everything not named active is historical.
    """
    if not isinstance(active_generation_id, str) or not active_generation_id:
        raise ValueError("active_generation_id must be a non-empty string")
    index: dict[str, Any] = {
        "index_type": RUN_INDEX_TYPE,
        "active_generation_id": active_generation_id,
        "roots": {"run_root_name": run_root_name},
        "candidate_root": candidate_root,
        "manifest_sha256": manifest_sha256,
        "manifest_file_sha256": manifest_file_sha256,
        "pipeline_run_id": pipeline_run_id,
        "pipeline_root": pipeline_root,
        "pipeline_report_sha256": pipeline_report_sha256,
        "active_audit_packet": active_audit_packet,
        "active_audit_packet_sha256": active_audit_packet_sha256,
        "handoff_path": handoff_path,
        "handoff_sha256": handoff_sha256,
        "superseded_generations": sorted(superseded_generations),
        "selection_policy": (
            "only active_generation_id may authorize current evidence; all "
            "sibling generations and pipeline directories are historical "
            "unless explicitly re-indexed"),
        "diagnostics": dict(diagnostics or {}),
    }
    return bind_artifact_envelope(index)


def verify_run_index(payload: Any, *,
                     run_root: Optional[str | Path] = None,
                     ) -> tuple[bool, list[str]]:
    """Verify a RUN_INDEX_V1 document end to end.

    Checks the envelope self-hash, the exactly-one-active-generation
    invariant, hash shapes, the relative-path policy, and — when
    ``run_root`` is supplied — that every bound artifact exists on disk with
    the recorded bytes.
    """
    problems: list[str] = []
    ok, envelope_problems = verify_artifact_envelope(payload)
    problems.extend(envelope_problems)
    if not isinstance(payload, Mapping):
        problems.append("run index must be a mapping")
        return False, problems
    if payload.get("index_type") != RUN_INDEX_TYPE:
        problems.append(f"index_type must be {RUN_INDEX_TYPE!r}")

    active = payload.get("active_generation_id")
    if not isinstance(active, str) or not active:
        problems.append("run index must name exactly one active generation")
    superseded = payload.get("superseded_generations")
    if not isinstance(superseded, list) or any(
            not isinstance(item, str) or not item for item in superseded):
        problems.append("superseded_generations must be a string list")
        superseded = []
    if isinstance(active, str) and active in superseded:
        problems.append("the active generation cannot also be superseded")

    for field in ("manifest_sha256", "manifest_file_sha256",
                  "pipeline_report_sha256", "active_audit_packet_sha256"):
        if not _is_sha256(payload.get(field)):
            problems.append(f"run index {field} must be a lowercase SHA-256")
    if payload.get("handoff_sha256") is not None and not _is_sha256(
            payload.get("handoff_sha256")):
        problems.append("run index handoff_sha256 must be a lowercase SHA-256")

    canonical_view = {k: v for k, v in payload.items()
                      if k not in ("diagnostics", "artifact_sha256")}
    for dotted, value in _iter_strings(canonical_view):
        if value.startswith("/"):
            problems.append(
                f"absolute path in canonical run index field {dotted!r}; "
                "canonical bindings must be run-root-relative")

    for field in _RELATIVE_PATH_FIELDS:
        value = payload.get(field)
        if not isinstance(value, str) or not value or ".." in Path(
                value).parts:
            problems.append(f"run index {field} must be a safe relative path")

    if run_root is not None:
        root = Path(run_root)
        bindings = (
            ("candidate manifest", payload.get("candidate_root"),
             "manifest.json", payload.get("manifest_file_sha256")),
            ("pipeline report", payload.get("pipeline_root"),
             "pipeline_report.json", payload.get("pipeline_report_sha256")),
            ("active audit packet", payload.get("active_audit_packet"),
             None, payload.get("active_audit_packet_sha256")),
            ("handoff", payload.get("handoff_path"),
             None, payload.get("handoff_sha256")),
        )
        for label, rel, leaf, expected in bindings:
            if not isinstance(rel, str) or expected is None:
                continue
            target = root / rel if leaf is None else root / rel / leaf
            if not target.is_file():
                problems.append(f"indexed {label} missing on disk: {rel}")
                continue
            actual = sha256_file(target)
            if actual != expected:
                problems.append(
                    f"indexed {label} checksum mismatch: {rel} "
                    f"({expected} -> {actual})")
    return (not problems), problems


def write_run_index(path: str | Path, index: Mapping[str, Any]) -> Path:
    """Atomically persist a run index and verify it on re-read."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    write_deterministic_json(p, dict(index))
    ok, problems = verify_run_index(dict(index))
    if not ok:
        raise ValueError("run index failed self-verification after write: "
                         + "; ".join(problems))
    return p


def load_run_index(path: str | Path, *,
                   run_root: Optional[str | Path] = None) -> dict[str, Any]:
    """Load and verify a run index from disk.

    ``run_root`` enables on-disk re-verification of every indexed artifact;
    omit it for structural verification only.
    """
    import json
    p = Path(path)
    payload = json.loads(p.read_text(encoding="utf-8"))
    ok, problems = verify_run_index(payload, run_root=run_root)
    if not ok:
        raise ValueError("run index failed verification: "
                         + "; ".join(problems))
    return payload
