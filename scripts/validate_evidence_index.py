#!/usr/bin/env python3
"""P5 evidence-index V1/V2 cross-root validator.

The audited v0 index (``retrieval/p5_evidence_index_v0.json``) stored
absolute host paths inside ``files[]``, carried stale digests, lacked
provenance fields, and had no automated check.  The V1 schema fixes
the shape — absolute paths are allowed ONLY under ``roots.<id>.path``;
every ``files[]`` entry is a ``(root_id, relpath)`` pair — and this
validator fails closed on schema violations, malformed digests, path
escapes, duplicates, self-reference, stale bytes, stale sidecars, and
incomplete provenance.

Usage::

    validate_evidence_index.py <index.json>
        [--root-map '<json object: root_id -> absolute path>']
        [--report-out <path>]

``--root-map`` overrides the on-disk locations recorded in
``roots.<id>.path`` (e.g. when the index was generated on another
host); it may be an inline JSON object or a path to a JSON file.

Emits ``{status, problems, files_checked, files_ok, ...}`` as JSON to
stdout (and to ``--report-out`` if given).  Exit 0 on INDEX_OK, 1 on
INDEX_FAIL.  V1 keeps its historical validator semantics; V2 is dispatched
to the exhaustive inventory validator.  READ-ONLY: the validator never
writes into evidence roots.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath

SCHEMA_V1 = "P5_EVIDENCE_INDEX_V1"
CLAIM_SCOPE_V1 = "research_only_no_operational_authorization"
STATES_V1 = frozenset({"current", "immutable", "superseded"})

SCHEMA_V2 = "P5_EVIDENCE_INDEX_V2"
CLAIM_SCOPE_V2 = CLAIM_SCOPE_V1
STATES_V2 = STATES_V1
SIDECAR_SUFFIX_V2 = ".sha256"
SIDECAR_EXCEPTION_CODE_V2 = "approved_missing_sidecar"

_HEX64 = re.compile(r"[0-9a-fA-F]{64}").fullmatch
_HEX40 = re.compile(r"[0-9a-fA-F]{40}").fullmatch
# Strict ISO-8601 UTC: trailing Z only — no offsets, no date-only,
# no space separator.
_ISO8601_Z = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z").fullmatch

REQUIRED_TOP_FIELDS = (
    "schema", "title", "generated_utc", "generator",
    "manifest_sha256", "roots", "files", "claim_scope",
)
REQUIRED_FILE_FIELDS = (
    "root_id", "relpath", "sha256", "size_bytes",
    "sidecar_sha256", "state", "activity", "entity_role",
)

REQUIRED_V2_TOP_FIELDS = (
    "schema", "title", "generated_utc", "generator",
    "manifest_sha256", "manifest", "content_head", "manifest_commit",
    "roots", "topology", "coverage_scope", "exclusions", "files",
    "final_verification", "claim_scope",
)
REQUIRED_V2_FILE_FIELDS = REQUIRED_FILE_FIELDS + ("sidecar_exception",)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_path(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_absolute_string(s: str) -> bool:
    """True if the WHOLE string parses as an absolute path on POSIX or
    Windows semantics."""
    if not s:
        return False
    return (s.startswith(("/", "\\"))
            or PurePosixPath(s).is_absolute()
            or PureWindowsPath(s).is_absolute())


def _iter_strings(obj):
    """Yield every string value nested anywhere in a JSON object."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _iter_strings(v)


def _check_relpath(relpath):
    """Return a problem string, or None if relpath is a clean relative
    path (no absolute form, no '..' escape, no NUL)."""
    if not isinstance(relpath, str) or not relpath.strip():
        return "relpath must be a non-empty string"
    if "\x00" in relpath:
        return "relpath contains a NUL byte"
    if _is_absolute_string(relpath):
        return (f"relpath {relpath!r} is an absolute path — absolute "
                f"paths are allowed only under roots.<id>.path")
    pp, wp = PurePosixPath(relpath), PureWindowsPath(relpath)
    if ".." in pp.parts or ".." in wp.parts:
        return f"relpath {relpath!r} escapes its root ('..' segment)"
    return None


def _check_generated_utc(value):
    """Strict ISO-8601 Z check.  Returns a problem string or None."""
    if not isinstance(value, str) or not _ISO8601_Z(value):
        return ("generated_utc must be strict ISO-8601 UTC "
                "(YYYY-MM-DDTHH:MM:SS[.ffffff]Z — trailing 'Z' required, "
                "no other offset)")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return f"generated_utc {value!r} does not parse as a real datetime"
    return None


def _report(status, problems, files_checked, files_ok, index_path,
            schema=None):
    return {
        "status": status,
        "index": str(index_path),
        "schema": schema,
        "problems": list(problems),
        "files_checked": files_checked,
        "files_ok": files_ok,
    }


def _resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


# ---------------------------------------------------------------------------
# V2 exhaustive inventory helpers
# ---------------------------------------------------------------------------

def _v2_report(status, problems, files_checked, files_ok, index_path,
               inventory_files=0, excluded_files=0, schema=SCHEMA_V2):
    """Return the stable report shape used by the V2 validator."""
    return {
        "status": status,
        "index": str(index_path),
        "schema": schema,
        "problems": list(problems),
        "files_checked": files_checked,
        "files_ok": files_ok,
        "inventory_files": inventory_files,
        "excluded_files": excluded_files,
    }


def _v2_root_kind(spec):
    raw = spec.get("kind", spec.get("root_kind", spec.get("type")))
    if isinstance(raw, str):
        value = raw.strip().lower()
        if value in {"logical", "logical_root", "partition"}:
            return "logical"
        if value in {"physical", "physical_root"}:
            return "physical"
    parent = spec.get("parent_root_id", spec.get("parent"))
    physical_id = spec.get("physical_root_id")
    prefix = spec.get("path_prefix",
                      spec.get("logical_relpath",
                               spec.get("partition_relpath")))
    if (parent not in (None, "")
            or (physical_id not in (None, "")
                and physical_id != spec.get("root_id"))
            or prefix not in (None, "")):
        return "logical"
    return "physical"


