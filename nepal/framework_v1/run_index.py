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

import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Optional

from .provenance import (bind_artifact_envelope, sha256_file,
                         verify_artifact_envelope,
                         write_deterministic_json)

RUN_INDEX_TYPE = "RUN_INDEX_V1"
RUN_INDEX_I1_TYPE = "RUN_INDEX_I1"
RUN_INDEX_FILENAME = "run_index.json"

# INDEX-02: index_profile declares the role the index claims.
# ``ACTIVE`` is the executable profile — it requires a non-null handoff
# binding (digest + relpath), run-root name equality, and an on-disk
# handoff that verifies as an artifact envelope.  ``HISTORICAL`` marks
# an explicitly non-active index; an absent profile is a legacy
# informational index verified under the pre-hardening rules only.
RUN_INDEX_PROFILE_ACTIVE = "ACTIVE"
RUN_INDEX_PROFILE_HISTORICAL = "HISTORICAL"
_INDEX_PROFILES = (RUN_INDEX_PROFILE_ACTIVE, RUN_INDEX_PROFILE_HISTORICAL)

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")

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


def _rel_path_problem(value: Any) -> Optional[str]:
    """INDEX-01: return a reason when ``value`` is not a safe, portable,
    canonical run-root-relative path — forward slashes only, no drive
    letters, no UNC/absolute prefix, no traversal, no NUL, and already
    in canonical form."""
    if not isinstance(value, str) or not value or not value.strip():
        return "must be a non-empty relative path"
    if "\\" in value:
        return "backslash separators are not portable"
    if value.startswith("/"):
        return "absolute/UNC paths are not allowed"
    if _DRIVE_PREFIX_RE.match(value):
        return "drive-letter paths are not portable"
    if "\x00" in value:
        return "NUL bytes are not allowed"
    parts = PurePosixPath(value).parts
    if not parts or ".." in parts:
        return "traversal outside the run root is not allowed"
    if PurePosixPath(value).as_posix() != value:
        return "path is not in canonical forward-slash form"
    return None


def _stored_relpath(value: Any) -> Any:
    """Canonicalize a path to forward-slash form for storage.  Values
    that are merely non-canonical (``a//b``, ``a/./b``) are normalized;
    unsafe values (backslash, drive-letter, absolute, traversal) are
    stored verbatim so verification rejects them — they can never be
    silently reinterpreted."""
    problem = _rel_path_problem(value)
    if problem is None or \
            problem == "path is not in canonical forward-slash form":
        return PurePosixPath(value).as_posix()
    return value


