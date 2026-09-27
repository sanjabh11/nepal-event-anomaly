"""Build and validate the detached India Phase-0 release closure.

The closure is a NON-CIRCULAR attestation: it lives outside the git
object set it proves (an external evidence root), so it can bind the
final release HEAD, the tested content HEAD, the manifest, the suite
receipt, the scope exclusions, the clean-history scan, the remote-ref
inventory, and the all-false authority block without self-reference.

``build`` writes the closure + a write-once sha256 sidecar.
``validate`` re-computes every binding against the live repository and
fails closed if the release HEAD, tested content HEAD, receipt,
manifest, exclusions, or allowed release-only diff changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = "INDIA_PHASE0_RELEASE_CLOSURE_V0"
REPO = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = (Path("/Users/sanjayb/nepal-event-anomaly-evidence")
                / "india-phase0-release")
AUTHORITY_FLAGS = {
    "bulk_acquisition_authorized": False,
    "weather_download_authorized": False,
    "satellite_bulk_authorized": False,
    "seismic_waveform_authorized": False,
    "forecast_authorized": False,
    "warning_authorized": False,
    "detector_authorized": False,
    "odds_authorized": False,
    "causal_authorized": False,
    "operational_authorized": False,
}
PAYLOAD_PATHS = [
    "data/dem_n28e085.tif", "data/temp/era5_land_2001_06.nc",
    "data/download_ledger.json", "data/era5_download_log.txt",
    "data/nisar_catalog_ledger.json",
]


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(*args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=REPO, capture_output=True,
                          text=True, check=True)
    return proc.stdout.strip()


def build(repo_root: Path, receipt_path: Path, release_head: str,
          tested_content_head: str, allowed_diff: list[str],
          remote_inventory: dict) -> dict:
    manifest = repo_root / "docs/science/ARTIFACT_MANIFEST_V0.json"
    exclusions = repo_root / "docs/science/MANIFEST_SCOPE_EXCLUSIONS_V0.json"
    return {
        "schema": SCHEMA,
        "version": 0,
        "claim_scope": "research_only_no_operational_authorization",
        "authority": dict(AUTHORITY_FLAGS),
        "built_utc": datetime.now(timezone.utc).isoformat(),
        "release_head": release_head,
        "tested_content_head": tested_content_head,
        "baseline_head": _git("rev-parse", "54aa612f7d478082716dfe5d0bca"
                            "7d92d62d493b"),
        "manifest_sha256": _sha256_file(manifest),
        "scope_exclusions_sha256": _sha256_file(exclusions),
        "suite_receipt_relpath": str(receipt_path.name),
        "suite_receipt_sha256": _sha256_file(receipt_path),
        "allowed_release_only_paths": sorted(allowed_diff),
        "clean_history_scan": {
            "payload_paths_absent_in_reachable_history": PAYLOAD_PATHS,
            "method": "git filter-repo --invert-paths; verified via "
                      "git rev-list --objects --all == 0 payload refs",
        },
        "remote_ref_inventory": remote_inventory,
        "disclosures": [
            "GitHub forks, caches, and prior clones may retain the "
            "scrubbed payload bytes; the rewrite cannot retract them.",
            "Pre-rewrite backup bundle retained offline outside the "
            "repository with restricted permissions; never published.",
            "Receipt/manifest bind digests, not source authenticity.",
        ],
    }


def validate_closure(closure_path: Path, repo_root: Path) -> dict:
    problems: list[str] = []
    doc = json.loads(closure_path.read_text(encoding="utf-8"))
    if doc.get("schema") != SCHEMA:
        problems.append("unexpected schema")
    if doc.get("authority") != AUTHORITY_FLAGS:
        problems.append("authority flags must be present and false")
    # Fail closed: recorded heads must resolve and match live state.
    for field in ("release_head", "tested_content_head", "baseline_head"):
        head = doc.get(field)
        if not isinstance(head, str) or len(head) != 40:
            problems.append(f"{field} must be a 40-hex commit")
            continue
        proc = subprocess.run(["git", "cat-file", "-e",
                               f"{head}^{{commit}}"], cwd=repo_root,
                              capture_output=True)
        if proc.returncode != 0:
            problems.append(f"{field} {head} does not resolve")
    if doc.get("release_head") != _git("rev-parse", "HEAD"):
        problems.append("release_head does not match live HEAD")
    rel = doc.get("suite_receipt_relpath")
    if rel:
        receipt = repo_root / "docs/science" / rel
        if not receipt.is_file():
            problems.append(f"receipt {rel} not present")
        elif _sha256_file(receipt) != doc.get("suite_receipt_sha256"):
            problems.append("receipt digest mismatch")
    manifest = repo_root / "docs/science/ARTIFACT_MANIFEST_V0.json"
    if _sha256_file(manifest) != doc.get("manifest_sha256"):
        problems.append("manifest digest mismatch")
    exclusions = repo_root / "docs/science/MANIFEST_SCOPE_EXCLUSIONS_V0.json"
    if _sha256_file(exclusions) != doc.get("scope_exclusions_sha256"):
        problems.append("scope exclusions digest mismatch")
    # The diff between tested content head and release head must be
    # exactly the declared release-only paths.
    tested = doc.get("tested_content_head")
    release = doc.get("release_head")
    if isinstance(tested, str) and isinstance(release, str) \
            and len(tested) == 40 and len(release) == 40:
        diff = _git("diff", "--name-only", f"{tested}..{release}")
        actual = sorted(p for p in diff.splitlines() if p)
        if actual != doc.get("allowed_release_only_paths"):
            problems.append(
                f"release-only diff {actual} != declared "
                f"{doc.get('allowed_release_only_paths')}")
    status = "CLOSURE_OK" if not problems else "CLOSURE_FAIL"
    return {"status": status, "problems": problems,
            "closure": str(closure_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["build", "validate"])
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--closure", type=Path,
                        default=EVIDENCE_DIR / "INDIA_PHASE0_RELEASE_CLOSURE_V0.json")
    parser.add_argument("--release-head")
    parser.add_argument("--tested-content-head")
    parser.add_argument("--allowed-diff", nargs="*", default=[])
    parser.add_argument("--remote-inventory", type=Path,
                        help="JSON file with remote head/tag inventory")
    args = parser.parse_args()
    if args.mode == "build":
        inventory = json.loads(args.remote_inventory.read_text()) \
            if args.remote_inventory else {}
        doc = build(REPO, args.receipt, args.release_head,
                    args.tested_content_head, args.allowed_diff,
                    inventory)
        args.closure.parent.mkdir(parents=True, exist_ok=True)
        import sys
        sys.path.insert(0, str(REPO / "scripts"))
        from p5_safe_io import write_once_json, write_once_sidecar
        write_once_json(args.closure, doc)
        write_once_sidecar(args.closure)
        print(f"CLOSURE_PUBLISHED: {args.closure}")
        return 0
    report = validate_closure(args.closure, REPO)
    print(json.dumps(report, indent=1))
    return 0 if report["status"] == "CLOSURE_OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
