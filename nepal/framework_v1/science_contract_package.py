"""Science-contract package builder/verifier (OPS-*).

Builds a fresh external, research-only package that aggregates the T2
contract/scaffold evidence: run context, a synthetic MEC fixture envelope,
the explicitly blocked FMX envelope, the T2S validation scaffold, a
no-claims register, a package index with per-file SHA-256, a research
handoff, and an atomic checkpoint.

Safety properties:

* writes only inside the caller-supplied package directory — with one
  documented exception: when ``evidence_root`` is supplied and the
  package resolves under it, a single ``active_generation.json``
  pointer is written at that root (PKG-09); never copies candidate
  data, ranked payloads, or any matrix bytes;
* every envelope carries ``research_diagnostic_only=true``,
  ``promotion_eligible=false``, ``production_authorized=false``,
  ``warning_path_authorized=false``;
* package status is ``SCIENCE_CONTRACT_SCAFFOLD_READY_WITH_FMX_BLOCKED`` —
  never a bare READY;
* checkpoint distinguishes RUNNING / INCOMPLETE / BLOCKED / PASS; an
  interrupted build never verifies as complete;
* all writes are atomic (temp file + rename) and a disk reserve of
  ``MIN_FREE_GIB`` is enforced before writing;
* :func:`verify_science_contract_package` re-reads every file from disk,
  recomputes hashes, and runs the research claim linter on each JSON doc;
* the dirty-main feature matrix is never accessed — this module takes no
  matrix path parameter at all.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Optional

from .feature_matrix_contract import (FEATURE_CONTRACT_SHA256_FIELD,
                                      FMX_BLOCKED_PENDING_EXPLICIT_FREEZE,
                                      build_fmx_envelope,
                                      verify_fmx_envelope)
from .multi_event_contract import (CANONICAL_ROW_SERIALIZATION,
                                   build_mec_envelope)
from .provenance import (bind_artifact_envelope, sha256_canonical,
                         sha256_file, verify_artifact_envelope)
from .research_boundaries import lint_research_claims
from .validation_scaffold import (BLOCKED_PENDING_FMX,
                                  build_scaffold_envelope,
                                  verify_scaffold_envelope)

SEAL_TYPE = "SCIENCE_CONTRACT_SEAL_V1"
POINTER_TYPE = "ACTIVE_GENERATION_POINTER_V1"
POINTER_NAME = "active_generation.json"
_MANDATORY_FLAG_FIELDS = ("research_diagnostic_only",
                          "promotion_eligible", "production_authorized",
                          "warning_path_authorized")

PACKAGE_STATUS = "SCIENCE_CONTRACT_SCAFFOLD_READY_WITH_FMX_BLOCKED"
PACKAGE_INDEX_TYPE = "SCIENCE_CONTRACT_PACKAGE_INDEX_V1"
HANDOFF_TYPE = "RESEARCH_CONTRACT_HANDOFF_V1"
MIN_FREE_GIB = 8.0
CHECKPOINT_STATES = ("RUNNING", "INCOMPLETE", "BLOCKED", "PASS")

# PKG-12: run-identity formats — a generation id is a portable token;
# a code revision is lowercase hex (short or full commit hash).
_GENERATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
_CODE_REVISION_RE = re.compile(r"^[0-9a-f]{7,64}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# PKG-11: run_context.b_evidence_binding values.  A bound B evidence
# file (run_context.b_evidence = {relative_path, sha256}) is the only
# form that authorizes FILE_BOUND; ranked digests with no bound file
# must carry exactly UNBOUND_INFORMATIONAL.
B_EVIDENCE_BOUND = "FILE_BOUND"
B_EVIDENCE_UNBOUND = "UNBOUND_INFORMATIONAL"

# PKG-13: run_context.json is canonical for these fields; every other
# package doc declaring one must carry the identical value.
_CANONICAL_IDENTITY_FIELDS = ("candidate_generation_id",
                              "code_revision", "package_status",
                              "b_status")

#: Subtrees that record *declared* (unverified, digest-nulled) foreign
#: metadata rather than this package's identity — exempt from the
#: canonical identity checks (the blocked-FMX fixture deliberately
#: names a synthetic generation).
_DECLARED_METADATA_KEYS = frozenset({"declared_matrix_metadata"})

# HANDOFF-01: a package at this stage must remain residual-positive;
# no document may claim the register is closed.
_GAP_CLOSED_PHRASES = ("all gaps closed", "no remaining gaps")
_NORMALIZE_SEP_RE = re.compile(r"[_-]+")
_WHITESPACE_RE = re.compile(r"\s+")
_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")

_REQUIRED_FILES = ("run_context.json", "mec_schema.json",
                   "fmx_blocked.json", "validation_scaffold.json",
                   "research_no_claims.json",
                   "science_contract_package_index.json",
                   "research_contract_handoff.json", "checkpoint.json",
                   "package_seal.json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_disk_reserve(path: Path) -> None:
    free_gib = shutil.disk_usage(path).free / (1024 ** 3)
    if free_gib < MIN_FREE_GIB:
        raise RuntimeError(
            f"free disk {free_gib:.1f} GiB is below the "
            f"{MIN_FREE_GIB} GiB reserve; refusing to write package")


_LOCKFILE_CANDIDATES = ("uv.lock", "poetry.lock", "requirements.txt",
                        "requirements-dev.txt", "pyproject.toml")


def _environment_fingerprint() -> dict[str, Any]:
    """OPS-04: path-independent environment binding — interpreter,
    platform, dependency-lock digest, and locale/timezone policy."""
    repo_root = Path(__file__).resolve().parents[2]
    lock_name: Optional[str] = None
    lock_sha: Optional[str] = None
    for cand in _LOCKFILE_CANDIDATES:
        cand_path = repo_root / cand
        if cand_path.is_file() and not cand_path.is_symlink():
            lock_name = cand
            lock_sha = sha256_file(cand_path)
            break
    return {"python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "lock_file": lock_name,
            "lock_sha256": lock_sha,
            "tz_policy": "UTC",
            "locale_independent": True}


def _iter_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for k, v in value.items():
            if isinstance(k, str):
                yield k
            yield from _iter_strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _iter_strings(v)


_ABS_PATH_RE = re.compile(r"^(?:/|[A-Za-z]:[\\/])")


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA256_RE.fullmatch(value))


def _normalize_text(text: str) -> str:
    """Canonical prose form for residual-closure scans: lowercase,
    ``[_-]+`` -> single space, whitespace collapsed."""
    return _WHITESPACE_RE.sub(
        " ", _NORMALIZE_SEP_RE.sub(" ", text.lower())).strip()


def _iter_declared_fields(node: Any, prefix: str = ""):
    """Yield ``(dotted_path, key, value)`` for every mapping entry,
    skipping ``_DECLARED_METADATA_KEYS`` subtrees (unverified declared
    foreign metadata, not this package's identity)."""
    if isinstance(node, Mapping):
        for k, v in node.items():
            if k in _DECLARED_METADATA_KEYS:
                continue
            yield f"{prefix}{k}", k, v
            yield from _iter_declared_fields(v, f"{prefix}{k}.")
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            yield from _iter_declared_fields(v, f"{prefix}[{i}].")


def _rel_path_problem(value: Any) -> Optional[str]:
    """Return a reason when ``value`` is not a safe, portable, canonical
    relative path (forward slashes only, no drive/UNC/absolute prefix,
    no traversal, no NUL)."""
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
        return "traversal outside the root is not allowed"
    if PurePosixPath(value).as_posix() != value:
        return "path is not in canonical forward-slash form"
    return None


def _resolve_under_root(root: Path, relpath: Any, label: str,
                        problems: list[str]) -> Optional[Path]:
    """Resolve a root-relative path fail-closed: portable relative
    form, no symlink entry or symlinked parent, and resolved
    containment inside ``root``."""
    problem = _rel_path_problem(relpath)
    if problem is not None:
        problems.append(f"{label} {relpath!r}: {problem}")
        return None
    parts = PurePosixPath(relpath).parts
    target = root.joinpath(*parts)
    cursor = root
    for part in parts:
        cursor = cursor / part
        if cursor.is_symlink():
            problems.append(f"{label} {relpath!r} traverses a symlink")
            return None
    root_r = root.resolve()
    resolved = target.resolve()
    if resolved != root_r and root_r not in resolved.parents:
        problems.append(f"{label} {relpath!r} resolves outside the "
                        "evidence root")
        return None
    return target


def _b_evidence_doc_problems(doc: Any, *, ranked_digest: Any,
                             b_status: Any) -> list[str]:
    """PKG-11 content checks on a bound B evidence document: verified
    artifact envelope, the declared ranked-array canonical digest must
    be carried by the document, and any gate/status fields must agree
    with the declared ``b_status``."""
    problems: list[str] = []
    if not isinstance(doc, Mapping):
        return ["bound B evidence must be a JSON object"]
    ok, envp = verify_artifact_envelope(doc)
    if not ok:
        problems.extend(f"bound B evidence envelope: {p}"
                        for p in envp)
    if isinstance(ranked_digest, str) and _is_sha256(ranked_digest) \
            and not any(ranked_digest in s
                        for s in _iter_strings(doc)):
        problems.append(
            "bound B evidence does not contain the declared "
            "ranked_array_canonical_sha256 — the digest must be "
            "carried by the referenced artifact")
    blocked = "BLOCK" in str(b_status).upper()
    if "gate_passed" in doc:
        if doc["gate_passed"] is True and blocked:
            problems.append(
                "bound B evidence claims gate_passed=true while "
                f"b_status is {b_status!r}")
        elif doc["gate_passed"] is False and not blocked:
            problems.append(
                "bound B evidence claims gate_passed=false while "
                f"b_status is {b_status!r}")
    for key in ("status", "b_status"):
        value = doc.get(key)
        if not isinstance(value, str):
            continue
        if key == "b_status":
            if value != b_status:
                problems.append(
                    f"bound B evidence b_status {value!r} disagrees "
                    f"with the declared b_status {b_status!r}")
            continue
        upper = value.upper()
        doc_blocked = "BLOCK" in upper
        doc_passed = any(tok in upper for tok in
                         ("PASS", "READY", "COMPLETE", "SUCCESS"))
        if blocked and doc_passed and not doc_blocked:
            problems.append(
                f"bound B evidence status {value!r} asserts a "
                f"passing state while b_status is {b_status!r}")
        elif not blocked and doc_blocked:
            problems.append(
                f"bound B evidence status {value!r} asserts a "
                f"blocked state while b_status is {b_status!r}")
    return problems


def _verify_b_evidence_binding(ev_root: Path, b_ev: Any, *,
                               ranked_digest: Any, b_status: Any,
                               expected_root_name: str = ""
                               ) -> list[str]:
    """PKG-11: verify a declared ``b_evidence`` mapping — exact
    ``{relative_path, sha256}`` shape, safe resolution under the
    evidence root, on-disk digest equality, and the B-evidence content
    checks."""
    label = "run_context.json b_evidence"
    problems: list[str] = []
    if not isinstance(b_ev, Mapping):
        return [f"{label} must be a mapping "
                "{relative_path, sha256}"]
    extra = set(b_ev) - {"relative_path", "sha256"}
    if extra:
        problems.append(f"{label} has disallowed fields "
                        f"{sorted(extra)}")
    rel = b_ev.get("relative_path")
    declared_sha = b_ev.get("sha256")
    if not _is_sha256(declared_sha):
        problems.append(f"{label}.sha256 must be a lowercase SHA-256")
    if not ev_root.is_dir() or ev_root.is_symlink():
        problems.append(f"{label}: evidence_root is not a real "
                        f"directory: {ev_root}")
        return problems
    root_r = ev_root.resolve()
    if expected_root_name and root_r.name != expected_root_name:
        problems.append(
            f"{label}: evidence_root identity mismatch — expected a "
            f"root named {expected_root_name!r}, got {root_r.name!r}")
        return problems
    target = _resolve_under_root(ev_root, rel,
                                 f"{label}.relative_path", problems)
    if target is None:
        return problems
    if not target.is_file():
        problems.append(f"{label}.relative_path {rel!r} is not a "
                        "file under the evidence root")
        return problems
    if _is_sha256(declared_sha) and sha256_file(target) != \
            declared_sha:
        problems.append(f"{label} digest does not match the file on "
                        "disk")
    try:
        doc = json.loads(target.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        problems.append(f"{label} file unreadable: {exc}")
        return problems
    problems.extend(_b_evidence_doc_problems(
        doc, ranked_digest=ranked_digest, b_status=b_status))
    return problems


def _residual_register_problems(register: Any) -> list[str]:
    """HANDOFF-01: the embedded residual register must be non-empty,
    typed ``{id, status}`` mappings, and residual-positive — a package
    at this stage may not close out every residual."""
    if not isinstance(register, list) or not register:
        return ["residual_register must be a non-empty list — the "
                "package must remain residual-positive"]
    problems: list[str] = []
    for i, entry in enumerate(register):
        if not isinstance(entry, Mapping) or not isinstance(
                entry.get("id"), str) or not entry["id"] or not \
                isinstance(entry.get("status"), str) or \
                not entry["status"]:
            problems.append(f"residual_register[{i}] must be a "
                            "mapping with non-empty id and status")
    if all(isinstance(e, Mapping) and
           str(e.get("status", "")).strip().upper() == "CLOSED"
           for e in register):
        problems.append("residual_register has every status CLOSED — "
                        "a package at this stage must remain "
                        "residual-positive")
    return problems


def _write_active_pointer(ev_root: Path, pkg_dir: Path, *,
                          run_root_name: str,
                          candidate_generation_id: str,
                          code_revision: str) -> None:
    """PKG-09: single active-generation pointer written at the evidence
    root (the ONE sanctioned write outside ``package_dir``).  Only
    written when the package resolves under the evidence root; the
    pointer binds run identity, the package's relative path, and the
    index + seal file digests."""
    root_r = ev_root.resolve()
    pkg_r = pkg_dir.resolve()
    if root_r != pkg_r and root_r not in pkg_r.parents:
        return  # package lives outside the root — no pointer
    index_path = pkg_dir / "science_contract_package_index.json"
    seal_path = pkg_dir / "package_seal.json"
    if not (index_path.is_file() and seal_path.is_file()):
        return
    pointer = bind_artifact_envelope({
        "pointer_type": POINTER_TYPE,
        "run_root_name": run_root_name,
        "candidate_generation_id": candidate_generation_id,
        "code_revision": code_revision,
        "package_relpath": pkg_r.relative_to(root_r).as_posix(),
        "index_file_sha256": sha256_file(index_path),
        "package_seal_sha256": sha256_file(seal_path),
        "created_at": _now(),
        "research_diagnostic_only": True,
        "promotion_eligible": False,
        "production_authorized": False,
        "warning_path_authorized": False})
    _atomic_write_json(root_r / POINTER_NAME, pointer)


def _atomic_write_json(path: Path, obj: Mapping[str, Any]) -> None:
    """Write canonical JSON atomically (temp file in same dir + rename)."""
    text = json.dumps(obj, indent=1, sort_keys=True) + "\n"
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_",
                               suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _synthetic_mec_fixture(candidate_generation_id: str,
                         code_revision: str) -> Mapping[str, Any]:
    """A synthetic MEC fixture — proves the schema, carries no real data."""
    _LOCS = {"R1": (81.5, 28.0), "R2": (85.5, 28.0), "R3": (89.0, 27.0)}

    def ev(i: int, group: str, day: int) -> dict[str, Any]:
        region = f"R{(i - 1) % 3 + 1}"
        lat, lon = _LOCS[region][1], _LOCS[region][0]
        row_source = {"fixture_row": i, "group": group,
                      "day": day}
        return {
            "event_id": f"SYNTH-EVT-{i:03d}",
            "event_group_id": group,
            "region_id": region,
            "location": {"lat": lat, "lon": lon},
            "synthetic": True,
            "date_spec": {"precision": "day",
                          "date": f"2015-04-{day:02d}",
                          "source": "synthetic_fixture_date",
                          "timezone": "UTC"},
            "holdout_group": f"H{i % 2}",
            "windows": {
                "acquisition_window": {"start": "2015-04-01",
                                       "end": "2015-04-01"},
                "production_time": "2015-04-01",
                "issue_time": "2015-04-02",
                "publication_time": "2015-04-02",
                "feature_availability_time": {"start": "2015-04-01",
                                              "end": "2015-04-02"},
                "target_window": {"start": f"2015-04-{day:02d}",
                                  "end": f"2015-04-{day:02d}"}},
            "row_source": row_source,
            "row_sha256": sha256_canonical(row_source)}
    return {
        "envelope_type": "MULTI_EVENT_CONTRACT_V1",
        "profile_id": "SCIENCE_CONTRACT_T2_RESEARCH",
        "mode": "SYNTHETIC_FIXTURE",
        "research_only": True,
        "research_diagnostic_only": True,
        "synthetic_fixture": True,
        "source_catalog": {
            "catalog_id": "synthetic-catalog-v0",
            "source_sha256": "ab" * 32,
            "asset_ids": ["SYNTH-ASSET-1"],
            "processing_script_sha256": "cd" * 32},
        "mode": "SYNTHETIC_FIXTURE",
        "candidate_generation_id": candidate_generation_id,
        "code_revision": code_revision,
        "row_schema_version": "synthetic-rows-v1",
        "row_serialization": CANONICAL_ROW_SERIALIZATION,
        "event_groups": ["G1", "G2", "G3", "G4"],
        "events": [ev(1, "G1", 3), ev(2, "G2", 10), ev(3, "G3", 21),
                   ev(4, "G4", 5)],
        "holdout": {"assigned_before_filtering": True,
                    "temporal_embargo_days": 30,
                    "geographic_holdout": {"min_separation_km": 50.0},
                    "event_separation": {"group_disjoint": True},
                    "holdout_groups": ["H0", "H1"],
                    "assignment": {"SYNTH-EVT-001": "H1",
                                   "SYNTH-EVT-002": "H0",
                                   "SYNTH-EVT-003": "H1",
                                   "SYNTH-EVT-004": "H0"},
                    "assignment_sha256": sha256_canonical(
                        {"SYNTH-EVT-001": "H1", "SYNTH-EVT-002": "H0",
                         "SYNTH-EVT-003": "H1", "SYNTH-EVT-004": "H0"})},
        "label_spec": {
            "label_source": "synthetic_fixture_labels",
            "adjudication": {"required": True,
                             "independent_reviewers": 1,
                             "ledger_sha256": "9f" * 32,
                             "reviewer_ids": ["SYNTH-REV-1"]},
            "negative_controls": {"required": True, "n_controls": 1,
                                  "artifact_sha256": "8e" * 32}},
        "validation_scope": {"scope_id": "synthetic-regional-split",
                             "n_geographic_regions": 3,
                             "min_events": 4,
                             "regions": {
                                 "R1": {"bbox": [80.0, 27.0, 82.0, 29.0]},
                                 "R2": {"bbox": [84.0, 27.0, 86.0, 29.0]},
                                 "R3": {"bbox": [88.0, 26.0, 90.0, 28.0]}}},
        "claim_scope": "synthetic contract exercise only",
        "promotion_eligible": False,
        "production_authorized": False,
        "warning_path_authorized": False}


def _blocked_fmx_fixture() -> Mapping[str, Any]:
    """Synthetic FMX metadata that can only produce the blocked status."""
    return {
        "matrix_id": "fmx-synthetic-unfrozen",
        "candidate_generation_id": "synthetic-gen-0",
        "source_artifact_id": "synthetic-source-1",
        "source_sha256": "ab" * 32,
        "byte_count": 1,
        "producer_sha256": "cd" * 32,
        FEATURE_CONTRACT_SHA256_FIELD: "ef" * 32,
        "preregistration_sha256": "01" * 32,
        "matrix_sha256": "23" * 32,
        "columns": [{"name": "t2m_mean", "unit": "K", "role": "feature",
                     "aggregation": "seasonal_mean",
                     "temporal_resolution": "daily",
                     "availability_time": "2015-04-01"}],
        "missingness": {"fraction": 0.0, "policy": "complete"},
        "date_range": {"start": "2001-06-01", "end": "2015-08-31"},
        "spatial_coverage": {"region_id": "synthetic-region",
                             "n_units": 4},
        "target_spec": {"name": "synthetic_target",
                        "definition": "fixture"},
        "data_source_status": "SYNTHETIC_FIXTURE"}


def _check_package_dir_fresh(pkg_dir: Path,
                             forbidden_roots: Optional[list]) -> None:
    """OPS-PKG-01: the package root must be a fresh, empty, non-symlink
    directory that does not resolve under any forbidden root (repo
    checkouts, dirty main, prior package roots)."""
    if pkg_dir.is_symlink():
        raise ValueError(f"package_dir is a symlink: {pkg_dir}")
    if pkg_dir.exists():
        if not pkg_dir.is_dir():
            raise ValueError(f"package_dir exists and is not a "
                             f"directory: {pkg_dir}")
        if any(pkg_dir.iterdir()):
            raise ValueError(f"package_dir must be a fresh empty "
                             f"directory: {pkg_dir}")
    resolved = pkg_dir.resolve()
    for root in forbidden_roots or []:
        root_r = Path(root).resolve()
        if resolved == root_r or root_r in resolved.parents:
            raise ValueError(f"package_dir resolves under forbidden root "
                             f"{root_r}")


_EVIDENCE_REF_FIELDS = ("role", "relative_path", "sha256")


def _check_evidence_references(refs: Any) -> list:
    """OPS-PKG-04: typed digest-only evidence references — role, safe
    relative path, and a lowercase SHA-256; nothing else is allowed."""
    if refs is None:
        return []
    if not isinstance(refs, list):
        raise ValueError("evidence_references must be a list")
    checked = []
    for i, ref in enumerate(refs):
        if not isinstance(ref, Mapping):
            raise ValueError(f"evidence_references[{i}] must be a mapping")
        extra = set(ref) - set(_EVIDENCE_REF_FIELDS)
        if extra:
            raise ValueError(f"evidence_references[{i}] has disallowed "
                             f"fields {sorted(extra)}")
        for field in _EVIDENCE_REF_FIELDS:
            if field not in ref:
                raise ValueError(f"evidence_references[{i}].{field} is "
                                 "required")
        if not isinstance(ref["role"], str) or not ref["role"]:
            raise ValueError(f"evidence_references[{i}].role must be a "
                             "non-empty string")
        rel = ref["relative_path"]
        if not isinstance(rel, str) or not rel or rel.startswith("/")                 or ".." in rel.split("/"):
            raise ValueError(f"evidence_references[{i}].relative_path "
                             "must be a safe relative path")
        if not isinstance(ref["sha256"], str) or not                 re.fullmatch(r"[0-9a-f]{64}", ref["sha256"]):
            raise ValueError(f"evidence_references[{i}].sha256 must be a "
                             "lowercase SHA-256")
        checked.append(dict(ref))
    return checked


def _verify_evidence_root(root: Path, refs: list,
                          expected_root_name: str = "") -> None:
    """PKG-03: every evidence reference must resolve to a real regular
    file under ``root`` whose recomputed digest matches the declared
    sha256.  Symlinks and escapes are rejected.  PKG-07: when
    ``expected_root_name`` is given the resolved root directory's final
    component must equal it — a sibling or renamed root carrying the
    same relative files fails identity binding."""
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f"evidence_root is not a real directory: {root}")
    root_r = root.resolve()
    if expected_root_name and root_r.name != expected_root_name:
        raise ValueError(f"evidence_root identity mismatch: expected a "
                         f"root named {expected_root_name!r}, got "
                         f"{root_r.name!r}")
    for ref in refs:
        target = root / ref["relative_path"]
        if target.is_symlink():
            raise ValueError(f"evidence reference {ref['relative_path']!r} "
                             "is a symlink")
        resolved = target.resolve()
        if resolved != root_r and root_r not in resolved.parents:
            raise ValueError(f"evidence reference "
                             f"{ref['relative_path']!r} resolves outside "
                             "the evidence root")
        if not target.is_file():
            raise ValueError(f"evidence reference "
                             f"{ref['relative_path']!r} is not a file "
                             "under the evidence root")
        if sha256_file(target) != ref["sha256"]:
            raise ValueError(f"evidence reference "
                             f"{ref['relative_path']!r} digest does not "
                             "match the file on disk")


def build_science_contract_package(
        *, package_dir: str | Path, run_root_name: str,
        code_revision: str, candidate_generation_id: str,
        ranked_array_canonical_sha256: str,
        ranked_payload_sha256: str,
        ranked_payload_sha256_domain_status: str,
        b_status: str = "B_TO_C_BLOCKED",
        evidence_references: Optional[list] = None,
        forbidden_roots: Optional[list] = None,
        evidence_root: Optional[str | Path] = None,
        b_evidence_relative_path: Optional[str] = None,
        residual_register: Optional[list] = None,
        residual_register_relpath: Optional[str] = None
        ) -> dict[str, Any]:
    """Build the external research package.  Returns the package index.

    Only writes inside ``package_dir`` — which must be a fresh, empty,
    non-symlink directory outside every ``forbidden_roots`` (a non-empty
    list is required).  ``evidence_references`` are typed digest refs;
    when any are supplied ``evidence_root`` is required and each
    relative_path must resolve to a regular, non-symlink file under it
    whose recomputed SHA-256 matches the declared digest.

    ``candidate_generation_id`` must match
    ``^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$`` and ``code_revision`` must be
    lowercase hex (``^[0-9a-f]{7,64}$``) — PKG-12.

    ``b_evidence_relative_path`` (PKG-11) file-binds the B stage
    evidence: it must name a verified artifact envelope under
    ``evidence_root`` carrying the declared
    ``ranked_array_canonical_sha256``; its binding is recorded in
    run_context.json as ``b_evidence={relative_path, sha256}``.  When it
    is not supplied the run context carries
    ``b_evidence_binding="UNBOUND_INFORMATIONAL"``.

    ``residual_register`` (HANDOFF-01) is embedded verbatim in the
    research handoff with its canonical SHA-256; it must be a non-empty
    list of ``{id, status}`` mappings and remain residual-positive (not
    every status CLOSED).  Defaults to the open+deferred residual lists.
    ``residual_register_relpath`` binds the register to a canonical JSON
    file under ``evidence_root``: the file must parse to the exact
    register list embedded in the handoff, and verify re-resolves it —
    the register is then bound to known external content, not merely
    any residual-positive list.  Supplying both ``residual_register``
    and ``residual_register_relpath`` is rejected as ambiguous.

    Raises ``RuntimeError`` when the disk reserve is violated; the
    checkpoint is left ``INCOMPLETE`` if the build fails partway.
    """
    pkg_dir = Path(package_dir)
    if not forbidden_roots:
        raise ValueError("forbidden_roots is required — the protected "
                         "root list must be explicit")
    # PKG-12: identity formats are enforced at construction — a package
    # can never be built on a malformed generation id or code revision.
    if not isinstance(candidate_generation_id, str) or not \
            _GENERATION_ID_RE.fullmatch(candidate_generation_id):
        raise ValueError("candidate_generation_id must match "
                         "^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
    if not isinstance(code_revision, str) or not \
            _CODE_REVISION_RE.fullmatch(code_revision):
        raise ValueError("code_revision must be lowercase hex matching "
                         "^[0-9a-f]{7,64}$")
    if residual_register is not None and \
            residual_register_relpath is not None:
        raise ValueError("supply residual_register or "
                         "residual_register_relpath, not both")
    ev_root = Path(evidence_root) if evidence_root is not None else None
    register_source_record: Optional[dict[str, str]] = None
    if residual_register_relpath is not None:
        if ev_root is None:
            raise ValueError("evidence_root is required when "
                             "residual_register_relpath is supplied")
        reg_problems = []
        reg_target = _resolve_under_root(
            ev_root, residual_register_relpath,
            "residual_register_relpath", reg_problems)
        if reg_target is not None and not reg_target.is_file():
            reg_problems.append("residual_register_relpath "
                                f"{residual_register_relpath!r} is not "
                                "a file under the evidence root")
            reg_target = None
        if reg_target is not None:
            try:
                parsed = json.loads(reg_target.read_text("utf-8"))
            except (OSError, ValueError) as exc:
                reg_problems.append("residual register file unreadable: "
                                    f"{exc}")
            else:
                reg_problems.extend(_residual_register_problems(parsed))
                if not reg_problems:
                    residual_register = parsed
                    register_source_record = {
                        "relative_path": PurePosixPath(
                            residual_register_relpath).as_posix(),
                        "sha256": sha256_file(reg_target)}
        if reg_problems:
            raise ValueError("residual register binding is not valid: "
                             + "; ".join(reg_problems))
    if residual_register is not None:
        reg_problems = _residual_register_problems(residual_register)
        if reg_problems:
            raise ValueError("residual_register is not "
                             "residual-positive: "
                             + "; ".join(reg_problems))
    _check_package_dir_fresh(pkg_dir, forbidden_roots)
    pkg_dir.mkdir(parents=True, exist_ok=True)
    _check_disk_reserve(pkg_dir)
    checked_refs = _check_evidence_references(evidence_references)
    if checked_refs:
        if ev_root is None:
            raise ValueError("evidence_root is required when "
                             "evidence_references are supplied")
        _verify_evidence_root(ev_root, checked_refs,
                              expected_root_name=run_root_name)

    # PKG-11: optional file-bound B evidence — the path must resolve to
    # a real, non-symlink file under evidence_root carrying a verified
    # artifact envelope that itself records the declared ranked digest.
    b_evidence_record: Optional[dict[str, str]] = None
    if b_evidence_relative_path is not None:
        if ev_root is None:
            raise ValueError("evidence_root is required when "
                             "b_evidence_relative_path is supplied")
        b_problems: list[str] = []
        root_r = ev_root.resolve()
        if not ev_root.is_dir() or ev_root.is_symlink():
            b_problems.append("evidence_root is not a real directory: "
                              f"{ev_root}")
        elif root_r.name != run_root_name:
            b_problems.append("evidence_root identity mismatch: "
                              f"expected a root named "
                              f"{run_root_name!r}, got {root_r.name!r}")
        else:
            b_target = _resolve_under_root(
                ev_root, b_evidence_relative_path,
                "b_evidence_relative_path", b_problems)
            if b_target is not None:
                if not b_target.is_file():
                    b_problems.append("b_evidence_relative_path "
                                      f"{b_evidence_relative_path!r} is "
                                      "not a file under the evidence "
                                      "root")
                else:
                    try:
                        b_doc = json.loads(
                            b_target.read_text("utf-8"))
                    except (OSError, ValueError) as exc:
                        b_problems.append("bound B evidence file "
                                          f"unreadable: {exc}")
                    else:
                        b_problems.extend(_b_evidence_doc_problems(
                            b_doc,
                            ranked_digest=ranked_array_canonical_sha256,
                            b_status=b_status))
                if not b_problems and b_target is not None:
                    b_evidence_record = {
                        "relative_path": PurePosixPath(
                            b_evidence_relative_path).as_posix(),
                        "sha256": sha256_file(b_target)}
        if b_problems:
            raise ValueError("bound B evidence is not valid: "
                             + "; ".join(b_problems))

    checkpoint = {"run_state": "RUNNING", "created_at": _now(),
                  "states": list(CHECKPOINT_STATES),
                  "research_diagnostic_only": True,
                  "promotion_eligible": False,
                  "production_authorized": False,
                  "warning_path_authorized": False}
    ckpt_path = pkg_dir / "checkpoint.json"
    _atomic_write_json(ckpt_path, checkpoint)

    try:
        mec_env = build_mec_envelope(
            _synthetic_mec_fixture(candidate_generation_id,
                                   code_revision))
        fmx_env = build_fmx_envelope(_blocked_fmx_fixture())
        # Strict scaffold verification requires every file-bound
        # referenced envelope to carry the assembly identity — stamp it
        # on the blocked fixture envelope and re-bind.
        fmx_env = bind_artifact_envelope(
            {**fmx_env,
             "candidate_generation_id": candidate_generation_id,
             "code_revision": code_revision})
        assert fmx_env["fmx_status"] == FMX_BLOCKED_PENDING_EXPLICIT_FREEZE
        assert fmx_env["matrix"]["status"] == "ABSENT"
        # The scaffold's references are file-bound to the package's own
        # envelope files, so they are written first and hashed on disk.
        _atomic_write_json(pkg_dir / "mec_schema.json", mec_env)
        _atomic_write_json(pkg_dir / "fmx_blocked.json", fmx_env)

        # PKG-11: b_evidence_binding is always recorded — FILE_BOUND
        # only when a verified evidence file was bound above;
        # otherwise the exact UNBOUND_INFORMATIONAL marker.
        run_context_fields: dict[str, Any] = {
            "context_type": "SCIENCE_CONTRACT_RUN_CONTEXT_V1",
            "created_at": _now(),
            "run_root_name": run_root_name,
            "code_revision": code_revision,
            "candidate_generation_id": candidate_generation_id,
            "package_status": PACKAGE_STATUS,
            "b_status": b_status,
            "ranked_array_canonical_sha256": ranked_array_canonical_sha256,
            "ranked_payload_sha256": ranked_payload_sha256,
            "ranked_payload_sha256_domain_status":
                ranked_payload_sha256_domain_status,
            "evidence_references": checked_refs,
            "b_evidence_binding": (B_EVIDENCE_BOUND
                                   if b_evidence_record is not None
                                   else B_EVIDENCE_UNBOUND),
            "environment": _environment_fingerprint(),
            "dirty_matrix_policy": "treated as absent; never inspected",
            "research_diagnostic_only": True,
            "promotion_eligible": False,
            "production_authorized": False,
            "warning_path_authorized": False}
        if b_evidence_record is not None:
            run_context_fields["b_evidence"] = b_evidence_record
        run_context = bind_artifact_envelope(run_context_fields)
        _atomic_write_json(pkg_dir / "run_context.json", run_context)

        _metric_registry = [
            {"metric_id": "calibration_slope",
             "class": "calibration", "operational_threshold": None},
            {"metric_id": "auroc", "class": "discrimination",
             "operational_threshold": None},
            {"metric_id": "lead_time_days", "class": "lead_time",
             "operational_threshold": None},
            {"metric_id": "false_alarm_rate", "class": "false_alarm",
             "operational_threshold": None}]
        scaffold = build_scaffold_envelope({
            "mode": "VALIDATION_SCAFFOLD_ONLY",
            "research_diagnostic_only": True,
            "promotion_eligible": False,
            "candidate_generation_id": candidate_generation_id,
            "code_revision": code_revision,
            "references": {
                "mec_reference": {
                    "envelope_sha256": mec_env["artifact_sha256"],
                    "envelope_type": "MULTI_EVENT_CONTRACT_V1",
                    "relative_path": "mec_schema.json"},
                "fmx_reference": {
                    "envelope_sha256": fmx_env["artifact_sha256"],
                    "fmx_status": FMX_BLOCKED_PENDING_EXPLICIT_FREEZE,
                    "relative_path": "fmx_blocked.json"},
                "b_reference": {
                    "status": b_status,
                    "ranked_array_canonical_sha256":
                        ranked_array_canonical_sha256,
                    "envelope_sha256": run_context["artifact_sha256"],
                    "relative_path": "run_context.json"}},
            "split_spec": {
                "split_id": "T2S-SPLIT-SYNTHETIC-V1",
                "temporal_embargo_days": 30,
                "geographic_holdout": {"min_separation_km": 50.0},
                "event_separation": {"group_disjoint": True}},
            "metric_registry": _metric_registry,
            "metric_registry_sha256": sha256_canonical(_metric_registry),
            "inherited_state": {"b_status": b_status,
                                "ranking_rerun": False,
                                "e_status": "E_BLOCKED",
                                "f_status": "F_BLOCKED"}},
            reference_root=pkg_dir)

        no_claims = bind_artifact_envelope({
            "no_claims_type": "RESEARCH_NO_CLAIMS_V1",
            "research_diagnostic_only": True,
            "promotion_eligible": False,
            "production_authorized": False,
            "warning_path_authorized": False,
            "b_status": b_status,
            "e_status": "E_BLOCKED",
            "f_status": "F_BLOCKED",
            "fmx_status": FMX_BLOCKED_PENDING_EXPLICIT_FREEZE,
            "ranking_rerun": False,
            "claims": [
                "contract/scaffold engineering only; no scientific "
                "validation performed or implied",
                "no warning, production, or authority readiness",
                "no freeze token was generated or accepted",
                "residuals C01-C07 remain open/deferred"]})

        open_resids = [
            {"id": "FMX-01", "status": "BLOCKED",
             "title": "no externally frozen environmental feature "
                      "matrix; freeze token required"},
            {"id": "R04", "status": "OPEN",
             "title": "merge requires explicit human approval"}]
        deferred_resids = [{"id": f"C{i:02d}", "status": "DEFERRED"}
                           for i in range(1, 8)]
        # HANDOFF-01: the complete residual register is embedded
        # verbatim and frozen by canonical digest.
        register = (list(residual_register)
                    if residual_register is not None
                    else open_resids + deferred_resids)
        handoff = bind_artifact_envelope({
            "envelope_type": HANDOFF_TYPE,
            "research_diagnostic_only": True,
            "package_status": PACKAGE_STATUS,
            "code_revision": code_revision,
            "candidate_generation_id": candidate_generation_id,
            "mec_envelope_sha256": mec_env["artifact_sha256"],
            "fmx_envelope_sha256": fmx_env["artifact_sha256"],
            "scaffold_envelope_sha256": scaffold["artifact_sha256"],
            "inherited_state": {"b_status": b_status,
                                "e_status": "E_BLOCKED",
                                "f_status": "F_BLOCKED",
                                "ranking_rerun": False},
            "open_residuals": open_resids,
            "deferred_residuals": deferred_resids,
            "residual_register": register,
            "residual_register_sha256": sha256_canonical(register),
            **({"residual_register_source": register_source_record}
               if register_source_record is not None else {}),
            "promotion_eligible": False,
            "production_authorized": False,
            "warning_path_authorized": False})

        _atomic_write_json(pkg_dir / "run_context.json", run_context)
        _atomic_write_json(pkg_dir / "mec_schema.json", mec_env)
        _atomic_write_json(pkg_dir / "fmx_blocked.json", fmx_env)
        _atomic_write_json(pkg_dir / "validation_scaffold.json", scaffold)
        _atomic_write_json(pkg_dir / "research_no_claims.json", no_claims)
        _atomic_write_json(pkg_dir / "research_contract_handoff.json",
                           handoff)

        files = []
        for name in sorted(p.name for p in pkg_dir.iterdir()
                           if p.is_file() and p.suffix == ".json"
                           and p.name != "science_contract_package_index.json"
                           and p.name != "checkpoint.json"):
            files.append({"relative_path": name,
                          "sha256": sha256_file(pkg_dir / name),
                          "bytes": (pkg_dir / name).stat().st_size})
        index = bind_artifact_envelope({
            "index_type": PACKAGE_INDEX_TYPE,
            "package_status": PACKAGE_STATUS,
            "code_revision": code_revision,
            "files": files,
            "research_diagnostic_only": True,
            "promotion_eligible": False,
            "production_authorized": False,
            "warning_path_authorized": False})
        _atomic_write_json(
            pkg_dir / "science_contract_package_index.json", index)

        index_sha = sha256_file(
            pkg_dir / "science_contract_package_index.json")
        checkpoint["run_state"] = "PASS"
        checkpoint["completed_at"] = _now()
        checkpoint["index_file_sha256"] = index_sha
        checkpoint["package_status"] = PACKAGE_STATUS
        # PKG-01: the final checkpoint is a self-bound envelope — its
        # artifact_sha256 covers run_state, index linkage, and status.
        checkpoint = bind_artifact_envelope(checkpoint)
        _atomic_write_json(ckpt_path, checkpoint)

        # PKG-02: non-circular package seal — binds every package file
        # (index + checkpoint + all docs) except itself.
        seal_files = {f.name: sha256_file(f)
                      for f in sorted(pkg_dir.iterdir())
                      if f.is_file() and not f.is_symlink()}
        seal = bind_artifact_envelope({
            "seal_type": SEAL_TYPE,
            "package_status": PACKAGE_STATUS,
            "files": seal_files,
            "research_diagnostic_only": True,
            "promotion_eligible": False,
            "production_authorized": False,
            "warning_path_authorized": False})
        _atomic_write_json(pkg_dir / "package_seal.json", seal)
        # PKG-09: publish the single active-generation pointer at the
        # evidence root (the one write outside package_dir, by design).
        if ev_root is not None:
            _write_active_pointer(
                ev_root, pkg_dir, run_root_name=run_root_name,
                candidate_generation_id=candidate_generation_id,
                code_revision=code_revision)
    except BaseException:
        checkpoint["run_state"] = "INCOMPLETE"
        checkpoint["failed_at"] = _now()
        _atomic_write_json(ckpt_path, checkpoint)
        raise
    return {"package_status": PACKAGE_STATUS, "package_dir": str(pkg_dir)}


def verify_science_contract_package(
        package_dir: str | Path, *,
        evidence_root: Optional[str | Path] = None
        ) -> tuple[bool, list[str]]:
    """Re-read and re-verify a package from disk: presence, per-file
    hashes, envelope self-hashes, checkpoint state+self-hash, package
    seal, claim lint, mandatory flags, file-bound scaffold references,
    evidence-reference resolution, and the explicit FMX/B blocked
    statuses.

    ``evidence_root`` must resolve the run-context evidence references;
    when the package declares any and no root is supplied, verification
    fails closed (unverified evidence)."""
    problems: list[str] = []
    pkg_dir = Path(package_dir)
    if not pkg_dir.is_dir():
        return False, [f"package directory missing: {package_dir}"]

    for name in _REQUIRED_FILES:
        if not (pkg_dir / name).is_file():
            problems.append(f"required package file missing: {name}")

    # OPS-PKG-03: every entry must be a regular file; no symlinks, no
    # directories, no extras beyond the indexed set + index + checkpoint.
    for entry in sorted(pkg_dir.iterdir()):
        if entry.is_symlink():
            problems.append(f"symlink in package directory: "
                            f"{entry.name}")
        elif entry.is_dir():
            problems.append(f"directory in package directory: "
                            f"{entry.name}")
        elif not entry.is_file():
            problems.append(f"non-regular file in package directory: "
                            f"{entry.name}")

    ckpt_path = pkg_dir / "checkpoint.json"
    ckpt: Mapping[str, Any] = {}
    if ckpt_path.is_file():
        try:
            ckpt = json.loads(ckpt_path.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            problems.append(f"checkpoint unreadable: {exc}")
        if ckpt.get("run_state") != "PASS":
            problems.append(
                f"checkpoint run_state {ckpt.get('run_state')!r} is not "
                "PASS — the package did not complete")
        if ckpt.get("package_status") not in (None, PACKAGE_STATUS):
            problems.append("checkpoint package_status mismatch")
        # PKG-01: checkpoint self-hash is mandatory
        ok_ck, ckpt_env = verify_artifact_envelope(ckpt)
        if not ok_ck:
            problems.extend(f"checkpoint envelope: {p}" for p in ckpt_env)

    index_path = pkg_dir / "science_contract_package_index.json"
    index: Mapping[str, Any] = {}
    if index_path.is_file():
        try:
            index = json.loads(index_path.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            problems.append(f"package index unreadable: {exc}")
        if index:
            ok, envp = verify_artifact_envelope(index)
            if not ok:
                problems.extend(f"index envelope: {p}" for p in envp)
            if index.get("index_type") != PACKAGE_INDEX_TYPE:
                problems.append("index_type mismatch")
            if index.get("package_status") != PACKAGE_STATUS:
                problems.append(f"package_status must be "
                                f"{PACKAGE_STATUS!r}")
            indexed: set[str] = set()
            for entry in index.get("files", []):
                rel = entry.get("relative_path")
                if not isinstance(rel, str) or rel.startswith("/") or \
                        ".." in rel.split("/"):
                    problems.append(f"unsafe index path {rel!r}")
                    continue
                if rel in indexed:
                    problems.append(f"duplicate index path {rel!r}")
                indexed.add(rel)
                if not isinstance(entry.get("bytes"), int) or \
                        entry["bytes"] < 0:
                    problems.append(f"index entry {rel!r} has an "
                                    "untyped byte count")
                if not isinstance(entry.get("sha256"), str) or not \
                        re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
                    problems.append(f"index entry {rel!r} has an "
                                    "untyped digest")
                target = pkg_dir / rel
                if not target.is_file() or target.is_symlink():
                    problems.append(f"indexed file missing or not a "
                                    f"regular file: {rel}")
                    continue
                if sha256_file(target) != entry.get("sha256"):
                    problems.append(f"indexed file checksum mismatch: "
                                    f"{rel}")
                if isinstance(entry.get("bytes"), int) and \
                        target.stat().st_size != entry["bytes"]:
                    problems.append(f"indexed file byte-count mismatch: "
                                    f"{rel}")
            allowed = indexed | {"science_contract_package_index.json",
                                 "checkpoint.json", "package_seal.json"}
            for entry in pkg_dir.iterdir():
                if entry.is_file() and not entry.is_symlink() and \
                        entry.name not in allowed:
                    problems.append(f"extra file not indexed: "
                                    f"{entry.name}")
            # OPS-PKG-02: checkpoint must bind the index file digest —
            # omission is a failure, not an optional field.
            expected_index_sha = ckpt.get("index_file_sha256")
            if expected_index_sha is None:
                problems.append("checkpoint lacks index_file_sha256 — "
                                "the checkpoint does not bind the index")
            elif sha256_file(index_path) != expected_index_sha:
                problems.append("checkpoint index_file_sha256 does "
                                "not match the index file")

    # PKG-02: the seal must bind every package file except itself.
    seal_path = pkg_dir / "package_seal.json"
    if seal_path.is_file() and not seal_path.is_symlink():
        try:
            seal = json.loads(seal_path.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            problems.append(f"package seal unreadable: {exc}")
            seal = {}
        if seal:
            ok_s, seal_env = verify_artifact_envelope(seal)
            if not ok_s:
                problems.extend(f"package seal envelope: {p}"
                                for p in seal_env)
            if seal.get("seal_type") != SEAL_TYPE:
                problems.append("package seal type mismatch")
            sealed = seal.get("files")
            if not isinstance(sealed, Mapping):
                problems.append("package seal lacks a files map")
            else:
                actual = {f.name: f for f in pkg_dir.iterdir()
                          if f.is_file() and not f.is_symlink()
                          and f.name != "package_seal.json"}
                if set(sealed) != set(actual):
                    problems.append("package seal file set does not "
                                    "equal the package contents")
                else:
                    for name_s, f_s in actual.items():
                        if sha256_file(f_s) != sealed[name_s]:
                            problems.append(f"package seal hash "
                                            f"mismatch: {name_s}")

    docs: dict[str, Any] = {}
    if isinstance(ckpt, Mapping) and ckpt:
        docs["checkpoint.json"] = ckpt
    for name in ("run_context.json", "mec_schema.json",
                 "fmx_blocked.json", "validation_scaffold.json",
                 "research_no_claims.json",
                 "research_contract_handoff.json",
                 "science_contract_package_index.json",
                 "package_seal.json"):
        path = pkg_dir / name
        if not path.is_file():
            continue
        try:
            doc = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            problems.append(f"{name} unreadable: {exc}")
            continue
        if isinstance(doc, Mapping):
            docs[name] = doc
        ok, envp = verify_artifact_envelope(doc)
        if not ok:
            problems.extend(f"{name}: {p}" for p in envp)
        ok, lintp = lint_research_claims(doc)
        if not ok:
            problems.extend(f"{name}: {p}" for p in lintp)
        # PKG-06: every package document must carry the full
        # research-only flag set.
        for flag in _MANDATORY_FLAG_FIELDS:
            if flag not in doc:
                problems.append(f"{name}: mandatory flag {flag!r} "
                                "missing")
            elif doc[flag] is not (True if flag ==
                                   "research_diagnostic_only" else False):
                problems.append(f"{name}: flag {flag!r} has an "
                                f"unauthorized value "
                                f"{doc[flag]!r}")
        if name == "run_context.json" and isinstance(doc, Mapping):
            refs = doc.get("evidence_references") or []
            if refs:
                if evidence_root is None:
                    problems.append("run_context.json declares evidence "
                                    "references but no evidence_root "
                                    "was supplied — unverified")
                else:
                    try:
                        _verify_evidence_root(
                            Path(evidence_root), refs,
                            expected_root_name=str(
                                doc.get("run_root_name") or ""))
                    except ValueError as exc:
                        problems.append(f"evidence references: {exc}")
            # PKG-11: a declared bound B evidence file must resolve and
            # re-verify under the evidence root; ranked digests with no
            # bound file require the exact UNBOUND_INFORMATIONAL marker.
            b_ev = doc.get("b_evidence")
            b_binding = doc.get("b_evidence_binding")
            if b_ev is not None:
                if b_binding != B_EVIDENCE_BOUND:
                    problems.append(
                        "run_context.json: b_evidence is declared but "
                        "b_evidence_binding is not "
                        f"{B_EVIDENCE_BOUND!r}")
                if evidence_root is None:
                    problems.append(
                        "run_context.json declares bound B evidence but "
                        "no evidence_root was supplied — unverified")
                else:
                    problems.extend(
                        _verify_b_evidence_binding(
                            Path(evidence_root), b_ev,
                            ranked_digest=doc.get(
                                "ranked_array_canonical_sha256"),
                            b_status=doc.get("b_status"),
                            expected_root_name=str(
                                doc.get("run_root_name") or "")))
            elif any(_is_sha256(doc.get(k)) for k in
                     ("ranked_array_canonical_sha256",
                      "ranked_payload_sha256")):
                if b_binding != B_EVIDENCE_UNBOUND:
                    problems.append(
                        "run_context.json: ranked digests without a "
                        "bound B evidence file require "
                        f"b_evidence_binding={B_EVIDENCE_UNBOUND!r}")
            elif b_binding not in (None, B_EVIDENCE_UNBOUND):
                problems.append(
                    "run_context.json: b_evidence_binding "
                    f"{b_binding!r} is not a recognized binding state")
        # OPS-03: portable provenance — no absolute machine-local paths
        # may appear anywhere in a package document.
        for s in _iter_strings(doc):
            if _ABS_PATH_RE.match(s):
                problems.append(f"{name}: absolute path string "
                                f"{s!r} is not portable provenance")
                break

    # PKG-13: run_context.json is canonical for the run-identity fields.
    rc_doc = docs.get("run_context.json")
    canonical: dict[str, Any] = {}
    if isinstance(rc_doc, Mapping):
        for field in _CANONICAL_IDENTITY_FIELDS:
            value = rc_doc.get(field)
            if not isinstance(value, str) or not value:
                problems.append(
                    f"run_context.json: canonical field {field!r} must "
                    "be a non-empty string")
            else:
                canonical[field] = value
        if canonical.get("package_status") is not None and \
                canonical["package_status"] != PACKAGE_STATUS:
            problems.append(
                f"run_context.json: package_status must be "
                f"{PACKAGE_STATUS!r}")
    else:
        problems.append("run_context.json unavailable — canonical "
                        "run identity cannot be established")

    for doc_name, doc in docs.items():
        if not isinstance(doc, Mapping):
            continue
        # HANDOFF-01: no package doc may claim the residual register is
        # fully closed — this stage must remain residual-positive.
        for s in _iter_strings(doc):
            norm = _normalize_text(s)
            if any(phrase in norm for phrase in _GAP_CLOSED_PHRASES):
                problems.append(
                    f"{doc_name}: document claims the residual "
                    "register is closed — 'all gaps closed' / 'no "
                    "remaining gaps' claims are forbidden at this "
                    "stage")
                break
        for dotted, key, value in _iter_declared_fields(doc):
            # PKG-12: present identity fields must be well-formed.
            if key == "candidate_generation_id" and (
                    not isinstance(value, str) or
                    not _GENERATION_ID_RE.fullmatch(value)):
                problems.append(
                    f"{doc_name}: {dotted} value {value!r} is not a "
                    "well-formed candidate_generation_id")
            elif key == "code_revision" and (
                    not isinstance(value, str) or
                    not _CODE_REVISION_RE.fullmatch(value)):
                problems.append(
                    f"{doc_name}: {dotted} value {value!r} is not a "
                    "well-formed code_revision (lowercase hex, 7-64 "
                    "chars)")
            # PKG-13: declared identity fields must equal canonical.
            if doc_name != "run_context.json" and \
                    key in _CANONICAL_IDENTITY_FIELDS and \
                    key in canonical and value != canonical[key]:
                problems.append(
                    f"{doc_name}: {dotted} declares {value!r} but "
                    "run_context.json canonical value is "
                    f"{canonical[key]!r}")

    # HANDOFF-01: the handoff embeds the complete residual register,
    # frozen by canonical digest, and must remain residual-positive.
    ho_doc = docs.get("research_contract_handoff.json")
    if isinstance(ho_doc, Mapping):
        register = ho_doc.get("residual_register")
        problems.extend(
            f"research_contract_handoff.json: {p}"
            for p in _residual_register_problems(register))
        declared_reg_sha = ho_doc.get("residual_register_sha256")
        if not _is_sha256(declared_reg_sha):
            problems.append(
                "research_contract_handoff.json: "
                "residual_register_sha256 must be a lowercase SHA-256 "
                "of the embedded register")
        elif isinstance(register, list):
            try:
                actual_reg_sha = sha256_canonical(register)
            except (TypeError, ValueError):
                actual_reg_sha = None
            if declared_reg_sha != actual_reg_sha:
                problems.append(
                    "research_contract_handoff.json: "
                    "residual_register_sha256 does not recompute from "
                    "the embedded register")
        # HANDOFF-01: a declared register source must resolve to the
        # canonical register file under the evidence root and parse to
        # exactly the embedded register — the register is then bound to
        # known external content, not any residual-positive list.
        reg_src = ho_doc.get("residual_register_source")
        if reg_src is not None:
            if not isinstance(reg_src, Mapping) or \
                    set(reg_src) != {"relative_path", "sha256"}:
                problems.append("research_contract_handoff.json: "
                                "residual_register_source must be "
                                "{relative_path, sha256}")
            elif evidence_root is None:
                problems.append("research_contract_handoff.json: "
                                "declares residual_register_source but "
                                "no evidence_root was supplied — "
                                "unverified")
            else:
                src_problems: list[str] = []
                src_target = _resolve_under_root(
                    Path(evidence_root), reg_src["relative_path"],
                    "residual_register_source.relative_path",
                    src_problems)
                if src_target is None or not src_target.is_file():
                    src_problems.append("residual_register_source file "
                                        "missing under evidence root")
                elif sha256_file(src_target) != reg_src["sha256"]:
                    src_problems.append("residual_register_source file "
                                        "digest does not match")
                else:
                    try:
                        parsed = json.loads(
                            src_target.read_text("utf-8"))
                    except (OSError, ValueError) as exc:
                        src_problems.append("residual_register_source "
                                            f"file unparsable: {exc}")
                    else:
                        if parsed != register:
                            src_problems.append(
                                "residual_register_source content does "
                                "not equal the embedded register")
                problems.extend(
                    f"research_contract_handoff.json: {p}"
                    for p in src_problems)

    fmx_path = pkg_dir / "fmx_blocked.json"
    if fmx_path.is_file():
        fmx_doc = json.loads(fmx_path.read_text("utf-8"))
        ok, fmxp = verify_fmx_envelope(fmx_doc)
        if not ok:
            problems.extend(f"fmx_blocked.json: {p}" for p in fmxp)
        if fmx_doc.get("fmx_status") != \
                FMX_BLOCKED_PENDING_EXPLICIT_FREEZE:
            problems.append("fmx_blocked.json must carry "
                            "FMX_BLOCKED_PENDING_EXPLICIT_FREEZE")
    scaffold_path = pkg_dir / "validation_scaffold.json"
    if scaffold_path.is_file():
        sc_doc = json.loads(scaffold_path.read_text("utf-8"))
        ok, scp = verify_scaffold_envelope(sc_doc, reference_root=pkg_dir,
                                           strict=True)
        if not ok:
            problems.extend(f"validation_scaffold.json: {p}"
                            for p in scp)
        if sc_doc.get("scaffold_status") != BLOCKED_PENDING_FMX:
            problems.append("validation_scaffold.json must carry "
                            "BLOCKED_PENDING_FMX")

    # PKG-09/PKG-10: when an evidence root is supplied and the package
    # resolves under it, the active-generation pointer at the root is
    # MANDATORY — a verified envelope binding pointer_type, run-root
    # name, generation, code revision, package relpath, and the index +
    # seal file digests to THIS package.  A missing, forged, or renamed
    # pointer fails; stale siblings reject.
    if evidence_root is not None:
        ev_root = Path(evidence_root)
        ptr_path = ev_root / POINTER_NAME
        pkg_r = pkg_dir.resolve()
        root_r = ev_root.resolve() if ev_root.is_dir() else None
        if root_r is not None and \
                (pkg_r == root_r or root_r in pkg_r.parents):
            if ptr_path.is_symlink() or not ptr_path.is_file():
                problems.append(
                    f"active-generation pointer {POINTER_NAME} missing "
                    "at the evidence root — a package resolving under "
                    "the root must be the generation the pointer names")
            else:
                try:
                    ptr = json.loads(ptr_path.read_text("utf-8"))
                except (OSError, ValueError) as exc:
                    problems.append(f"active-generation pointer "
                                    f"unreadable: {exc}")
                    ptr = None
                if not isinstance(ptr, Mapping) or not ptr:
                    problems.append("active-generation pointer is not a "
                                    "JSON object")
                else:
                    ok_p, ptr_env = verify_artifact_envelope(ptr)
                    if not ok_p:
                        problems.extend(f"active pointer: {p}"
                                        for p in ptr_env)
                    for field in ("pointer_type", "run_root_name",
                                  "candidate_generation_id",
                                  "code_revision", "package_relpath",
                                  "index_file_sha256",
                                  "package_seal_sha256"):
                        if field not in ptr:
                            problems.append(
                                f"active pointer lacks {field!r}")
                    rel = pkg_r.relative_to(root_r).as_posix()
                    rc = rc_doc if isinstance(rc_doc, Mapping) else {}
                    for field, expected in (
                            ("pointer_type", POINTER_TYPE),
                            ("run_root_name",
                             rc.get("run_root_name")),
                            ("candidate_generation_id",
                             canonical.get("candidate_generation_id")),
                            ("code_revision",
                             canonical.get("code_revision")),
                            ("package_relpath", rel)):
                        if field in ptr and expected is not None and \
                                ptr.get(field) != expected:
                            if field == "package_relpath":
                                problems.append(
                                    "stale generation: active pointer "
                                    f"names {ptr.get(field)!r}, not "
                                    f"{expected!r}")
                            elif field == "pointer_type":
                                problems.append(
                                    "active pointer type mismatch")
                            else:
                                problems.append(
                                    f"active pointer {field} "
                                    f"{ptr.get(field)!r} does not "
                                    f"match this package's "
                                    f"{expected!r}")
                    if index_path.is_file() and \
                            ptr.get("index_file_sha256") != \
                            sha256_file(index_path):
                        problems.append("active pointer index digest "
                                        "does not match this package")
                    if seal_path.is_file() and \
                            ptr.get("package_seal_sha256") != \
                            sha256_file(seal_path):
                        problems.append("active pointer seal digest "
                                        "does not match this package")

    for path in pkg_dir.iterdir():
        if path.is_file() and path.name.startswith(("e_", "f_", "E_",
                                                    "F_")):
            problems.append(f"forbidden E/F artifact present: "
                            f"{path.name}")
    return (not problems), problems