def _v2_root_specs(roots, root_map, problems):
    """Normalize V2 root declarations and CLI path overrides.

    The canonical V2 form is ``roots.<id> = {path, role, kind,
    physical_root_id, path_prefix}``.  A few descriptive aliases are
    accepted at the input boundary so a root mapping remains readable,
    but the validator always applies the same physical partition rules.
    """
    if not isinstance(roots, dict) or not roots:
        problems.append("roots must be a non-empty object")
        return {}

    if root_map is not None and not isinstance(root_map, dict):
        problems.append("--root-map must be a JSON object")
        root_map = {}
    if isinstance(root_map, dict):
        for rid in root_map:
            if rid not in roots:
                problems.append(
                    f"--root-map key {rid!r} is not a declared root_id")

    specs = {}
    for rid, raw in roots.items():
        ctx = f"roots.{rid}"
        if not isinstance(rid, str) or not rid.strip():
            problems.append("root ids must be non-empty strings")
            continue
        if not isinstance(raw, dict):
            problems.append(f"{ctx}: must be an object")
            continue

        override = root_map.get(rid) if isinstance(root_map, dict) else None
        if isinstance(override, dict):
            override = override.get("path")
        raw_path = override if override is not None else raw.get("path")
        if not (isinstance(raw_path, str) and raw_path.strip()):
            problems.append(f"{ctx}.path must be a non-empty string")
            raw_path = ""
        elif not _is_absolute_string(raw_path):
            problems.append(f"{ctx}.path must be absolute")

        role = raw.get("role")
        if not (isinstance(role, str) and role.strip()):
            problems.append(f"{ctx}.role must be a non-empty string")

        kind = _v2_root_kind(raw)
        physical_id = raw.get("physical_root_id")
        if physical_id is None:
            physical_id = raw.get("parent_root_id", raw.get("parent"))
        if kind == "physical":
            if physical_id not in (None, rid):
                problems.append(
                    f"{ctx}.physical_root_id must equal {rid!r} "
                    "for a physical root")
            physical_id = rid
        elif not (isinstance(physical_id, str) and physical_id.strip()):
            problems.append(
                f"{ctx}.physical_root_id is required for a logical root")
            physical_id = None

        prefix = raw.get("path_prefix")
        if prefix is None:
            prefix = raw.get("logical_relpath",
                            raw.get("partition_relpath"))
        if prefix is None:
            prefix = "" if kind == "physical" else None
        if prefix is not None and not isinstance(prefix, str):
            problems.append(f"{ctx}.path_prefix must be a string")
            prefix = None
        if isinstance(prefix, str) and prefix:
            prefix_error = _check_relpath(prefix)
            if prefix_error:
                problems.append(f"{ctx}.path_prefix: {prefix_error}")

        path = Path(raw_path).expanduser() if raw_path else Path(".")
        path_resolved = _resolve(path)
        if not path_resolved.is_dir():
            problems.append(
                f"root {rid!r} does not resolve to an existing directory: "
                f"{path_resolved}")
        specs[rid] = {
            "root_id": rid,
            "path": path,
            "path_resolved": path_resolved,
            "role": role,
            "kind": kind,
            "physical_root_id": physical_id,
            "path_prefix": prefix,
        }

    # A logical partition must be physically nested under its declared
    # physical root, and its prefix must agree with the resolved paths.
    for rid, spec in specs.items():
        if spec["kind"] != "logical":
            continue
        pid = spec["physical_root_id"]
        parent = specs.get(pid)
        if parent is None:
            problems.append(
                f"{rid}: physical_root_id {pid!r} is not declared")
            continue
        try:
            actual_prefix = spec["path_resolved"].relative_to(
                parent["path_resolved"]).as_posix()
        except ValueError:
            problems.append(
                f"{rid}: logical root is not nested under physical root "
                f"{pid!r}")
            continue
        if not actual_prefix:
            problems.append(
                f"{rid}: logical root must occupy a non-empty partition")
        if spec["path_prefix"] in (None, ""):
            spec["path_prefix"] = actual_prefix
        elif spec["path_prefix"] != actual_prefix:
            problems.append(
                f"{rid}: path_prefix {spec['path_prefix']!r} does not "
                f"match resolved partition {actual_prefix!r}")

    return specs


def _v2_validate_topology(doc, specs, problems):
    topology = doc.get("topology")
    if not isinstance(topology, dict):
        problems.append("topology must be a structured object")
        return
    topo_kind = topology.get("kind", topology.get("type"))
    if not (isinstance(topo_kind, str) and topo_kind.strip()):
        problems.append("topology.kind must be a non-empty string")

    partitions = topology.get("partitions")
    if not isinstance(partitions, list):
        partitions = topology.get("root_partitions")
    if not isinstance(partitions, list) or not partitions:
        problems.append("topology.partitions must be a non-empty list")
        partitions = []

    seen = set()
    for i, part in enumerate(partitions):
        ctx = f"topology.partitions[{i}]"
        if not isinstance(part, dict):
            problems.append(f"{ctx} must be an object")
            continue
        rid = part.get("root_id")
        if rid not in specs:
            problems.append(f"{ctx}.root_id {rid!r} is not declared")
            continue
        if rid in seen:
            problems.append(f"{ctx} duplicates root_id {rid!r}")
        seen.add(rid)
        spec = specs[rid]
        if part.get("kind", part.get("root_kind")) != spec["kind"]:
            problems.append(f"{ctx}.kind does not match roots.{rid}.kind")
        if part.get("physical_root_id") != spec["physical_root_id"]:
            problems.append(
                f"{ctx}.physical_root_id does not match roots.{rid}")
        if part.get("path_prefix", "") != (spec["path_prefix"] or ""):
            problems.append(f"{ctx}.path_prefix does not match roots.{rid}")
    missing = set(specs) - seen
    if missing:
        problems.append(
            "topology.partitions missing root_ids: "
            + ", ".join(sorted(missing)))

    physical_ids = topology.get("physical_roots")
    logical_ids = topology.get("logical_roots")
    if not isinstance(physical_ids, list):
        problems.append("topology.physical_roots must be a list")
    else:
        expected = sorted(r for r, s in specs.items()
                          if s["kind"] == "physical")
        if sorted(physical_ids) != expected:
            problems.append("topology.physical_roots does not match roots")
    if not isinstance(logical_ids, list):
        problems.append("topology.logical_roots must be a list")
    else:
        expected = sorted(r for r, s in specs.items()
                          if s["kind"] == "logical")
        if sorted(logical_ids) != expected:
            problems.append("topology.logical_roots does not match roots")