def _resolve_index_target(root: Path, rel: Any, label: str,
                          problems: list[str]) -> Optional[Path]:
    """Resolve an indexed path under ``root`` fail-closed: safe relative
    form, no symlink entry or symlinked parent component, and resolved
    containment inside the run root."""
    if _rel_path_problem(rel) is not None:
        return None  # the field-level check reports the shape problem
    parts = PurePosixPath(rel).parts
    target = root.joinpath(*parts)
    cursor = root
    for part in parts:
        cursor = cursor / part
        if cursor.is_symlink():
            problems.append(
                f"indexed {label} traverses a symlink: {rel}")
            return None
    root_r = root.resolve()
    resolved = target.resolve()
    if resolved != root_r and root_r not in resolved.parents:
        problems.append(
            f"indexed {label} resolves outside the run root: {rel}")
        return None
    return target


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
                    diagnostics: Optional[Mapping[str, Any]] = None,
                    index_profile: Optional[str] = None
                    ) -> dict[str, Any]:
    """Build a self-hashed RUN_INDEX_V1 document.

    ``candidate_root``, ``pipeline_root``, ``active_audit_packet`` and
    ``handoff_path`` are paths relative to the run root — canonicalized
    to forward-slash form; unsafe values (drive letters, UNC, absolute,
    traversal, backslashes) are stored verbatim so verification rejects
    them (INDEX-01).  ``superseded_*`` lists are informational:
    everything not named active is historical.

    ``index_profile=RUN_INDEX_PROFILE_ACTIVE`` (INDEX-02) marks the
    executable profile: it requires a non-null ``handoff_sha256``
    binding the handoff file and a non-empty ``run_root_name``.
    """
    if not isinstance(active_generation_id, str) or not active_generation_id:
        raise ValueError("active_generation_id must be a non-empty string")
    if index_profile is not None and index_profile not in _INDEX_PROFILES:
        raise ValueError(f"index_profile must be one of {_INDEX_PROFILES}")
    candidate_root = _stored_relpath(candidate_root)
    pipeline_root = _stored_relpath(pipeline_root)
    active_audit_packet = _stored_relpath(active_audit_packet)
    handoff_path = _stored_relpath(handoff_path)
    if index_profile == RUN_INDEX_PROFILE_ACTIVE:
        if not _is_sha256(handoff_sha256):
            raise ValueError("the active index profile requires a "
                             "non-null handoff_sha256 binding the "
                             "handoff file")
        if not isinstance(run_root_name, str) or not run_root_name:
            raise ValueError("the active index profile requires a "
                             "non-empty run_root_name")
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
    if index_profile is not None:
        index["index_profile"] = index_profile
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
                       diagnostics: Optional[Mapping[str, Any]] = None,
                       index_profile: Optional[str] = None
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
    if index_profile is not None and index_profile not in _INDEX_PROFILES:
        raise ValueError(f"index_profile must be one of {_INDEX_PROFILES}")
    # INDEX-01: stored paths are canonical forward-slash run-root-relative
    # forms; unsafe values are stored verbatim so verification rejects.
    candidate_manifest_path = _stored_relpath(candidate_manifest_path)
    candidate_artifact_root = _stored_relpath(candidate_artifact_root)
    active_audit_packet = _stored_relpath(active_audit_packet)
    handoff_path = _stored_relpath(handoff_path)
    if index_profile == RUN_INDEX_PROFILE_ACTIVE and (
            not isinstance(run_root_name, str) or not run_root_name):
        raise ValueError("the active index profile requires a "
                         "non-empty run_root_name")
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
    active_report = dict(active_report)
    compute_parent = dict(compute_parent)
    for holder, key in ((active_report, "path"),
                        (compute_parent, "report_path"),
                        (compute_parent, "pipeline_root")):
        if isinstance(holder.get(key), str):
            holder[key] = _stored_relpath(holder[key])
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
        "active_report": active_report,
        "compute_parent": compute_parent,
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
    if index_profile is not None:
        index["index_profile"] = index_profile
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
            target = _resolve_index_target(run_root, rel, label,
                                           problems)
            if target is None:
                continue
            if not target.is_file():
                problems.append(f"indexed {label} missing on disk: {rel}")
                continue
            actual = sha256_file(target)
            if actual != expected:
                problems.append(
                    f"indexed {label} checksum mismatch: {rel} "
                    f"({expected} -> {actual})")
        artifact_root = payload.get("candidate_artifact_root")
        if isinstance(artifact_root, str):
            art_target = _resolve_index_target(
                run_root, artifact_root, "candidate_artifact_root",
                problems)
            if art_target is not None and not art_target.is_dir():
                problems.append(
                    "indexed candidate_artifact_root missing on "
                    f"disk: {artifact_root}")
        if isinstance(active_report.get("path"), str) and _is_sha256(
                active_report.get("artifact_sha256")):
            target = _resolve_index_target(
                run_root, active_report["path"], "active report",
                problems)
            if target is not None and target.is_file():
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
    problem = _rel_path_problem(value)
    if problem is not None:
        problems.append(f"run index {field} must be a safe relative "
                        f"path ({problem})")


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

    # INDEX-02: a declared index_profile must be a known role; the
    # ACTIVE (executable) profile requires a non-null handoff binding
    # (digest + relpath), run-root name equality, and — when a run_root
    # is supplied — an on-disk handoff that verifies as an artifact
    # envelope.
    profile = payload.get("index_profile")
    if profile is not None and profile not in _INDEX_PROFILES:
        problems.append(
            f"index_profile must be one of {_INDEX_PROFILES}")
    if profile == RUN_INDEX_PROFILE_ACTIVE:
        digest_field = ("handoff_file_sha256"
                        if index_type == RUN_INDEX_I1_TYPE
                        else "handoff_sha256")
        active_handoff_rel = payload.get("handoff_path")
        if _rel_path_problem(active_handoff_rel) is not None:
            problems.append(
                "the active index profile requires a non-null handoff "
                "relative path")
        if not _is_sha256(payload.get(digest_field)):
            problems.append(
                "the active index profile requires a non-null "
                f"{digest_field} binding the handoff file")
        roots = payload.get("roots")
        rr_name = (roots.get("run_root_name")
                   if isinstance(roots, Mapping) else None)
        if not isinstance(rr_name, str) or not rr_name:
            problems.append("the active index profile requires "
                            "roots.run_root_name")
        if run_root is not None:
            root_for_active = Path(run_root)
            if isinstance(rr_name, str) and rr_name and \
                    root_for_active.resolve().name != rr_name:
                problems.append(
                    "run-root name mismatch: index declares "
                    f"{rr_name!r} but the verified root is "
                    f"{root_for_active.resolve().name!r}")
            if isinstance(active_handoff_rel, str) and \
                    _rel_path_problem(active_handoff_rel) is None:
                target = _resolve_index_target(
                    root_for_active, active_handoff_rel, "handoff",
                    problems)
                if target is not None:
                    if not target.is_file():
                        problems.append(
                            "active-profile handoff missing on disk: "
                            f"{active_handoff_rel}")
                    else:
                        try:
                            hdoc = json.loads(
                                target.read_text("utf-8"))
                        except (OSError, ValueError) as exc:
                            problems.append(
                                f"active-profile handoff unreadable: "
                                f"{exc}")
                        else:
                            ok_h, h_env = verify_artifact_envelope(hdoc)
                            if not ok_h:
                                problems.append(
                                    "active-profile handoff is not a "
                                    "verified artifact envelope: "
                                    + (h_env[0] if h_env else
                                       "invalid envelope"))

    canonical_view = {k: v for k, v in payload.items()
                      if k not in ("diagnostics", "artifact_sha256")}
    for dotted, value in _iter_strings(canonical_view):
        if value.startswith("/"):
            problems.append(
                f"absolute path in canonical run index field {dotted!r}; "
                "canonical bindings must be run-root-relative")
        elif "\\" in value:
            problems.append(
                f"backslash in canonical run index field {dotted!r}; "
                "canonical bindings use forward slashes only")
        elif _DRIVE_PREFIX_RE.match(value):
            problems.append(
                f"drive-letter path in canonical run index field "
                f"{dotted!r}; canonical bindings must be "
                "run-root-relative")

    if index_type == RUN_INDEX_TYPE:
        for field in _RELATIVE_PATH_FIELDS:
            _check_rel(payload.get(field), field, problems)

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
            combined = rel if leaf is None else f"{rel}/{leaf}"
            target = _resolve_index_target(root, combined, label,
                                           problems)
            if target is None:
                continue
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
