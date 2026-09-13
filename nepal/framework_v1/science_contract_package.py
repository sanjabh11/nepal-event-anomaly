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
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from .feature_matrix_contract import (FMX_BLOCKED_PENDING_EXPLICIT_FREEZE,
                                      build_fmx_envelope)
from .multi_event_contract import build_mec_envelope
from .provenance import (bind_artifact_envelope, sha256_file,
                         verify_artifact_envelope)
from .research_boundaries import lint_research_claims
from .validation_scaffold import (BLOCKED_PENDING_FMX,
                                  build_scaffold_envelope)

PACKAGE_STATUS = "SCIENCE_CONTRACT_SCAFFOLD_READY_WITH_FMX_BLOCKED"
PACKAGE_INDEX_TYPE = "SCIENCE_CONTRACT_PACKAGE_INDEX_V1"
HANDOFF_TYPE = "RESEARCH_CONTRACT_HANDOFF_V1"
MIN_FREE_GIB = 8.0
CHECKPOINT_STATES = ("RUNNING", "INCOMPLETE", "BLOCKED", "PASS")

_REQUIRED_FILES = ("run_context.json", "mec_schema.json",
                   "fmx_blocked.json", "validation_scaffold.json",
                   "research_no_claims.json",
                   "science_contract_package_index.json",
                   "research_contract_handoff.json", "checkpoint.json")


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
    def ev(i: int, group: str, day: int) -> dict[str, Any]:
        return {
            "event_id": f"SYNTH-EVT-{i:03d}",
            "event_group_id": group,
            "synthetic": True,
            "date_spec": {"precision": "day",
                          "date": f"2015-04-{day:02d}",
                          "source": "synthetic_fixture_date"},
            "holdout_group": f"H{i % 2}",
            "windows": {
                "acquisition_window": {"start": "2015-04-01",
                                       "end": "2015-04-10"},
                "publication_time": "2015-05-01",
                "feature_availability_time": {"start": "2015-04-01",
                                              "end": "2015-04-02"},
                "target_window": {"start": f"2015-04-{day:02d}",
                                  "end": f"2015-04-{day:02d}"}},
            "row_sha256": f"{i:064x}"[-64:]}
    return {
        "envelope_type": "MULTI_EVENT_CONTRACT_V1",
        "profile_id": "SCIENCE_CONTRACT_T2_RESEARCH",
        "research_only": True,
        "research_diagnostic_only": True,
        "synthetic_fixture": True,
        "source_catalog": {
            "catalog_id": "synthetic-catalog-v0",
            "source_sha256": "ab" * 32,
            "asset_ids": ["SYNTH-ASSET-1"],
            "processing_script_sha256": "cd" * 32},
        "events": [ev(1, "G1", 3), ev(2, "G2", 10), ev(3, "G3", 21),
                   ev(4, "G4", 5)],
        "holdout": {"assigned_before_filtering": True,
                    "temporal_embargo_days": 30,
                    "geographic_holdout": {"min_separation_km": 50.0},
                    "event_separation": {"group_disjoint": True}},
        "validation_scope": {"scope_id": "synthetic-regional-split",
                             "n_geographic_regions": 3,
                             "min_events": 4},
        "claim_scope": "synthetic contract exercise only",
        "promotion_eligible": False,
        "production_authorized": False}