def _v2_validate_coverage_scope(doc, specs, problems):
    scope = doc.get("coverage_scope")
    if isinstance(scope, str):
        if scope != "all_non_sidecar_payload_files":
            problems.append(
                "coverage_scope must declare all non-sidecar payload files")
        return
    if not isinstance(scope, dict):
        problems.append("coverage_scope must be a structured object")
        return
    mode = scope.get("mode", scope.get("scope"))
    if mode not in {"exhaustive", "all_non_sidecar_payload_files"}:
        problems.append(
            "coverage_scope.mode must be exhaustive all-non-sidecar "
            "inventory")
    definition = scope.get("payload_definition")
    if definition != "all_non_sidecar_payload_files":
        problems.append(
            "coverage_scope.payload_definition must be "
            "all_non_sidecar_payload_files")
    if scope.get("sidecar_suffix") != SIDECAR_SUFFIX_V2:
        problems.append(
            f"coverage_scope.sidecar_suffix must be {SIDECAR_SUFFIX_V2!r}")
    root_ids = scope.get("root_ids")
    if not isinstance(root_ids, list) or sorted(root_ids) != sorted(specs):
        problems.append("coverage_scope.root_ids must list every declared root")


def _v2_physical_identity(path):
    try:
        stat = path.stat()
    except OSError:
        return ("path", str(_resolve(path)))
    return ("inode", stat.st_dev, stat.st_ino)


def _v2_iter_payload_files(root):
    """Yield deterministic regular files, excluding sha256 sidecars."""
    for directory, directories, filenames in os.walk(
            root, topdown=True, followlinks=False):
        directories.sort()
        filenames.sort()
        for name in filenames:
            if name.endswith(SIDECAR_SUFFIX_V2):
                continue
            path = Path(directory) / name
            if path.is_file():
                yield path


def _v2_inventory(specs, problems):
    """Build the physical inventory and reject ambiguous root overlaps."""
    for rid, spec in specs.items():
        for other_id, other in specs.items():
            if rid >= other_id:
                continue
            left, right = spec["path_resolved"], other["path_resolved"]
            if left == right:
                problems.append(
                    f"roots {rid!r} and {other_id!r} resolve to the same "
                    "physical directory")
                continue
            nested = None
            try:
                left.relative_to(right)
                nested = (rid, other_id)
            except ValueError:
                try:
                    right.relative_to(left)
                    nested = (other_id, rid)
                except ValueError:
                    pass
            if nested:
                child_id, parent_id = nested
                child, parent = specs[child_id], specs[parent_id]
                if (child["kind"] != "logical"
                        or child["physical_root_id"] !=
                        parent["physical_root_id"]):
                    problems.append(
                        f"overlapping roots {parent_id!r} and {child_id!r} "
                        "require an explicit logical partition")

    inventory = {}
    physical_to_key = {}
    for scan_id, scan_spec in specs.items():
        base = scan_spec["path_resolved"]
        if not base.is_dir():
            continue
        for path in _v2_iter_payload_files(base):
            resolved = _resolve(path)
            try:
                resolved.relative_to(base)
            except ValueError:
                problems.append(
                    f"payload {path} resolves outside root {scan_id!r}")
                continue

            candidates = []
            for rid, candidate in specs.items():
                try:
                    resolved.relative_to(candidate["path_resolved"])
                except ValueError:
                    continue
                candidates.append(rid)
            if not candidates:
                problems.append(f"payload {path} has no logical root")
                continue
            deepest = max(len(specs[r]["path_resolved"].parts)
                          for r in candidates)
            best = [r for r in candidates
                    if len(specs[r]["path_resolved"].parts) == deepest]
            if len(best) != 1:
                problems.append(
                    f"payload {path} has duplicate logical root assignment: "
                    + ", ".join(sorted(best)))
                continue
            rid = best[0]
            candidate_base = specs[rid]["path_resolved"]
            try:
                relpath = path.relative_to(candidate_base).as_posix()
            except ValueError:
                relpath = resolved.relative_to(candidate_base).as_posix()
            key = (rid, relpath)
            physical = _v2_physical_identity(resolved)
            old = inventory.get(key)
            if old is not None:
                if old["physical"] != physical:
                    problems.append(
                        f"logical path {key!r} maps to different physical "
                        "bytes")
                continue
            previous = physical_to_key.get(physical)
            if previous is not None and previous != key:
                problems.append(
                    f"duplicate physical-byte assignment: {previous!r} "
                    f"and {key!r}")
                continue
            inventory[key] = {"path": path, "resolved": resolved,
                              "physical": physical}
            physical_to_key[physical] = key
    return inventory


def _v2_entry_path(specs, rid, relpath, problems, context):
    spec = specs.get(rid)
    if spec is None:
        problems.append(f"{context}: root_id {rid!r} is not declared")
        return None, None
    rel_error = _check_relpath(relpath)
    if rel_error:
        problems.append(f"{context}: {rel_error}")
        return None, None
    path = spec["path_resolved"] / relpath
    resolved = _resolve(path)
    try:
        resolved.relative_to(spec["path_resolved"])
    except ValueError:
        problems.append(
            f"{context}: relpath {relpath!r} resolves outside root {rid!r}")
    return path, resolved


