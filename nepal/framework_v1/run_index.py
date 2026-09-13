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
RUN_INDEX_I1_TYPE = "RUN_INDEX_I1"
RUN_INDEX_FILENAME = "run_index.json"

_SHA256_RE = re.compile(r"[0-9a-f]{64}")

#: Index fields whose values must be run-root-relative paths, never absolute.
_RELATIVE_PATH_FIELDS = ("candidate_root", "pipeline_root", "handoff_path",
                         "active_audit_packet")

#: I1-only canonical path fields (all run-root-relative).  The I1 index
#: binds the successor manifest and the unchanged v2 artifact root as two
#: separate roots because the loader already takes manifest_path and the
#: artifact root as independent parameters.
_I1_PATH_FIELDS = ("candidate_manifest_path", "candidate_artifact_root",
                   "handoff_path", "active_audit_packet")


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


def build_run_index_i1(*, run_root_name: str,
                       active_generation_id: str,
                       candidate_manifest_path: str,
                       candidate_artifact_root: str,
                       manifest_sha256: str,
                       manifest_file_sha256: str,
                       parent_manifest_sha256: str,
                       parent_manifest_file_sha256: str,
                       active_report: Mapping[str, Any],
                       compute_parent: Mapping[str, Any],
                       active_audit_packet: str,
                       active_audit_packet_file_sha256: str,
                       handoff_path: str,
                       handoff_file_sha256: str,
                       superseded_generations: tuple | list = (),
                       diagnostics: Optional[Mapping[str, Any]] = None
                       ) -> dict[str, Any]:
    """Build a self-hashed RUN_INDEX_I1 document (N01).

    The I1 index unifies the active chain on ONE report object:

    * ``active_report`` — the metadata-rebound pipeline report for the I1
      generation, with its raw file digest (``file_sha256``) and envelope
      self-hash (``artifact_sha256``) in separate named fields;
    * ``compute_parent`` — the immutable ``official-pipeline-v3`` report
      that actually executed B, bound by raw file digest only;
    * ``candidate_manifest_path`` + ``candidate_artifact_root`` — the
      successor manifest and the unchanged v2 artifact root, bound
      separately because the strict loader takes them as independent
      parameters;
    * ``handoff_file_sha256`` — the handoff's RAW file digest, mandatory
      and non-null (the v1 index allowed null, which split the chain).

    No field ever mixes the raw-file and envelope hash domains.
    """
    if not isinstance(active_generation_id, str) or not active_generation_id:
        raise ValueError("active_generation_id must be a non-empty string")
    for label, digest in (("manifest_sha256", manifest_sha256),
                          ("manifest_file_sha256", manifest_file_sha256),
                          ("parent_manifest_sha256", parent_manifest_sha256),
                          ("parent_manifest_file_sha256",
                           parent_manifest_file_sha256),
                          ("active_audit_packet_file_sha256",
                           active_audit_packet_file_sha256),
                          ("handoff_file_sha256", handoff_file_sha256)):
        if not _is_sha256(digest):
            raise ValueError(f"{label} must be a lowercase SHA-256")
    if not isinstance(active_report, Mapping) or not _is_sha256(
            active_report.get("file_sha256")) or not _is_sha256(
            active_report.get("artifact_sha256")):
        raise ValueError("active_report requires file_sha256 and "
                         "artifact_sha256 digests")
    if not isinstance(compute_parent, Mapping) or not _is_sha256(
            compute_parent.get("report_file_sha256")):
        raise ValueError("compute_parent requires report_file_sha256")
    index: dict[str, Any] = {
        "index_type": RUN_INDEX_I1_TYPE,
        "active_generation_id": active_generation_id,
        "roots": {"run_root_name": run_root_name},
        "candidate_manifest_path": candidate_manifest_path,
        "candidate_artifact_root": candidate_artifact_root,
        "manifest_sha256": manifest_sha256,
        "manifest_file_sha256": manifest_file_sha256,
        "parent_manifest_sha256": parent_manifest_sha256,
        "parent_manifest_file_sha256": parent_manifest_file_sha256,
        "pipeline_run_id": active_report.get("run_id"),
        "active_report": dict(active_report),
        "compute_parent": dict(compute_parent),
        "active_audit_packet": active_audit_packet,
        "active_audit_packet_file_sha256": active_audit_packet_file_sha256,
        "handoff_path": handoff_path,
        "handoff_file_sha256": handoff_file_sha256,
        "superseded_generations": sorted(superseded_generations),
        "selection_policy": (
            "only active_generation_id may authorize current evidence; the "
            "active_report is the sole current chain and compute_parent is "
            "immutable historical compute evidence"),
        "diagnostics": dict(diagnostics or {}),
    }
    return bind_artifact_envelope(index)


