"""Science-contract package builder/verifier (OPS-*).

Builds a fresh external, research-only package that aggregates the T2
contract/scaffold evidence: run context, a synthetic MEC fixture envelope,
the explicitly blocked FMX envelope, the T2S validation scaffold, a
no-claims register, a package index with per-file SHA-256, a research
handoff, and an atomic checkpoint.

Safety properties:

* writes only inside the caller-supplied package directory; never copies
  candidate data, ranked payloads, or any matrix bytes;
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
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from .feature_matrix_contract import (FEATURE_CONTRACT_SHA256_FIELD,
                                      FMX_BLOCKED_PENDING_EXPLICIT_FREEZE,
                                      build_fmx_envelope,
                                      verify_fmx_envelope)
from .multi_event_contract import build_mec_envelope
from .provenance import (bind_artifact_envelope, sha256_canonical,
                         sha256_file, verify_artifact_envelope)
from .research_boundaries import lint_research_claims
from .validation_scaffold import (BLOCKED_PENDING_FMX,
                                  build_scaffold_envelope,
                                  verify_scaffold_envelope)

SEAL_TYPE = "SCIENCE_CONTRACT_SEAL_V1"
_MANDATORY_FLAG_FIELDS = ("research_diagnostic_only",
                          "promotion_eligible", "production_authorized",
                          "warning_path_authorized")

PACKAGE_STATUS = "SCIENCE_CONTRACT_SCAFFOLD_READY_WITH_FMX_BLOCKED"
PACKAGE_INDEX_TYPE = "SCIENCE_CONTRACT_PACKAGE_INDEX_V1"
HANDOFF_TYPE = "RESEARCH_CONTRACT_HANDOFF_V1"
MIN_FREE_GIB = 8.0
CHECKPOINT_STATES = ("RUNNING", "INCOMPLETE", "BLOCKED", "PASS")

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


def _synthetic_mec_fixture() -> Mapping[str, Any]:
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
        "row_schema_version": "synthetic-rows-v1",
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


def _verify_evidence_root(root: Path, refs: list) -> None:
    """PKG-03: every evidence reference must resolve to a real regular
    file under ``root`` whose recomputed digest matches the declared
    sha256.  Symlinks and escapes are rejected."""
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f"evidence_root is not a real directory: {root}")
    root_r = root.resolve()
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
        evidence_root: Optional[str | Path] = None
        ) -> dict[str, Any]:
    """Build the external research package.  Returns the package index.

    Only writes inside ``package_dir`` — which must be a fresh, empty,
    non-symlink directory outside every ``forbidden_roots`` (a non-empty
    list is required).  ``evidence_references`` are typed digest refs;
    when any are supplied ``evidence_root`` is required and each
    relative_path must resolve to a regular, non-symlink file under it
    whose recomputed SHA-256 matches the declared digest.  Raises
    ``RuntimeError`` when the disk reserve is violated; the checkpoint is
    left ``INCOMPLETE`` if the build fails partway.
    """
    pkg_dir = Path(package_dir)
    if not forbidden_roots:
        raise ValueError("forbidden_roots is required — the protected "
                         "root list must be explicit")
    _check_package_dir_fresh(pkg_dir, forbidden_roots)
    pkg_dir.mkdir(parents=True, exist_ok=True)
    _check_disk_reserve(pkg_dir)
    checked_refs = _check_evidence_references(evidence_references)
    if checked_refs:
        if evidence_root is None:
            raise ValueError("evidence_root is required when "
                             "evidence_references are supplied")
        _verify_evidence_root(Path(evidence_root), checked_refs)

    checkpoint = {"run_state": "RUNNING", "created_at": _now(),
                  "states": list(CHECKPOINT_STATES),
                  "research_diagnostic_only": True,
                  "promotion_eligible": False,
                  "production_authorized": False,
                  "warning_path_authorized": False}
    ckpt_path = pkg_dir / "checkpoint.json"
    _atomic_write_json(ckpt_path, checkpoint)

    try:
        mec_env = build_mec_envelope(_synthetic_mec_fixture())
        fmx_env = build_fmx_envelope(_blocked_fmx_fixture())
        assert fmx_env["fmx_status"] == FMX_BLOCKED_PENDING_EXPLICIT_FREEZE
        assert fmx_env["matrix"]["status"] == "ABSENT"
        # The scaffold's references are file-bound to the package's own
        # envelope files, so they are written first and hashed on disk.
        _atomic_write_json(pkg_dir / "mec_schema.json", mec_env)
        _atomic_write_json(pkg_dir / "fmx_blocked.json", fmx_env)

        run_context = bind_artifact_envelope({
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
            "dirty_matrix_policy": "treated as absent; never inspected",
            "research_diagnostic_only": True,
            "promotion_eligible": False,
            "production_authorized": False,
            "warning_path_authorized": False})
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
            "open_residuals": [
                {"id": "FMX-01", "status": "BLOCKED",
                 "title": "no externally frozen environmental feature "
                          "matrix; freeze token required"},
                {"id": "R04", "status": "OPEN",
                 "title": "merge requires explicit human approval"}],
            "deferred_residuals": [
                {"id": f"C{i:02d}", "status": "DEFERRED"}
                for i in range(1, 8)],
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
        if name == "run_context.json":
            refs = doc.get("evidence_references") or []
            if refs:
                if evidence_root is None:
                    problems.append("run_context.json declares evidence "
                                    "references but no evidence_root "
                                    "was supplied — unverified")
                else:
                    try:
                        _verify_evidence_root(Path(evidence_root), refs)
                    except ValueError as exc:
                        problems.append(f"evidence references: {exc}")

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

    for path in pkg_dir.iterdir():
        if path.is_file() and path.name.startswith(("e_", "f_", "E_",
                                                    "F_")):
            problems.append(f"forbidden E/F artifact present: "
                            f"{path.name}")
    return (not problems), problems