def _v2_is_self_reference(path, resolved, index_resolved):
    if resolved == index_resolved:
        return True
    if path.name != index_resolved.name or not path.is_file():
        return False
    try:
        return path.samefile(index_resolved)
    except OSError:
        return False


def _v2_validate_sidecar(entry, payload, sha, sidecar_sha, context,
                         problems):
    exception = entry.get("sidecar_exception")
    if exception is None:
        if not (isinstance(sidecar_sha, str) and _HEX64(sidecar_sha)):
            problems.append(f"{context}: sidecar_sha256 is required")
            return
        sidecar = Path(str(payload) + SIDECAR_SUFFIX_V2)
        if not sidecar.is_file():
            problems.append(f"{context}: sidecar missing on disk: {sidecar}")
            return
        sidecar_bytes = sidecar.read_bytes()
        if _sha256_bytes(sidecar_bytes) != sidecar_sha:
            problems.append(
                f"{context}: sidecar_sha256 stale for {sidecar.name}")
        tokens = sidecar_bytes.split()
        first = (tokens[0].decode("utf-8", "replace") if tokens else "")
        if first != sha:
            problems.append(
                f"{context}: sidecar payload first token does not equal "
                "the file sha256")
        return

    if not isinstance(exception, dict):
        problems.append(
            f"{context}: sidecar_exception must be an object or null")
        return
    code = exception.get("code")
    if code != SIDECAR_EXCEPTION_CODE_V2:
        problems.append(
            f"{context}: sidecar_exception.code must be "
            f"{SIDECAR_EXCEPTION_CODE_V2!r}")
    for field in ("reason", "authority"):
        if not (isinstance(exception.get(field), str)
                and exception[field].strip()):
            problems.append(
                f"{context}: sidecar_exception.{field} must be a "
                "non-empty string")
    if sidecar_sha is not None:
        problems.append(
            f"{context}: sidecar_sha256 must be null for an explicit "
            "sidecar exception")
    if Path(str(payload) + SIDECAR_SUFFIX_V2).exists():
        problems.append(
            f"{context}: sidecar exception is invalid while a sidecar "
            "exists")


def _v2_validate_manifest(doc, problems):
    manifest_sha = doc.get("manifest_sha256")
    if not (isinstance(manifest_sha, str) and _HEX64(manifest_sha)):
        problems.append("manifest_sha256 must be 64 hex chars")
    manifest = doc.get("manifest")
    if not isinstance(manifest, dict):
        problems.append("manifest must be a structured object")
        return
    relpath = manifest.get("relpath")
    rel_error = _check_relpath(relpath)
    if rel_error:
        problems.append(f"manifest.relpath: {rel_error}")
    nested_sha = manifest.get("sha256")
    if not (isinstance(nested_sha, str) and _HEX64(nested_sha)):
        problems.append("manifest.sha256 must be 64 hex chars")
    elif isinstance(manifest_sha, str) and nested_sha != manifest_sha:
        problems.append("manifest.sha256 does not match manifest_sha256")
    for field in ("content_head", "manifest_commit"):
        value = manifest.get(field)
        if not (isinstance(value, str) and _HEX40(value)):
            problems.append(f"manifest.{field} must be 40 hex chars")
    content_head = manifest.get("content_head")
    manifest_commit = manifest.get("manifest_commit")
    if (isinstance(content_head, str) and _HEX40(content_head)
            and isinstance(manifest_commit, str) and _HEX40(manifest_commit)
            and content_head != manifest_commit):
        problems.append(
            "manifest_commit must equal content_head for a closed index")
    for field in ("content_head", "manifest_commit"):
        if doc.get(field) != manifest.get(field):
            problems.append(f"top-level {field} does not match manifest.{field}")


def _v2_validate_supersedes(doc, specs, index_resolved, problems):
    """Verify an optional logical, relative supersedes pointer."""
    supersedes = doc.get("supersedes")
    if supersedes is None:
        return
    if not isinstance(supersedes, dict):
        problems.append("supersedes must be an object or null")
        return
    relpath = supersedes.get("relpath")
    if not isinstance(relpath, str) or not relpath.strip():
        problems.append("supersedes.relpath must be a non-empty string")
        return
    if _is_absolute_string(relpath):
        problems.append(
            "supersedes.relpath must be relative/logical; absolute paths "
            "are forbidden in V2")
        return
    rel_error = _check_relpath(relpath)
    if rel_error:
        problems.append(f"supersedes.relpath: {rel_error}")
        return
    sha = supersedes.get("sha256")
    if not (isinstance(sha, str) and _HEX64(sha)):
        problems.append("supersedes.sha256 must be 64 hex chars")
        return

    root_id = supersedes.get("root_id")
    candidates = []
    if root_id is not None:
        if root_id not in specs:
            problems.append(
                f"supersedes.root_id {root_id!r} is not declared")
            return
        candidates.append(specs[root_id]["path_resolved"] / relpath)
    else:
        # Root-less logical pointers are accepted only when a unique target
        # can be found alongside the index or under one declared root.
        candidates.append(index_resolved.parent / relpath)
        candidates.extend(spec["path_resolved"] / relpath
                          for spec in specs.values())
    targets = [path for path in candidates if path.is_file()]
    if not targets:
        problems.append(f"supersedes target not found on disk: {relpath!r}")
        return
    target = targets[0]
    if len({str(_resolve(path)) for path in targets}) > 1:
        problems.append(
            f"supersedes.relpath {relpath!r} resolves to multiple targets")
        return
    if _resolve(target) == index_resolved:
        problems.append("supersedes target is the index itself")
    if _sha256_path(target) != sha:
        problems.append(
            f"supersedes.sha256 does not match {relpath!r} on disk")


