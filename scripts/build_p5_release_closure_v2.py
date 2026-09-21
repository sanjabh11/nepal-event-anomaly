#!/usr/bin/env python3
"""Build the detached P5 release-integrity closure.

The exhaustive evidence index is the release root of trust.  This detached
record binds the index digest to the final local verification facts without
creating a circular index hash.  It is exclusive-create only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from p5_safe_io import sha256_bytes, write_once_json, write_once_sidecar
from validate_evidence_index import validate_index
from validate_incident_surface import validate_surface


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _head() -> str:
    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO,
                          capture_output=True, text=True, check=False)
    if proc.returncode != 0 or len(proc.stdout.strip()) != 40:
        raise ValueError("repository HEAD is not resolvable")
    return proc.stdout.strip()


def _environment() -> dict:
    packages = {}
    for name in ("numpy", "pandas", "scikit-learn"):
        try:
            packages[name] = version(name)
        except Exception:
            packages[name] = "unavailable"
    return {"python": sys.version.split()[0],
            "platform": platform.platform(), "packages": packages}


def _read_report(root_id: str, root: Path, relpath: str) -> dict:
    path = root / relpath
    if not path.is_file():
        raise ValueError(f"replay report missing: {root_id}:{relpath}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {"root_id": root_id, "relpath": relpath,
            "sha256": _sha(path), "status": payload.get("status"),
            "scope": payload.get("replay_scope")}


ALLOWED_APPROVAL_STATUS = ("PENDING", "PENDING_OWNER_APPROVAL",
                           "NOT_REQUESTED", "NOT_APPROVED", "APPROVED")


def _owner_approval_state(owner: dict) -> dict:
    """Fail-closed decoding of the owner approval surface (G-05).

    The consumed v1 record carried no approval fields at all; "absent" is
    not "null" and cannot support a not-approved assertion.  The superseding
    v2 record must state the triple explicitly, and an approval may only be
    represented by a real owner identity plus a timestamp.
    """
    schema = owner.get("schema")
    if schema != "P5_D_OWNER_DISPOSITION_V2":
        raise ValueError(
            "owner disposition schema must be P5_D_OWNER_DISPOSITION_V2 "
            f"(got {schema!r}) — v1 has no explicit approval fields")
    status = owner.get("approval_status")
    if status not in ALLOWED_APPROVAL_STATUS:
        raise ValueError("approval_status must be one of "
                         + ", ".join(ALLOWED_APPROVAL_STATUS)
                         + f" (got {status!r})")
    approved_by = owner.get("approved_by")
    approval_utc = owner.get("approval_utc")
    if (approved_by is None) != (approval_utc is None):
        raise ValueError("approved_by and approval_utc must be both null "
                         "or both set")
    if approved_by is not None and status != "APPROVED":
        raise ValueError("approved_by is set but approval_status is not "
                         "APPROVED")
    if approved_by is None and status == "APPROVED":
        raise ValueError("approval_status APPROVED requires an owner "
                         "identity and timestamp")
    return {"option3_approved_by": approved_by,
            "option3_approval_utc": approval_utc,
            "option3_approval_status": status,
            "option3_status": owner.get("disposition")}


def build_closure(*, index: Path, daily_root: Path, seasonal_root: Path,
                  owner_disposition: Path, suite: dict, output: Path,
                  incident_surface: Path,
                  daily_report_relpath: str = (
                      "retrieval/p5_replay_report_v3.json"),
                  seasonal_report_relpath: str = (
                      "run/seasonal_replay_report_v3.json")) -> dict:
    index = Path(index).resolve()
    daily_root = Path(daily_root).resolve()
    seasonal_root = Path(seasonal_root).resolve()
    owner_disposition = Path(owner_disposition).resolve()
    incident_surface = Path(incident_surface).resolve()
    if not index.is_file():
        raise ValueError(f"index missing: {index}")
    validation = validate_index(index)
    if validation.get("status") != "INDEX_OK":
        raise ValueError("index validation failed: "
                         + "; ".join(validation.get("problems", [])))
    index_doc = json.loads(index.read_text(encoding="utf-8"))
    manifest_path = REPO / index_doc["manifest"]["relpath"]
    manifest_sha = _sha(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    owner = json.loads(owner_disposition.read_text(encoding="utf-8"))
    owner_state = _owner_approval_state(owner)
    try:
        owner_relpath = str(owner_disposition.relative_to(
            daily_root)).replace(os.sep, "/")
    except ValueError:
        owner_relpath = owner_disposition.name
    surface_check = validate_surface(incident_surface)
    if surface_check.get("status") != "INCIDENT_SURFACE_OK":
        raise ValueError("incident surface invalid: "
                         + "; ".join(surface_check.get("problems", [])))
    try:
        surface_relpath = str(incident_surface.relative_to(
            daily_root.parent)).replace(os.sep, "/")
    except ValueError:
        surface_relpath = incident_surface.name
    env = _environment()
    reports = {
        "daily": _read_report("daily_p5a2", daily_root,
                              daily_report_relpath),
        "seasonal": _read_report("seasonal_v1_current", seasonal_root,
                                  seasonal_report_relpath),
    }
    for name, report in reports.items():
        if report["status"] != "REPLAY_OK":
            raise ValueError(f"{name} replay is not REPLAY_OK")
        if report["scope"] != "artifact_integrity_replay":
            raise ValueError(f"{name} replay scope is not integrity-only")
    required_suite = {"passed", "skipped", "failed", "collected",
                      "warnings"}
    if set(suite) != required_suite or any(
            not isinstance(suite[k], int) or suite[k] < 0 for k in suite):
        raise ValueError("suite must contain non-negative integer counts: "
                         + ", ".join(sorted(required_suite)))
    authority = {
        "promotion_eligible": False,
        "production_authorized": False,
        "warning_path_authorized": False,
        "operational_claim": False,
    }
    closure = {
        "schema": "P5_RELEASE_CLOSURE_V2",
        "title": "Detached P5 audit-3 release-integrity closure",
        "generated_utc": _utc_now(),
        "claim_scope": "research_only_no_operational_authorization",
        "root_of_trust": {
            "root_id": "daily_p5a2",
            "relpath": "retrieval/p5_evidence_index_v2.json",
            "sha256": _sha(index),
            "validator_status": validation["status"],
            "index_schema": index_doc["schema"],
        },
        "repository": {
            "head": _head(),
            "content_head": manifest["content_head"],
            "manifest_commit": manifest["manifest_commit"],
            "manifest_root_id": "repository",
            "manifest_relpath": "docs/science/ARTIFACT_MANIFEST_V0.json",
            "manifest_sha256": manifest_sha,
            "manifest_file_count": len(manifest["files"]),
        },
        "suite": suite,
        "replays": reports,
        "authority": authority,
        "incident_surface": {
            "logical_path": surface_relpath,
            "sha256": _sha(incident_surface),
            "schema": "INCIDENT_SURFACE_V1",
            "artifact_count": surface_check["artifact_count"],
            "validator_status": surface_check["status"],
            "main_release_excluded": True,
            "bound_in_main_index": False,
        },
        "owner_disposition": {
            "relpath": owner_relpath,
            "sha256": _sha(owner_disposition),
            "schema": owner.get("schema"),
        },
        "owner_gated": {
            **owner_state,
            "obspy_admission": "pending_owner_dependency_amendment",
            "arm_c": "deferred_separate_amendment",
            "publication": "deferred",
            "remote_ci": "not_run",
            "external_preservation": "not_run",
        },
        "environment": env,
        "environment_digest": sha256_bytes(
            json.dumps(env, sort_keys=True,
                       separators=(",", ":")).encode("utf-8")),
        "closure_note": "Detached metadata; the exhaustive index is the "
                        "release root of trust and this record is not "
                        "included in its own hash.",
    }
    write_once_json(output, closure, indent=2)
    write_once_sidecar(output)
    return closure


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", required=True)
    parser.add_argument("--daily-root", required=True)
    parser.add_argument("--seasonal-root", required=True)
    parser.add_argument("--owner-disposition", required=True)
    parser.add_argument("--incident-surface", required=True,
                        help="INCIDENT_SURFACE_V1 descriptor bound into the "
                             "closure; explicitly excluded from the index")
    parser.add_argument("--suite-json", required=True,
                        help="JSON object with passed/skipped/failed/"
                             "collected/warnings counts")
    parser.add_argument("--daily-report", default=None,
                        help="daily replay report relpath bound into the "
                             "closure (default: retrieval/"
                             "p5_replay_report_v3.json)")
    parser.add_argument("--seasonal-report", default=None,
                        help="seasonal replay report relpath bound into "
                             "the closure (default: run/"
                             "seasonal_replay_report_v3.json)")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        suite = json.loads(args.suite_json)
        build_closure(index=Path(args.index),
                      daily_root=Path(args.daily_root),
                      seasonal_root=Path(args.seasonal_root),
                      owner_disposition=Path(args.owner_disposition),
                      incident_surface=Path(args.incident_surface),
                      suite=suite, output=Path(args.output),
                      daily_report_relpath=(
                          args.daily_report
                          or "retrieval/p5_replay_report_v3.json"),
                      seasonal_report_relpath=(
                          args.seasonal_report
                          or "run/seasonal_replay_report_v3.json"))
    except (OSError, ValueError, json.JSONDecodeError, KeyError) as exc:
        print(f"RELEASE_CLOSURE_FAIL: {exc}")
        return 1
    print(json.dumps({"status": "RELEASE_CLOSURE_OK",
                      "output": str(Path(args.output).resolve())},
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
