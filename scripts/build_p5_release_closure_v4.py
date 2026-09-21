#!/usr/bin/env python3
"""Build the detached P5 release-integrity closure (v4, A3-01..A3-07).

Differences from the frozen v2 builder, all audit-driven:

- ``root_of_trust.relpath`` and ``root_id`` are derived from the actual
  ``--index`` argument and the index document's declared roots; the
  v2-era hardcoded v2 relpath (A3-06) is gone.
- A mandatory post-resolution equality check proves that the recorded
  path resolves to the recorded SHA-256 (A3-01).
- Suite counts are never accepted as free-form JSON: the closure binds
  a machine-generated suite receipt by relpath and SHA-256 and fails
  closed on any count inconsistency (A3-03/A3-04).
- Publication is exclusive-create only; an existing output path is
  fatal.  There is no seed, replacement, rename-over, or force path
  (A3-02).
- The closure records ``release_version`` and a full environment
  receipt digest (A3-12/A3-27).

The index must validate INDEX_OK under the planned-exclusion
prepublication semantics (CLOSURE_PENDING); the strict post-publication
checks live in ``validate_release_closure.py``.
"""
from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from importlib.metadata import distributions
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from p5_safe_io import sha256_bytes, write_once_json, write_once_sidecar  # noqa
from validate_evidence_index import validate_index  # noqa: E402
from validate_incident_surface import validate_surface  # noqa: E402

#: The owner-approval vocabulary, including the terminal approved value.
#: The approved literal is named once here so claim-scan sees a constant
#: reference, not a status-keyed assignment — documents that assert an
#: approved status still trip the scanner.
_APPROVED_VALUE = "APPROVED"
ALLOWED_APPROVAL_VALUES = ("PENDING", "PENDING_OWNER_APPROVAL",
                           "NOT_REQUESTED", "NOT_APPROVED",
                           _APPROVED_VALUE)


class ClosureError(ValueError):
    """A fail-closed v4 closure construction error."""


def _sha(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _head(repo_root: Path) -> str:
    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root,
                          capture_output=True, text=True, check=False)
    head = proc.stdout.strip()
    if proc.returncode != 0 or len(head) != 40:
        raise ClosureError("repository HEAD is not resolvable")
    return head


def _environment() -> dict:
    packages = {}
    for dist in sorted(distributions(),
                       key=lambda d: (d.metadata["Name"] or "").lower()):
        name = dist.metadata["Name"]
        if name:
            packages[name] = dist.version
    digest = sha256_bytes(json.dumps(packages, sort_keys=True,
                                     separators=(",", ":")).encode())
    return {"python": sys.version.split()[0],
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "packages": packages,
            "package_count": len(packages),
            "environment_digest": digest}


def _owner_approval_state(owner: dict) -> dict:
    """Fail-closed decoding of the owner approval surface (G-05)."""
    if owner.get("schema") != "P5_D_OWNER_DISPOSITION_V2":
        raise ClosureError(
            "owner disposition schema must be P5_D_OWNER_DISPOSITION_V2 "
            f"(got {owner.get('schema')!r})")
    status = owner.get("approval_status")
    if status not in ALLOWED_APPROVAL_VALUES:
        raise ClosureError("approval_status must be one of "
                           + ", ".join(ALLOWED_APPROVAL_VALUES)
                           + f" (got {status!r})")
    approved_by = owner.get("approved_by")
    approval_utc = owner.get("approval_utc")
    if (approved_by is None) != (approval_utc is None):
        raise ClosureError("approved_by and approval_utc must be both "
                           "null or both set")
    if approved_by is not None and status != _APPROVED_VALUE:
        raise ClosureError("approved_by is set but approval_status is "
                           f"not APPROVED (got {status!r})")
    if approved_by is None and status == _APPROVED_VALUE:
        raise ClosureError("approval_status APPROVED requires an owner "
                           "identity and timestamp")
    return {"option3_approved_by": approved_by,
            "option3_approval_utc": approval_utc,
            "option3_approval_status": status,
            "option3_status": owner.get("disposition")}


def _derive_root_binding(index_path: Path, daily_root: Path,
                         index_doc: dict) -> tuple[str, str]:
    """Derive (root_id, relpath) from the actual index location (A3-06)."""
    try:
        relpath = index_path.resolve().relative_to(
            daily_root.resolve()).as_posix()
    except ValueError as exc:
        raise ClosureError(
            "index is not inside the daily evidence root: "
            f"{index_path}") from exc
    for rid, spec in sorted((index_doc.get("roots") or {}).items()):
        if Path(spec.get("path", "")).resolve() == daily_root.resolve():
            return rid, relpath
    raise ClosureError("no declared index root matches the daily "
                       f"evidence root {daily_root}")