def _v2_validate_final_verification(doc, inventory_count, included_count,
                                    excluded_count, problems, *,
                                    has_planned=False,
                                    has_planned_absent=False):
    final = doc.get("final_verification")
    if not isinstance(final, dict):
        problems.append("final_verification must be a structured object")
        return
    allowed = {"CLOSED"}
    if has_planned:
        allowed.add("CLOSURE_PENDING")
    if has_planned_absent:
        allowed = {"CLOSURE_PENDING"}
    if final.get("status") not in allowed:
        problems.append("final_verification.status must be one of "
                        + ", ".join(sorted(allowed)))
    verified = final.get("verified_utc")
    if _check_generated_utc(verified):
        problems.append(
            "final_verification.verified_utc must be strict ISO-8601 UTC")
    closure = final.get("closure")
    if not isinstance(closure, dict):
        problems.append("final_verification.closure must be an object")
    else:
        if closure.get("status") not in allowed:
            problems.append(
                "final_verification.closure.status must be one of "
                + ", ".join(sorted(allowed)))
        for field in (
                "inventory_coverage", "sidecar_validation", "exclusions",
                "duplicate_physical_assignment",
                "manifest_head_consistency", "no_self_reference"):
            if closure.get(field) != "PASS":
                problems.append(
                    f"final_verification.closure.{field} must be 'PASS'")
    counts = final.get("counts")
    if not isinstance(counts, dict):
        problems.append("final_verification.counts must be an object")
        return
    expected = {
        "payload_files": inventory_count,
        "included_files": included_count,
        "excluded_files": excluded_count,
    }
    for field, value in expected.items():
        actual = counts.get(field)
        if (not isinstance(actual, int) or isinstance(actual, bool)
                or actual != value):
            problems.append(
                f"final_verification.counts.{field} does not match "
                f"observed value {value}")