def _blocked_fmx_fixture() -> Mapping[str, Any]:
    """Synthetic FMX metadata that can only produce the blocked status."""
    return {
        "matrix_id": "fmx-synthetic-unfrozen",
        "candidate_generation_id": "synthetic-gen-0",
        "source_artifact_id": "synthetic-source-1",
        "source_sha256": "ab" * 32,
        "byte_count": 1,
        "producer_sha256": "cd" * 32,
        "feature_contract_sha256": "ef" * 32,
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


def build_science_contract_package(
        *, package_dir: str | Path, run_root_name: str,
        code_revision: str, candidate_generation_id: str,
        ranked_array_canonical_sha256: str,
        ranked_payload_sha256: str,
        ranked_payload_sha256_domain_status: str,
        b_status: str = "B_TO_C_BLOCKED",
        evidence_references: Optional[list] = None
        ) -> dict[str, Any]:
    """Build the external research package.  Returns the package index.

    Only writes inside ``package_dir``.  Raises ``RuntimeError`` when the
    disk reserve is violated; the checkpoint is left ``INCOMPLETE`` if the
    build fails partway.
    """
    pkg_dir = Path(package_dir)
    pkg_dir.mkdir(parents=True, exist_ok=True)
    _check_disk_reserve(pkg_dir)

    checkpoint = {"run_state": "RUNNING", "created_at": _now(),
                  "states": list(CHECKPOINT_STATES)}
    ckpt_path = pkg_dir / "checkpoint.json"
    _atomic_write_json(ckpt_path, checkpoint)

    try:
        mec_env = build_mec_envelope(_synthetic_mec_fixture())
        fmx_env = build_fmx_envelope(_blocked_fmx_fixture(),
                                     freeze_token=None)
        assert fmx_env["fmx_status"] == FMX_BLOCKED_PENDING_EXPLICIT_FREEZE
        scaffold = build_scaffold_envelope({
            "mode": "VALIDATION_SCAFFOLD_ONLY",
            "research_diagnostic_only": True,
            "promotion_eligible": False,
            "references": {
                "mec_reference": {
                    "envelope_sha256": mec_env["artifact_sha256"],
                    "envelope_type": "MULTI_EVENT_CONTRACT_V1"},
                "fmx_reference": {
                    "envelope_sha256": fmx_env["artifact_sha256"],
                    "fmx_status": FMX_BLOCKED_PENDING_EXPLICIT_FREEZE},
                "b_reference": {
                    "status": b_status,
                    "ranked_array_canonical_sha256":
                        ranked_array_canonical_sha256}},
            "split_spec": {
                "temporal_embargo_days": 30,
                "geographic_holdout": {"min_separation_km": 50.0},
                "event_separation": {"group_disjoint": True}},
            "metric_registry": [
                {"metric_id": "calibration_slope",
                 "class": "calibration", "operational_threshold": None},
                {"metric_id": "auroc", "class": "discrimination",
                 "operational_threshold": None},
                {"metric_id": "lead_time_days", "class": "lead_time",
                 "operational_threshold": None},
                {"metric_id": "false_alarm_rate", "class": "false_alarm",
                 "operational_threshold": None}],
            "inherited_state": {"b_status": b_status,
                                "ranking_rerun": False,
                                "e_status": "E_BLOCKED",
                                "f_status": "F_BLOCKED"}})

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
            "evidence_references": list(evidence_references or []),
            "dirty_matrix_policy": "treated as absent; never inspected",
            "research_diagnostic_only": True,
            "promotion_eligible": False,
            "production_authorized": False,
            "warning_path_authorized": False})

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
            "promotion_eligible": False})
        _atomic_write_json(
            pkg_dir / "science_contract_package_index.json", index)

        checkpoint["run_state"] = "PASS"
        checkpoint["completed_at"] = _now()
        checkpoint["index_sha256"] = sha256_file(
            pkg_dir / "science_contract_package_index.json")
        _atomic_write_json(ckpt_path, checkpoint)
    except BaseException:
        checkpoint["run_state"] = "INCOMPLETE"
        checkpoint["failed_at"] = _now()
        _atomic_write_json(ckpt_path, checkpoint)
        raise
    return {"package_status": PACKAGE_STATUS, "package_dir": str(pkg_dir)}


def verify_science_contract_package(
        package_dir: str | Path) -> tuple[bool, list[str]]:
    """Re-read and re-verify a package from disk: presence, per-file
    hashes, envelope self-hashes, checkpoint state, claim lint, and the
    explicit FMX/B blocked statuses."""
    problems: list[str] = []
    pkg_dir = Path(package_dir)
    if not pkg_dir.is_dir():
        return False, [f"package directory missing: {package_dir}"]

    for name in _REQUIRED_FILES:
        if not (pkg_dir / name).is_file():
            problems.append(f"required package file missing: {name}")

    ckpt_path = pkg_dir / "checkpoint.json"
    if ckpt_path.is_file():
        try:
            ckpt = json.loads(ckpt_path.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            problems.append(f"checkpoint unreadable: {exc}")
            ckpt = {}
        if ckpt.get("run_state") != "PASS":
            problems.append(
                f"checkpoint run_state {ckpt.get('run_state')!r} is not "
                "PASS — the package did not complete")

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
            for entry in index.get("files", []):
                rel = entry.get("relative_path")
                if not isinstance(rel, str) or rel.startswith("/") or \
                        ".." in rel.split("/"):
                    problems.append(f"unsafe index path {rel!r}")
                    continue
                target = pkg_dir / rel
                if not target.is_file():
                    problems.append(f"indexed file missing: {rel}")
                    continue
                if sha256_file(target) != entry.get("sha256"):
                    problems.append(f"indexed file checksum mismatch: "
                                    f"{rel}")

    for name in ("run_context.json", "mec_schema.json",
                 "fmx_blocked.json", "validation_scaffold.json",
                 "research_no_claims.json",
                 "research_contract_handoff.json"):
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

    fmx_path = pkg_dir / "fmx_blocked.json"
    if fmx_path.is_file():
        fmx_doc = json.loads(fmx_path.read_text("utf-8"))
        if fmx_doc.get("fmx_status") != \
                FMX_BLOCKED_PENDING_EXPLICIT_FREEZE:
            problems.append("fmx_blocked.json must carry "
                            "FMX_BLOCKED_PENDING_EXPLICIT_FREEZE")
    scaffold_path = pkg_dir / "validation_scaffold.json"
    if scaffold_path.is_file():
        sc_doc = json.loads(scaffold_path.read_text("utf-8"))
        if sc_doc.get("scaffold_status") != BLOCKED_PENDING_FMX:
            problems.append("validation_scaffold.json must carry "
                            "BLOCKED_PENDING_FMX")

    for path in pkg_dir.iterdir():
        if path.is_file() and path.name.startswith(("e_", "f_", "E_",
                                                    "F_")):
            problems.append(f"forbidden E/F artifact present: "
                            f"{path.name}")
    return (not problems), problems