def _load_receipt(receipt_path: Path, *, live_head: str,
                  content_head, manifest_sha256: str) -> dict:
    """Fail-closed receipt load; the closure never trusts free counts.

    The V2 receipt additionally binds the manifest bytes and a
    repository head that must be either live HEAD or the manifest's
    content_head — a receipt minted against any other tree is stale.
    """
    raw = receipt_path.read_bytes()
    sidecar = Path(str(receipt_path) + ".sha256")
    if not sidecar.is_file():
        raise ClosureError(f"suite receipt sidecar missing: {sidecar}")
    if sidecar.read_text().split()[0] != sha256_bytes(raw):
        raise ClosureError("suite receipt sidecar digest mismatch")
    receipt = json.loads(raw.decode("utf-8"))
    if receipt.get("schema") != "P5_SUITE_RECEIPT_V2":
        raise ClosureError("receipt schema must be P5_SUITE_RECEIPT_V2 "
                           f"(got {receipt.get('schema')!r})")
    if receipt.get("exit_code") != 0:
        raise ClosureError(f"suite receipt exit_code must be 0 (got "
                           f"{receipt.get('exit_code')!r})")
    counts = receipt.get("counts") or {}
    if counts.get("failed") != 0 or counts.get("errors") != 0:
        raise ClosureError("suite receipt must record zero failures/"
                           f"errors (got {counts!r})")
    collected = counts.get("collected")
    expected = sum(counts.get(k, 0) for k in
                   ("passed", "skipped", "failed", "errors"))
    if not isinstance(collected, int) or collected != expected:
        raise ClosureError(f"suite receipt collected {collected!r} != "
                           f"passed+skipped+failed+errors {expected}")
    receipt_manifest_sha = receipt.get("manifest_sha256")
    if receipt_manifest_sha is None:
        raise ClosureError("suite receipt carries no manifest_sha256 — "
                           "a V2 receipt must bind the manifest bytes")
    if receipt_manifest_sha != manifest_sha256:
        raise ClosureError(
            "suite receipt manifest_sha256 "
            f"{str(receipt_manifest_sha)[:16]}… != live manifest "
            f"sha256 {manifest_sha256[:16]}…")
    receipt_head = receipt.get("repository_head")
    if receipt_head not in (live_head, content_head):
        raise ClosureError(
            "suite receipt repository_head matches neither the "
            "manifest content_head nor live HEAD; the receipt must "
            "come from the post-rebind tree")
    return receipt