def _validate_v2_document(index_path, doc, root_map=None):
    problems = []
    files_checked = 0
    files_ok = 0
    index_path = Path(index_path)
    index_resolved = _resolve(index_path)

    if doc.get("schema") != SCHEMA_V2:
        problems.append(
            f"schema must be {SCHEMA_V2!r} (got {doc.get('schema')!r})")
    for field in REQUIRED_V2_TOP_FIELDS:
        if field not in doc:
            problems.append(f"missing required top-level field {field!r}")
    if not (isinstance(doc.get("title"), str)
            and doc["title"].strip()):
        problems.append("title must be a non-empty string")
    if doc.get("claim_scope") != CLAIM_SCOPE_V2:
        problems.append(
            f"claim_scope must be {CLAIM_SCOPE_V2!r} "
            f"(got {doc.get('claim_scope')!r})")

    generator = doc.get("generator")
    if not isinstance(generator, dict):
        problems.append("generator must be an object {agent, repo_commit}")
    else:
        if not (isinstance(generator.get("agent"), str)
                and generator["agent"].strip()):
            problems.append("generator.agent must be a non-empty string")
        if not (isinstance(generator.get("repo_commit"), str)
                and _HEX40(generator["repo_commit"])):
            problems.append("generator.repo_commit must be 40 hex chars")
    if "generated_utc" in doc:
        err = _check_generated_utc(doc["generated_utc"])
        if err:
            problems.append(err)
    _v2_validate_manifest(doc, problems)

    roots = doc.get("roots")
    specs = _v2_root_specs(roots, root_map, problems)
    _v2_validate_topology(doc, specs, problems)
    _v2_validate_coverage_scope(doc, specs, problems)
    _v2_validate_supersedes(doc, specs, index_resolved, problems)
    inventory = _v2_inventory(specs, problems)

    files = doc.get("files")
    listed_keys = set()
    listed_physical = {}
    if not isinstance(files, list):
        problems.append("files must be a list")
        files = []
    for i, entry in enumerate(files):
        ctx = f"files[{i}]"
        if not isinstance(entry, dict):
            problems.append(f"{ctx}: entry must be a JSON object")
            continue
        files_checked += 1
        entry_problems = []
        for field in REQUIRED_V2_FILE_FIELDS:
            if field not in entry:
                entry_problems.append(f"missing field {field!r}")
        for value in _iter_strings(entry):
            if _is_absolute_string(value):
                entry_problems.append(
                    f"absolute path {value!r} is forbidden under files[]")
                break

        rid = entry.get("root_id")
        relpath = entry.get("relpath")
        rel_error = _check_relpath(relpath)
        if rel_error:
            entry_problems.append(rel_error)
        if not isinstance(rid, str) or rid not in specs:
            entry_problems.append(f"root_id {rid!r} is not declared in roots")
        key = (rid, relpath)
        if isinstance(rid, str) and isinstance(relpath, str):
            if key in listed_keys:
                entry_problems.append(
                    f"duplicate logical assignment {key!r}")
            listed_keys.add(key)

        sha = entry.get("sha256")
        sha_ok = isinstance(sha, str) and bool(_HEX64(sha))
        if not sha_ok:
            entry_problems.append("sha256 must be exactly 64 hex chars")
        size = entry.get("size_bytes")
        size_ok = (isinstance(size, int) and not isinstance(size, bool)
                   and size >= 0)
        if not size_ok:
            entry_problems.append("size_bytes must be a non-negative integer")
        if entry.get("state") not in STATES_V2:
            entry_problems.append(
                f"state {entry.get('state')!r} not in vocabulary "
                f"{sorted(STATES_V2)}")
        for field in ("activity", "entity_role"):
            if not (isinstance(entry.get(field), str)
                    and entry[field].strip()):
                entry_problems.append(
                    f"{field} must be a non-empty string")

        payload, resolved = (None, None)
        if not rel_error and isinstance(rid, str) and rid in specs:
            payload, resolved = _v2_entry_path(
                specs, rid, relpath, entry_problems, ctx)
            if payload is not None and _v2_is_self_reference(
                    payload, resolved, index_resolved):
                entry_problems.append(
                    "index lists itself (self-reference rejected)")
            if payload is not None and not payload.is_file():
                entry_problems.append(f"file missing on disk: {payload}")
            if payload is not None and payload.is_file() and sha_ok:
                disk_sha = _sha256_path(payload)
                if disk_sha != sha:
                    entry_problems.append(
                        f"sha256 stale: index={sha[:16]}… "
                        f"disk={disk_sha[:16]}…")
                if size_ok and payload.stat().st_size != size:
                    entry_problems.append(
                        f"size_bytes stale: index={size} "
                        f"disk={payload.stat().st_size}")
                _v2_validate_sidecar(
                    entry, payload, sha, entry.get("sidecar_sha256"),
                    ctx, entry_problems)
                physical = _v2_physical_identity(resolved)
                previous = listed_physical.get(physical)
                if previous is not None and previous != key:
                    entry_problems.append(
                        "duplicate physical-byte assignment: "
                        f"{previous!r} and {key!r}")
                else:
                    listed_physical[physical] = key
                if key not in inventory:
                    entry_problems.append(
                        f"logical assignment {key!r} is not in the "
                        "deterministic inventory")
        problems.extend(f"{ctx}: {message}" for message in entry_problems)
        if not entry_problems:
            files_ok += 1

    exclusions = doc.get("exclusions")
    if not isinstance(exclusions, list):
        problems.append("exclusions must be a list of structured objects")
        exclusions = []
    excluded_keys = set()
    excluded_physical = {}
    exclusion_states = {}
    has_planned = False
    has_planned_absent = False
    for i, exclusion in enumerate(exclusions):
        ctx = f"exclusions[{i}]"
        if not isinstance(exclusion, dict):
            problems.append(f"{ctx}: exclusion must be an object")
            continue
        rid, relpath = exclusion.get("root_id"), exclusion.get("relpath")
        reason = exclusion.get("reason")
        if not (isinstance(reason, str) and reason.strip()):
            problems.append(f"{ctx}.reason must be a non-empty string")
        state = exclusion.get("state", "present")
        if state not in ("planned", "present"):
            problems.append(
                f"{ctx}.state must be 'planned' or 'present' "
                f"(got {state!r})")
            state = "present"
        owner = exclusion.get("owner")
        if state == "planned" and not (isinstance(owner, str)
                                       and owner.strip()):
            problems.append(
                f"{ctx}.owner is required for planned exclusions")
        if not isinstance(rid, str) or rid not in specs:
            problems.append(f"{ctx}.root_id {rid!r} is not declared")
            continue
        path, resolved = _v2_entry_path(
            specs, rid, relpath, problems, ctx)
        key = (rid, relpath)
        if key in excluded_keys:
            problems.append(f"{ctx}: duplicate exclusion {key!r}")
        excluded_keys.add(key)
        exclusion_states[key] = state
        if state == "planned":
            has_planned = True
            if key not in inventory:
                has_planned_absent = True
        if key in listed_keys:
            problems.append(
                f"{ctx}: payload is both included and explicitly excluded")
        if state == "present" and key not in inventory:
            problems.append(
                f"{ctx}: exclusion is not an inventory payload {key!r}")
        if path is not None and path.name.endswith(SIDECAR_SUFFIX_V2):
            problems.append(f"{ctx}: exclusions target payloads, not sidecars")
        if path is not None and path.is_file() and resolved is not None:
            physical = _v2_physical_identity(resolved)
            previous = excluded_physical.get(physical)
            if previous is not None and previous != key:
                problems.append(
                    "duplicate physical-byte assignment in exclusions: "
                    f"{previous!r} and {key!r}")
            elif physical in listed_physical:
                problems.append(
                    "duplicate physical-byte assignment between included "
                    f"and excluded payloads: {listed_physical[physical]!r} "
                    f"and {key!r}")
            else:
                excluded_physical[physical] = key

    missing = sorted(set(inventory) - listed_keys - excluded_keys)
    if missing:
        problems.append(
            "inventory coverage missing payloads: "
            + ", ".join(f"{rid}:{rel}" for rid, rel in missing))
    planned_absent = {key for key, state in exclusion_states.items()
                      if state == "planned" and key not in inventory}
    extra = sorted((listed_keys | excluded_keys) - set(inventory)
                   - planned_absent)
    if extra:
        problems.append(
            "inventory assignments not present on disk: "
            + ", ".join(f"{rid}:{rel}" for rid, rel in extra))

    _v2_validate_final_verification(
        doc, len(inventory) + len(planned_absent), len(files),
        len(exclusions), problems,
        has_planned=has_planned, has_planned_absent=has_planned_absent)
    status = "INDEX_OK" if not problems else "INDEX_FAIL"
    return _v2_report(status, problems, files_checked, files_ok,
                      index_path, inventory_files=len(inventory),
                      excluded_files=len(exclusions))


