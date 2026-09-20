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


def build_closure(*, index: Path, daily_root: Path, seasonal_root: Path,
                  owner_disposition: Path, suite: dict, output: Path) -> dict:
    index = Path(index).resolve()
    daily_root = Path(daily_root).resolve()
    seasonal_root = Path(seasonal_root).resolve()
    owner_disposition = Path(owner_disposition).resolve()
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
    env = _environment()
    reports = {
        "daily": _read_report("daily_p5a2", daily_root,
                              "retrieval/p5_replay_report_v3.json"),
        "seasonal": _read_report("seasonal_v1_current", seasonal_root,
                                  "run/seasonal_replay_report_v3.json"),
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
        "owner_gated": {
            "option3_approved_by": owner.get("approved_by"),
            "option3_status": owner.get("disposition"),
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
    parser.add_argument("--suite-json", required=True,
                        help="JSON object with passed/skipped/failed/"
                             "collected/warnings counts")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        suite = json.loads(args.suite_json)
        build_closure(index=Path(args.index),
                      daily_root=Path(args.daily_root),
                      seasonal_root=Path(args.seasonal_root),
                      owner_disposition=Path(args.owner_disposition),
                      suite=suite, output=Path(args.output))
    except (OSError, ValueError, json.JSONDecodeError, KeyError) as exc:
        print(f"RELEASE_CLOSURE_FAIL: {exc}")
        return 1
    print(json.dumps({"status": "RELEASE_CLOSURE_OK",
                      "output": str(Path(args.output).resolve())},
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
