#!/usr/bin/env python3
"""REPLAY-01 — independent replay of the P5 GLOF real-data chain.

Rehashes every manifest-bound byte in the evidence root, rebuilds the
derived artifacts deterministically, and compares digests end-to-end.
Any difference is a replay failure — promotion stays impossible.

Usage:
    PYTHONPATH=. .venv/bin/python -B scripts/replay_p5.py [--evidence-root PATH]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

DEFAULT_ROOT = Path(
    "/Users/sanjayb/nepal-event-anomaly-evidence/p5-glof-2026-09-19")


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def replay(evidence_root: Path) -> dict:
    root = Path(evidence_root)
    failures: list[str] = []
    report: dict = {"evidence_root": str(root), "checks": {}}

    # 1. every coverage-ledger entry rehashes to live bytes
    led = json.loads(
        (root / "retrieval/p5_coverage_ledger_20260919.json")
        .read_text())
    bad = []
    for e in led["files"]:
        p = root / e["relpath"]
        if not p.exists():
            bad.append(("missing", e["relpath"]))
        elif sha256_file(p) != e["sha256"]:
            bad.append(("digest", e["relpath"]))
        elif p.stat().st_size != e["size"]:
            bad.append(("size", e["relpath"]))
    report["checks"]["ledger_bytes"] = {
        "checked": len(led["files"]), "failures": bad}
    if bad:
        failures.append("ledger_bytes")

    # 2. every role manifest re-verifies through the governed floor
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from nepal.research_v0._hashing import verify_source_evidence
    manifests = json.loads(
        (root / "retrieval/role_manifests_v0.json").read_text())
    role_problems = {}
    for role, m in manifests.items():
        probs = verify_source_evidence(m)
        if probs:
            role_problems[role] = probs
    report["checks"]["role_manifests"] = {
        "roles": sorted(manifests), "problems": role_problems}
    if role_problems:
        failures.append("role_manifests")

    # 3. runner package section digests recompute from content
    pkg = json.loads(
        (root / "glof-events/p3_runner_package_v0.json").read_text())
    from nepal.research_v0._hashing import sha256_canonical
    dig_ok = {}
    for section, dkey in (
            ("event_labels", "event_digest"),
            ("opportunities", "opportunity_digest"),
            ("controls", "control_digest"),
            ("holdout_plan", "holdout_digest")):
        recomputed = sha256_canonical(pkg[section])
        dig_ok[dkey] = recomputed == pkg[dkey]
        if not dig_ok[dkey]:
            failures.append(f"digest:{dkey}")
    report["checks"]["package_digests"] = dig_ok

    # 4. package sidecar + wrapper digest stability (order-invariance:
    # canonical JSON hashing is order-free by construction)
    pkg_file = root / "glof-events/p3_runner_package_v0.json"
    side = pkg_file.with_name(pkg_file.name + ".sha256")
    side_ok = side.exists() and \
        side.read_text().split()[0] == sha256_file(pkg_file)
    report["checks"]["package_sidecar"] = side_ok
    if not side_ok:
        failures.append("package_sidecar")
    w = pkg.get("run_evidence_manifest")
    if w:
        report["checks"]["wrapper_digest_present"] = \
            bool(w.get("run_evidence_digest"))

    # 5. regime receipt is deterministic-carried (digest sidecar)
    rcpt = root / "retrieval/p5_glof_descriptive_receipt_v0.json"
    if rcpt.exists():
        rs = rcpt.with_name(rcpt.name + ".sha256")
        ok = rs.exists() and \
            rs.read_text().split()[0] == sha256_file(rcpt)
        report["checks"]["receipt_sidecar"] = ok
        if not ok:
            failures.append("receipt_sidecar")
        body = json.loads(rcpt.read_text())
        report["checks"]["receipt_authority"] = {
            "promotion_eligible": body.get("promotion_eligible"),
            "production_authorized":
                body.get("production_authorized"),
            "warning_path_authorized":
                body.get("warning_path_authorized")}
        if any(report["checks"]["receipt_authority"].values()):
            failures.append("authority_flags")

    report["status"] = "REPLAY_FAIL" if failures else "REPLAY_OK"
    report["failures"] = failures
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence-root", default=str(DEFAULT_ROOT))
    args = ap.parse_args()
    report = replay(Path(args.evidence_root))
    out = Path(args.evidence_root) / "retrieval" / \
        "p5_replay_report_v0.json"
    b = json.dumps(report, indent=2, sort_keys=True).encode()
    out.write_bytes(b)
    out.with_suffix(".json.sha256").write_text(
        hashlib.sha256(b).hexdigest() + "\n")
    print(json.dumps(report, indent=1)[:2000])
    print(f"\nstatus: {report['status']}  -> {out}")
    return 0 if report["status"] == "REPLAY_OK" else 1


if __name__ == "__main__":
    sys.exit(main())