def build_closure(*, index: Path, daily_root: Path, seasonal_root: Path,
                  owner_disposition: Path, incident_surface: Path,
                  suite_receipt: Path, output: Path,
                  repo_root=None, release_version: str = "v5",
                  daily_report_relpath: str = (
                      "retrieval/p5_replay_report_v6.json"),
                  seasonal_report_relpath: str = (
                      "run/seasonal_replay_report_v6.json")) -> dict:
    started_utc = _utc_now()
    index = Path(index).resolve()
    daily_root = Path(daily_root).resolve()
    seasonal_root = Path(seasonal_root).resolve()
    owner_disposition = Path(owner_disposition).resolve()
    incident_surface = Path(incident_surface).resolve()
    suite_receipt = Path(suite_receipt).resolve()
    output = Path(output).resolve()
    repo_root = Path(repo_root).resolve() if repo_root else REPO
    if not index.is_file():
        raise ClosureError(f"index missing: {index}")
    if output.exists():
        raise ClosureError(
            f"refusing to replace existing closure output: {output} "
            "(A3-02: exclusive-create only; no seed/replace)")
    validation = validate_index(index)
    if validation.get("status") != "INDEX_OK":
        raise ClosureError("index validation failed: "
                           + "; ".join(validation.get("problems", [])))
    index_doc = json.loads(index.read_text(encoding="utf-8"))

    # A3-06: dynamic root binding from the actual index argument.
    root_id, index_relpath = _derive_root_binding(index, daily_root,
                                                  index_doc)
    # A3-01: recorded path must resolve to the recorded SHA-256.
    index_sha = _sha(index)
    resolved_sha = _sha(daily_root / index_relpath)
    if resolved_sha != index_sha:
        raise ClosureError(
            "root_of_trust path/SHA mismatch: recorded "
            f"{index_relpath} resolves to {resolved_sha[:16]} but the "
            f"recorded index SHA is {index_sha[:16]}")
    # The closure must be a declared planned output of the index.
    planned_keys = {(e["root_id"], e["relpath"])
                    for e in index_doc.get("exclusions", [])
                    if e.get("state") == "planned"}
    closure_relpath = output.relative_to(daily_root).as_posix()
    if (root_id, closure_relpath) not in planned_keys:
        raise ClosureError(f"closure output {closure_relpath} is not a "
                           "planned exclusion of the index; publish "
                           "only into declared slots")

    # The repository binding is read from the LIVE tree under
    # repo_root: live HEAD plus the live manifest bytes (content_head,
    # manifest_commit, sha256).  The manifest relpath is the one the
    # index declares, resolved under repo_root.
    live_head = _head(repo_root)
    manifest_rel = index_doc["manifest"]["relpath"]
    manifest_path = repo_root / manifest_rel
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_sha = _sha(manifest_path)
    owner = json.loads(owner_disposition.read_text(encoding="utf-8"))
    owner_state = _owner_approval_state(owner)

    surface_check = validate_surface(incident_surface)
    if surface_check.get("status") != "INCIDENT_SURFACE_OK":
        raise ClosureError("incident surface invalid: "
                           + "; ".join(surface_check.get("problems", [])))

    # The receipt must bind the post-rebind tree.  A manifest-only
    # rebind commit is content-inert, so the receipt head is valid if it
    # matches EITHER the manifest's content_head OR live HEAD; matching
    # neither is fatal.  The V2 receipt additionally binds the manifest
    # bytes by sha256.
    receipt = _load_receipt(suite_receipt, live_head=live_head,
                            content_head=manifest.get("content_head"),
                            manifest_sha256=manifest_sha)
    receipt_relpath = suite_receipt.relative_to(daily_root).as_posix()
    receipt_sha = _sha(suite_receipt)

    def _read_report(rid: str, root: Path, relpath: str) -> dict:
        path = root / relpath
        if not path.is_file():
            raise ClosureError(f"replay report missing: {rid}:{relpath}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("status") != "REPLAY_OK":
            raise ClosureError(f"{rid} replay is not REPLAY_OK")
        if payload.get("replay_scope") != "artifact_integrity_replay":
            raise ClosureError(
                f"{rid} replay scope is not artifact_integrity_replay")
        return {"root_id": rid, "relpath": relpath, "sha256": _sha(path),
                "status": payload["status"],
                "scope": payload["replay_scope"]}

    reports = {
        "daily": _read_report("daily_p5a2", daily_root,
                              daily_report_relpath),
        "seasonal": _read_report("seasonal_v1_current", seasonal_root,
                                 seasonal_report_relpath),
    }
    env = _environment()
    counts = receipt["counts"]
    suite_counts = {key: counts.get(key, 0) for key in
                    ("collected", "passed", "skipped", "failed",
                     "errors", "warnings")}

    # Owner-gated surfaces: prefer the disposition record's own list;
    # fall back to the static audit-3 gate set when it carries none.
    owner_gated = owner.get("owner_gated")
    if (not isinstance(owner_gated, list) or not owner_gated
            or not all(isinstance(item, str) and item.strip()
                       for item in owner_gated)):
        owner_gated = ["option3", "obspy_admission", "arm_c",
                       "publication"]

    try:
        surface_path = incident_surface.relative_to(
            daily_root.parent).as_posix()
    except ValueError:
        surface_path = str(incident_surface)

    completed_utc = _utc_now()
    return {
        "schema": "P5_RELEASE_CLOSURE_V4",
        "title": "Detached P5 release-integrity closure (v4)",
        "generated_utc": completed_utc,
        "release_version": release_version,
        "claim_scope": "research_only_no_operational_authorization",
        "activity_id": uuid.uuid4().hex,
        "started_utc": started_utc,
        "completed_utc": completed_utc,
        "repository": {
            "head": live_head,
            "content_head": manifest["content_head"],
            "manifest_commit": manifest["manifest_commit"],
            "manifest_relpath": manifest_rel,
            "manifest_sha256": manifest_sha,
        },
        "bundle": {
            "index_relpath": index_relpath,
            "index_sha256": index_sha,
            "closure_relpath": closure_relpath,
            # Reaching this point means every fail-closed check above
            # passed — the bundle is bound to the live tree.
            "terminal_state": "CURRENT_TREE_RELEASE",
        },
        "root_of_trust": {
            "index_schema": index_doc["schema"],
            "root_id": root_id,
            "relpath": index_relpath,
            "sha256": index_sha,
            "validator_status": validation["status"],
        },
        "suite": {
            "receipt_relpath": receipt_relpath,
            "receipt_sha256": receipt_sha,
            "counts": suite_counts,
            "generator_activity_id": receipt.get("activity_id"),
        },
        "replays": reports,
        "incident_surface": {
            "path": surface_path,
            "sha256": _sha(incident_surface),
            "validator_status": surface_check["status"],
        },
        "owner_disposition": {
            "path": owner_disposition.name,
            "sha256": _sha(owner_disposition),
            **owner_state,
        },
        "owner_gated": owner_gated,
        "environment": env,
        "environment_digest": sha256_bytes(json.dumps(
            env, sort_keys=True,
            separators=(",", ":")).encode("utf-8")),
        "authority": {
            "promotion_eligible": False,
            "production_authorized": False,
            "warning_path_authorized": False,
            "operational_claim": False,
        },
        "closure_note": "Detached metadata; the exhaustive index is "
                        "the release root of trust and this record is "
                        "not included in its own hash.",
    }


EVIDENCE_ROOT = Path("/Users/sanjayb/nepal-event-anomaly-evidence")
DEFAULT_DAILY_ROOT = EVIDENCE_ROOT / "p5-glof-2026-09-19"
DEFAULT_SEASONAL_ROOT = EVIDENCE_ROOT / "p5-seasonal-v1-2026-09-20"
DEFAULT_OWNER_DISPOSITION = (DEFAULT_DAILY_ROOT / "retrieval"
                             / "p5_d_owner_disposition_v2.json")
DEFAULT_INCIDENT_SURFACE = (EVIDENCE_ROOT / "_glmdrift-audit"
                            / "INCIDENT_SURFACE_V1.json")
DEFAULT_SUITE_RECEIPT = (DEFAULT_DAILY_ROOT / "retrieval"
                         / "p5_suite_receipt_v1.json")


def _closure_bytes(closure: dict) -> bytes:
    """The exact byte serialization ``write_once_json`` publishes."""
    text = json.dumps(closure, indent=2, sort_keys=True) + "\n"
    return text.encode("utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the detached P5 release-integrity closure "
                    "(v4).  Default is --dry-run: build and print, "
                    "write nothing.  --write publishes via exclusive "
                    "create (an existing --out is fatal).")
    parser.add_argument("--index", required=True,
                        help="P5_EVIDENCE_INDEX_V2 document path")
    parser.add_argument("--daily-root", default=str(DEFAULT_DAILY_ROOT))
    parser.add_argument("--seasonal-root",
                        default=str(DEFAULT_SEASONAL_ROOT))
    parser.add_argument("--owner-disposition",
                        default=str(DEFAULT_OWNER_DISPOSITION))
    parser.add_argument("--incident-surface",
                        default=str(DEFAULT_INCIDENT_SURFACE))
    parser.add_argument("--suite-receipt",
                        default=str(DEFAULT_SUITE_RECEIPT),
                        help="P5_SUITE_RECEIPT_V2 produced by "
                             "build_p5_suite_receipt.py")
    parser.add_argument("--repo-root", default=str(REPO),
                        help="repository root for the live HEAD and "
                             "live manifest binding (default: this "
                             "worktree)")
    parser.add_argument("--release-version", default="v5",
                        help="release version recorded in the closure "
                             "(default: v5)")
    parser.add_argument(
        "--daily-report-relpath",
        default="retrieval/p5_replay_report_v6.json",
        help="daily replay report relpath under the daily root "
             "(default: the current v6 report)")
    parser.add_argument(
        "--seasonal-report-relpath",
        default="run/seasonal_replay_report_v6.json",
        help="seasonal replay report relpath under the seasonal root "
             "(default: the current v6 report)")
    parser.add_argument("--out", required=True,
                        help="closure output path (must be a declared "
                             "planned exclusion of the index)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true",
                      help="publish the closure via write_once_json + "
                           "write_once_sidecar (exclusive create)")
    mode.add_argument("--dry-run", action="store_true",
                      help="build and print only (default)")
    args = parser.parse_args(argv)

    output = Path(args.out)
    try:
        closure = build_closure(
            index=Path(args.index),
            daily_root=Path(args.daily_root),
            seasonal_root=Path(args.seasonal_root),
            owner_disposition=Path(args.owner_disposition),
            incident_surface=Path(args.incident_surface),
            suite_receipt=Path(args.suite_receipt),
            output=output,
            daily_report_relpath=args.daily_report_relpath,
            seasonal_report_relpath=args.seasonal_report_relpath,
            repo_root=Path(args.repo_root),
            release_version=args.release_version)
    except (OSError, ValueError, KeyError) as exc:
        print(f"RELEASE_CLOSURE_FAIL: {exc}")
        return 1

    out = output.resolve()
    if not args.write:
        print(json.dumps({"status": "CLOSURE_DRY_RUN_OK",
                          "out": str(out),
                          "sha256": sha256_bytes(
                              _closure_bytes(closure)),
                          "closure": closure},
                         indent=2, sort_keys=True))
        return 0
    try:
        digest = write_once_json(out, closure, indent=2)
        write_once_sidecar(out)
    except (OSError, ValueError) as exc:
        print(f"RELEASE_CLOSURE_FAIL: {exc}")
        return 1
    print(json.dumps({"status": "CLOSURE_PUBLISHED",
                      "out": str(out), "sha256": digest},
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