def validate_index(index_path, root_map=None):
    """Validate a V1 index or dispatch a V2 index.  Returns the report dict.

    ``root_map``: optional {root_id: absolute path} override for live
    verification; when None, ``roots.<id>.path`` is used.
    """
    problems = []
    files_checked = 0
    files_ok = 0
    index_path = Path(index_path)
    index_resolved = _resolve(index_path)

    try:
        doc = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        return _report("INDEX_FAIL", [f"index unreadable: {exc}"],
                       0, 0, index_path)
    except json.JSONDecodeError as exc:
        return _report("INDEX_FAIL", [f"index is not valid JSON: {exc}"],
                       0, 0, index_path)
    if not isinstance(doc, dict):
        return _report("INDEX_FAIL",
                       ["index root must be a JSON object"],
                       0, 0, index_path)

    # V2 is intentionally dispatched to its own exhaustive validator.  A
    # V1 document continues through the historical validator below and is
    # never silently upgraded to an inventory claim.
    if doc.get("schema") == SCHEMA_V2:
        return _validate_v2_document(index_path, doc, root_map=root_map)

    # ---- 1. schema + required top-level fields --------------------
    if doc.get("schema") != SCHEMA_V1:
        problems.append(
            f"schema must be {SCHEMA_V1!r} (got {doc.get('schema')!r})")
    for field in REQUIRED_TOP_FIELDS:
        if field not in doc:
            problems.append(f"missing required top-level field {field!r}")
    if "title" in doc and not (isinstance(doc["title"], str)
                               and doc["title"].strip()):
        problems.append("title must be a non-empty string")
    if "claim_scope" in doc and doc.get("claim_scope") != CLAIM_SCOPE_V1:
        problems.append(f"claim_scope must be {CLAIM_SCOPE_V1!r} "
                        f"(got {doc.get('claim_scope')!r})")

    # ---- 5. provenance completeness --------------------------------
    if "generator" in doc:
        gen = doc["generator"]
        if not isinstance(gen, dict):
            problems.append("generator must be an object "
                            "{agent, repo_commit}")
        else:
            if not (isinstance(gen.get("agent"), str)
                    and gen["agent"].strip()):
                problems.append("generator.agent must be a non-empty string")
            if not (isinstance(gen.get("repo_commit"), str)
                    and _HEX40(gen["repo_commit"])):
                problems.append("generator.repo_commit must be 40 hex chars")
    if "generated_utc" in doc:
        err = _check_generated_utc(doc["generated_utc"])
        if err:
            problems.append(err)
    if "manifest_sha256" in doc:
        if not (isinstance(doc["manifest_sha256"], str)
                and _HEX64(doc["manifest_sha256"])):
            problems.append("manifest_sha256 must be 64 hex chars")

    # ---- roots ----------------------------------------------------
    roots = doc.get("roots")
    declared_roots = set()
    if not isinstance(roots, dict) or not roots:
        problems.append("roots must be a non-empty object "
                        "{root_id: {path, role}}")
        roots = {}
    for rid, spec in roots.items():
        declared_roots.add(rid)
        if not isinstance(spec, dict):
            problems.append(f"roots.{rid}: must be an object "
                            f"{{path, role}}")
            continue
        rp = spec.get("path")
        if not (isinstance(rp, str) and rp.strip()):
            problems.append(f"roots.{rid}.path must be a non-empty string")
        elif not _is_absolute_string(rp):
            problems.append(f"roots.{rid}.path must be an absolute path "
                            f"(got {rp!r})")
        if not (isinstance(spec.get("role"), str)
                and spec["role"].strip()):
            problems.append(f"roots.{rid}.role must be a non-empty string")

    # ---- 3. effective root map + resolve check ---------------------
    effective = {rid: Path(spec["path"])
                 for rid, spec in roots.items()
                 if isinstance(spec, dict)
                 and isinstance(spec.get("path"), str) and spec["path"]}
    if root_map is not None:
        if not isinstance(root_map, dict):
            problems.append("--root-map must be a JSON object "
                            "{root_id: absolute path}")
        else:
            for rid, p in root_map.items():
                if rid not in declared_roots:
                    problems.append(f"--root-map key {rid!r} is not a "
                                    f"declared root_id")
                elif not (isinstance(p, str) and _is_absolute_string(p)):
                    problems.append(f"--root-map[{rid!r}] must be an "
                                    f"absolute path string")
                else:
                    effective[rid] = Path(p)
    resolved_roots = {}
    for rid, p in effective.items():
        rp = _resolve(p.expanduser())
        resolved_roots[rid] = rp
        if not rp.is_dir():
            problems.append(f"root {rid!r} does not resolve to an "
                            f"existing directory: {rp}")

    # ---- 2 + 4. file entries ----------------------------------------
    files = doc.get("files")
    if not isinstance(files, list):
        problems.append("files must be a list")
    else:
        seen = set()
        for i, entry in enumerate(files):
            ctx = f"files[{i}]"
            if not isinstance(entry, dict):
                problems.append(f"{ctx}: entry must be a JSON object")
                continue
            files_checked += 1
            entry_problems = []

            def bad(msg, _l=entry_problems):
                _l.append(msg)

            for field in REQUIRED_FILE_FIELDS:
                if field not in entry:
                    bad(f"missing field {field!r}")

            # No absolute paths ANYWHERE under files[] — the only
            # permitted absolute path in the document is roots.*.path.
            for s in _iter_strings(entry):
                if _is_absolute_string(s):
                    bad(f"absolute path {s!r} is forbidden under files[]")
                    break

            rid = entry.get("root_id")
            relpath = entry.get("relpath")
            rp_err = (_check_relpath(relpath)
                      if "relpath" in entry else None)
            if rp_err:
                bad(rp_err)
            if "root_id" in entry:
                if not isinstance(rid, str) or rid not in declared_roots:
                    bad(f"root_id {rid!r} is not declared in roots")

            sha = entry.get("sha256")
            sha_ok = (isinstance(sha, str) and bool(_HEX64(sha)))
            if "sha256" in entry and not sha_ok:
                bad("sha256 must be exactly 64 hex chars")

            sb = entry.get("size_bytes")
            sb_ok = (isinstance(sb, int) and not isinstance(sb, bool)
                     and sb >= 0)
            if "size_bytes" in entry and not sb_ok:
                bad("size_bytes must be a non-negative integer")

            sc = entry.get("sidecar_sha256")
            sc_ok = (sc is None
                     or (isinstance(sc, str) and bool(_HEX64(sc))))
            if "sidecar_sha256" in entry and not sc_ok:
                bad("sidecar_sha256 must be 64 hex chars or null")

            st = entry.get("state")
            if "state" in entry and st not in STATES_V1:
                bad(f"state {st!r} not in vocabulary "
                    f"{sorted(STATES_V1)}")

            if "activity" in entry:
                act = entry["activity"]
                if not (isinstance(act, str) and act.strip()):
                    bad("activity must be a non-empty string")
            if "entity_role" in entry:
                er = entry["entity_role"]
                if not (isinstance(er, str) and er.strip()):
                    bad("entity_role must be a non-empty string")

            # Duplicate logical path.
            if isinstance(rid, str) and isinstance(relpath, str):
                key = (rid, relpath)
                if key in seen:
                    bad(f"duplicate (root_id, relpath) pair {key!r}")
                seen.add(key)

            # Live verification — only when root_id and relpath are
            # structurally sane enough to resolve a target.
            if (isinstance(rid, str) and rid in resolved_roots
                    and rp_err is None):
                base = resolved_roots[rid]
                cand = base / relpath
                cand_r = _resolve(cand)
                try:
                    cand_r.relative_to(base)
                except ValueError:
                    bad(f"relpath {relpath!r} resolves outside root "
                        f"{rid!r}")
                # Self-reference: resolved path equality, plus samefile
                # (symlinks/hardlinks) when the basename matches.
                is_self = (cand_r == index_resolved)
                if not is_self and cand_r.name == index_resolved.name:
                    try:
                        is_self = cand_r.samefile(index_resolved)
                    except OSError:
                        pass
                if is_self:
                    bad("index lists itself (self-reference rejected)")
                if not cand.is_file():
                    bad(f"file missing on disk: {cand}")
                else:
                    disk_sha = _sha256_path(cand)
                    if sha_ok and disk_sha != sha:
                        bad(f"sha256 stale: index={sha[:16]}… "
                            f"disk={disk_sha[:16]}…")
                    disk_size = cand.stat().st_size
                    if sb_ok and disk_size != sb:
                        bad(f"size_bytes stale: index={sb} "
                            f"disk={disk_size}")
                # Sidecar verification: relpath + ".sha256", digest
                # recomputes, and payload's first whitespace token is
                # the file's sha256 (sha256sum format).
                if sc is not None and isinstance(sc, str) and _HEX64(sc):
                    scp = base / (relpath + ".sha256")
                    if not scp.is_file():
                        bad(f"sidecar missing on disk: {scp}")
                    else:
                        sc_bytes = scp.read_bytes()
                        if _sha256_bytes(sc_bytes) != sc:
                            bad(f"sidecar_sha256 stale for {scp.name}")
                        tokens = sc_bytes.split()
                        first = (tokens[0].decode("utf-8", "replace")
                                 if tokens else "")
                        if sha_ok and first != sha:
                            bad("sidecar payload first token does not "
                                "equal the file sha256")

            problems.extend(f"{ctx}: {m}" for m in entry_problems)
            if not entry_problems:
                files_ok += 1

    # ---- 4. supersedes digest ---------------------------------------
    sup = doc.get("supersedes")
    if sup is not None:
        if not isinstance(sup, dict):
            problems.append("supersedes must be an object "
                            "{relpath, sha256}")
        else:
            srel = sup.get("relpath")
            ssha = sup.get("sha256")
            sup_ok = True
            if not (isinstance(srel, str) and srel.strip()):
                problems.append("supersedes.relpath must be a "
                                "non-empty string")
                sup_ok = False
            if not (isinstance(ssha, str) and _HEX64(ssha)):
                problems.append("supersedes.sha256 must be 64 hex chars")
                sup_ok = False
            if sup_ok:
                if _is_absolute_string(srel):
                    candidates = [Path(srel)]
                elif (".." in PurePosixPath(srel).parts
                      or ".." in PureWindowsPath(srel).parts):
                    problems.append("supersedes.relpath must not "
                                    "contain '..'")
                    candidates = []
                else:
                    # Resolution order: alongside the index first,
                    # then each declared root.
                    candidates = [index_resolved.parent / srel]
                    candidates += [resolved_roots[r] / srel
                                   for r in resolved_roots]
                if candidates:
                    target = next((c for c in candidates if c.is_file()),
                                  None)
                    if target is None:
                        problems.append(f"supersedes target not found "
                                        f"on disk: {srel!r}")
                    elif _sha256_path(target) != ssha:
                        problems.append(f"supersedes.sha256 does not "
                                        f"match {srel!r} on disk")

    status = "INDEX_OK" if not problems else "INDEX_FAIL"
    return _report(status, problems, files_checked, files_ok,
                   index_path, schema=doc.get("schema"))