def _verify_i1_index(payload: Mapping[str, Any],
                     problems: list[str],
                     run_root: Optional[Path]) -> None:
    """I1-specific verification: one active report, hash-domain separation."""
    active_report = payload.get("active_report")
    if not isinstance(active_report, Mapping):
        problems.append("RUN_INDEX_I1 requires an active_report object")
        active_report = {}
    else:
        for field in ("path", "file_sha256", "artifact_sha256"):
            if field not in active_report:
                problems.append(f"active_report.{field} is required")
        _check_rel(active_report.get("path"), "active_report.path",
                   problems)
        for field in ("file_sha256", "artifact_sha256"):
            if not _is_sha256(active_report.get(field)):
                problems.append(f"active_report.{field} must be a "
                                "lowercase SHA-256")
        if _is_sha256(active_report.get("file_sha256")) and _is_sha256(
                active_report.get("artifact_sha256")) and \
                active_report["file_sha256"] == \
                active_report["artifact_sha256"]:
            problems.append("active_report file digest and envelope "
                            "self-hash must be distinct hash domains")

    compute_parent = payload.get("compute_parent")
    if not isinstance(compute_parent, Mapping):
        problems.append("RUN_INDEX_I1 requires a compute_parent object")
        compute_parent = {}
    else:
        _check_rel(compute_parent.get("report_path"),
                   "compute_parent.report_path", problems)
        if not _is_sha256(compute_parent.get("report_file_sha256")):
            problems.append("compute_parent.report_file_sha256 must be a "
                            "lowercase SHA-256")
        # The compute parent must not double as the active report.
        if active_report and compute_parent.get("report_path") == \
                active_report.get("path"):
            problems.append("active_report and compute_parent must be "
                            "distinct reports")

    for field in _I1_PATH_FIELDS:
        _check_rel(payload.get(field), field, problems)
    if not _is_sha256(payload.get("handoff_file_sha256")):
        problems.append("RUN_INDEX_I1 handoff_file_sha256 is mandatory "
                        "and must be a lowercase SHA-256 (non-null)")
    for field in ("manifest_sha256", "manifest_file_sha256",
                  "parent_manifest_sha256", "parent_manifest_file_sha256",
                  "active_audit_packet_file_sha256"):
        if not _is_sha256(payload.get(field)):
            problems.append(f"run index {field} must be a lowercase SHA-256")

    if run_root is not None:
        bindings = (
            ("candidate manifest", payload.get("candidate_manifest_path"),
             payload.get("manifest_file_sha256")),
            ("active report", active_report.get("path"),
             active_report.get("file_sha256")),
            ("compute parent report", compute_parent.get("report_path"),
             compute_parent.get("report_file_sha256")),
            ("active audit packet", payload.get("active_audit_packet"),
             payload.get("active_audit_packet_file_sha256")),
            ("handoff", payload.get("handoff_path"),
             payload.get("handoff_file_sha256")),
        )
        for label, rel, expected in bindings:
            if not isinstance(rel, str) or expected is None:
                continue
            target = run_root / rel
            if not target.is_file():
                problems.append(f"indexed {label} missing on disk: {rel}")
                continue
            actual = sha256_file(target)
            if actual != expected:
                problems.append(
                    f"indexed {label} checksum mismatch: {rel} "
                    f"({expected} -> {actual})")
        artifact_root = payload.get("candidate_artifact_root")
        if isinstance(artifact_root, str) and not (
                run_root / artifact_root).is_dir():
            problems.append("indexed candidate_artifact_root missing on "
                            f"disk: {artifact_root}")
        if isinstance(active_report.get("path"), str) and _is_sha256(
                active_report.get("artifact_sha256")):
            target = run_root / active_report["path"]
            if target.is_file():
                import json
                try:
                    report = json.loads(target.read_text("utf-8"))
                except (OSError, ValueError) as exc:
                    problems.append(f"active report unreadable: {exc}")
                else:
                    if isinstance(report, Mapping) and report.get(
                            "artifact_sha256") != active_report.get(
                            "artifact_sha256"):
                        problems.append(
                            "active report envelope self-hash does not "
                            "match the indexed artifact_sha256")


def _check_rel(value: Any, field: str, problems: list[str]) -> None:
    if not isinstance(value, str) or not value or value.startswith("/") \
            or ".." in Path(value).parts:
        problems.append(f"run index {field} must be a safe relative path")


def verify_run_index(payload: Any, *,
                     run_root: Optional[str | Path] = None,
                     ) -> tuple[bool, list[str]]:
    """Verify a RUN_INDEX_V1 or RUN_INDEX_I1 document end to end.

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
    index_type = payload.get("index_type")
    if index_type not in (RUN_INDEX_TYPE, RUN_INDEX_I1_TYPE):
        problems.append(
            f"index_type must be {RUN_INDEX_TYPE!r} or "
            f"{RUN_INDEX_I1_TYPE!r}")

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

    if index_type == RUN_INDEX_I1_TYPE:
        _verify_i1_index(payload, problems,
                         Path(run_root) if run_root is not None else None)
    else:
        for field in ("manifest_sha256", "manifest_file_sha256",
                      "pipeline_report_sha256",
                      "active_audit_packet_sha256"):
            if not _is_sha256(payload.get(field)):
                problems.append(
                    f"run index {field} must be a lowercase SHA-256")
        if payload.get("handoff_sha256") is not None and not _is_sha256(
                payload.get("handoff_sha256")):
            problems.append(
                "run index handoff_sha256 must be a lowercase SHA-256")

    canonical_view = {k: v for k, v in payload.items()
                      if k not in ("diagnostics", "artifact_sha256")}
    for dotted, value in _iter_strings(canonical_view):
        if value.startswith("/"):
            problems.append(
                f"absolute path in canonical run index field {dotted!r}; "
                "canonical bindings must be run-root-relative")

    if index_type == RUN_INDEX_TYPE:
        for field in _RELATIVE_PATH_FIELDS:
            value = payload.get(field)
            if not isinstance(value, str) or not value or ".." in Path(
                    value).parts:
                problems.append(
                    f"run index {field} must be a safe relative path")

    if run_root is not None and index_type == RUN_INDEX_TYPE:
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