def validate_index_v2(index_path, root_map=None):
    """Explicit V2 entry point; V1 remains available through validate_index."""
    return validate_index(index_path, root_map=root_map)


def _load_root_map(raw: str):
    """--root-map accepts an inline JSON object or a path to a JSON
    file containing one."""
    try:
        return json.loads(raw), None
    except json.JSONDecodeError:
        pass
    p = Path(raw)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8")), None
        except (OSError, json.JSONDecodeError) as exc:
            return None, f"--root-map file unreadable: {exc}"
    return None, "--root-map is neither a JSON object nor a readable file"


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Validate a P5 evidence index V1 or V2 (read-only; "
                    "JSON report to stdout).")
    ap.add_argument("index", help="path to the index JSON file")
    ap.add_argument("--root-map", default=None,
                    help="JSON object (or path to JSON file) mapping "
                         "root_id -> absolute path, overriding "
                         "roots.<id>.path for live verification")
    ap.add_argument("--report-out", default=None,
                    help="optional path to also write the JSON report")
    args = ap.parse_args(argv)

    root_map = None
    if args.root_map is not None:
        root_map, err = _load_root_map(args.root_map)
        if err is not None:
            report = _report("INDEX_FAIL", [err], 0, 0, args.index)
            text = json.dumps(report, indent=2, sort_keys=True)
            print(text)
            return 1

    report = validate_index(args.index, root_map=root_map)
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.report_out:
        Path(args.report_out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["status"] == "INDEX_OK" else 1


if __name__ == "__main__":
    sys.exit(main())
