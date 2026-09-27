#!/usr/bin/env python3
"""REPLAY-01 — artifact-integrity replay of the P5 GLOF real-data chain.

Rehashes every declared byte in the evidence root, rebuilds the
deterministic FMX/package/receipt integrity chain, and validates the
persisted regime artifact envelope and freeze digests.  This script does
NOT independently refit the regime model; its proof level is therefore
``artifact_integrity_replay``.  A future model-execution replay must use
the separate ``model_reexecuted`` scope after a complete configuration
and source inventory is available.

Usage:
    PYTHONPATH=. .venv/bin/python -B scripts/replay_p5.py \
        [--evidence-root PATH] [--report-out PATH]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ROOT = Path(
    "/Users/sanjayb/nepal-event-anomaly-evidence/p5-glof-2026-09-19")

#: Control documents the sidecar role must bind (R11.9-19).  Run
#: products (runner package, descriptive receipt, replay report) are
#: self-sidecarred instead — binding them inside the sidecar role
#: would be self-referential because the package embeds the wrapper.
CONTROL_DOCS = (
    "retrieval/p3_review_packet_v0.json",
    "retrieval/holdout_feature_gate_report.json",
    "retrieval/anchor_derivation_record.json",
    "retrieval/hydrology_adjudication_v0.json",
    "retrieval/p5_amendment_v2_temporal_holdout.json",
    "retrieval/p5_coverage_ledger_20260919.json",
    "retrieval/retrieval_record_era5_multibasin.json",
    "era5-multibasin/features/fmx_audit_report_v0.json",
    "era5-multibasin/features/cutoff_record_v0.json",
    "era5-multibasin/features/preprocessing_provenance_v0.json")

FRAME_RELPATH = \
    "era5-multibasin/features/regime_frame_hma_jja_2001_2025.csv"


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def rebuild_ledger_entry(root: Path, relpath: str) -> dict:
    """Recompute one declared ledger entry's fields from live bytes —
    size, digest, and the ``sidecar`` flag are re-derived, never
    trusted."""
    p = root / relpath
    return {
        "relpath": relpath,
        "size": p.stat().st_size,
        "sha256": sha256_file(p),
        "sidecar": p.with_name(p.name + ".sha256").exists()}


def replay(evidence_root: Path) -> dict:
    root = Path(evidence_root)
    import uuid as _uuid
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    failures: list[str] = []
    report: dict = {
        "schema": "P5_REPLAY_REPORT_V5",
        "activity_id": _uuid.uuid4().hex,
        "started_utc": started,
        "evidence_root_id": "daily_p5a2",
        "execution_context": {
            "host_evidence_root": str(root),
            "note": "host paths are execution context only — evidence "
                    "references are logical (root_id + relpath)"},
        "replay_scope": "artifact_integrity_replay",
        "model_reexecution": {
            "status": "NOT_RUN",
            "reason": "this replay validates persisted bytes and "
                      "does not invoke run_regimes; an independent "
                      "model_reexecuted run requires a separate "
                      "execution record",
        },
                    "checks": {}}

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

    # 1b. every declared ledger entry's fields re-derive from disk —
    # the ledger is a declared ACQUISITION-payload coverage set, so
    # derived run products outside it are informational, not drift
    field_mismatch = []
    for e in led["files"]:
        p = root / e["relpath"]
        if not p.exists():
            continue  # already flagged by ledger_bytes
        rebuilt = rebuild_ledger_entry(root, e["relpath"])
        # entries may carry annotation fields (status/note); only the
        # re-derived evidence fields are compared
        # Sidecars are release-integrity metadata and may be materialized
        # after the acquisition ledger was written.  The historical ledger
        # remains immutable; only its payload size/digest are replay-bound.
        if any(rebuilt[k] != e.get(k)
               for k in ("size", "sha256")):
            field_mismatch.append(e["relpath"])
    disk_payloads = {
        str(p.relative_to(root)) for p in root.rglob("*")
        if p.is_file() and not p.name.endswith(".sha256")}
    declared = {e["relpath"] for e in led["files"]}
    report["checks"]["ledger_rebuild"] = {
        "field_mismatch": field_mismatch,
        "derived_outside_ledger": sorted(disk_payloads - declared)}
    if field_mismatch:
        failures.append("ledger_rebuild")

    # 2. every role manifest re-verifies through the governed floor
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from nepal.research_v0._hashing import (
        sha256_canonical, verify_source_evidence)
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

    # 2b. the sidecar role binds every declared control document that
    # exists on disk, plus every review report consumed by the packet
    sidecar = manifests.get("sidecar", {})
    bound = {f.get("relpath")
             for f in sidecar.get("source_files", [])
             if isinstance(f, dict)}
    required = {d for d in CONTROL_DOCS if (root / d).exists()}
    required |= {str(p.relative_to(root))
                 for p in root.glob("retrieval/*review*.json")}
    required |= {str(p.relative_to(root))
                 for p in root.glob("retrieval/*adjudication*.json")}
    unbound = sorted(required - bound)
    report["checks"]["sidecar_control_docs"] = {
        "required": sorted(CONTROL_DOCS), "unbound": unbound}
    if unbound:
        failures.append("sidecar_control_docs")

    # 3. runner package section digests recompute from content
    pkg = json.loads(
        (root / "glof-events/p3_runner_package_v0.json").read_text())
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

    # 3b. semantic role binding — package/frame/FMX all ride the roles
    import nepal.research_v0.records  # noqa: F401 — records must
    # initialize before run_evidence (module cycle)
    from nepal.research_v0.run_evidence import (
        semantic_binding_problems)
    import pandas as pd
    frame_path = root / FRAME_RELPATH
    frame_cols = list(pd.read_csv(frame_path, nrows=1).columns) \
        if frame_path.exists() else None
    fmx_path = root / "era5-multibasin/features/" \
        "fmx_audit_report_v0.json"
    fmx_persisted = json.loads(fmx_path.read_text()) \
        if fmx_path.exists() else None
    from nepal.real_fmx import _CARRIER_META
    sem_problems = semantic_binding_problems(
        pkg.get("run_evidence_manifest"), pkg,
        frame_relpath=FRAME_RELPATH,
        frame_columns=frame_cols,
        carrier_columns=list(_CARRIER_META),
        fmx_report=fmx_persisted,
        control_doc_relpaths=CONTROL_DOCS)
    report["checks"]["semantic_binding"] = sem_problems
    if sem_problems:
        failures.append("semantic_binding")

    # 3c. review-packet integrity — blank slots are HONEST (awaiting
    # humans); filled slots must carry distinct identities, existing
    # digest-bound report files, and adjudicator-last ordering.
    # Nothing else in the chain validates a filled slot, so a garbage
    # fill would pass silently without this check.
    pkt_path = root / "retrieval/p3_review_packet_v0.json"
    if pkt_path.exists():
        pkt = json.loads(pkt_path.read_text())
        slots = {r: pkt.get(r) for r in
                 ("reviewer_1", "reviewer_2", "adjudicator")}
        filled = {r: s for r, s in slots.items()
                  if isinstance(s, dict) and s.get("id")}
        pkt_problems = []
        ids = [s["id"] for s in filled.values()]
        if len(ids) != len(set(ids)):
            pkt_problems.append("duplicate reviewer identity")
        for role, s in filled.items():
            if not s.get("decision") or not s.get("utc") or \
                    not s.get("report_sha256"):
                pkt_problems.append(f"{role}: incomplete slot fields")
                continue
            matches = [p for p in root.rglob("*")
                       if p.is_file() and
                       sha256_file(p) == s["report_sha256"]]
            if not matches:
                pkt_problems.append(
                    f"{role}: report_sha256 matches no file on disk")
        if len(filled) == 3 and filled["adjudicator"].get("utc") and \
                all(filled[r].get("utc") for r in
                    ("reviewer_1", "reviewer_2")):
            if not (filled["adjudicator"]["utc"] >=
                    filled["reviewer_1"]["utc"] and
                    filled["adjudicator"]["utc"] >=
                    filled["reviewer_2"]["utc"]):
                pkt_problems.append(
                    "adjudicator recorded before a reviewer")
        report["checks"]["review_packet"] = {
            "filled_slots": sorted(filled),
            "awaiting_human_review": len(filled) < 3,
            "problems": pkt_problems}
        if pkt_problems:
            failures.append("review_packet")

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

    # 5. FMX report is REBUILT from verified bytes and compared —
    # persisted reports are never trusted at face value
    from nepal.real_fmx import run_real_fmx
    cutoff_path = root / "era5-multibasin/features/" \
        "cutoff_record_v0.json"
    prov_path = root / "era5-multibasin/features/" \
        "preprocessing_provenance_v0.json"
    fmx_rebuild = {"status": "skipped_missing_inputs"}
    if fmx_persisted is not None and cutoff_path.exists() and \
            prov_path.exists() and "feature" in manifests:
        rebuilt_report = run_real_fmx(
            manifests["feature"],
            frame_relpath=FRAME_RELPATH,
            cutoff_record=json.loads(cutoff_path.read_text()),
            preprocessing_record=json.loads(prov_path.read_text()))
        match = sha256_canonical(rebuilt_report) == \
            sha256_canonical(fmx_persisted)
        fmx_rebuild = {
            "rebuilt_status": rebuilt_report.get("status"),
            "digest_match": match}
        if not match:
            failures.append("fmx_rebuild")
    else:
        failures.append("fmx_rebuild_inputs")
    report["checks"]["fmx_rebuild"] = fmx_rebuild

    # 6. regime receipt is deterministic-carried (digest sidecar) AND
    # its report_digest recomputes from its own content
    rcpt = root / "retrieval/p5_glof_descriptive_receipt_v1.json"
    if not rcpt.exists():  # fall back to the historical v0 receipt
        rcpt = root / "retrieval/p5_glof_descriptive_receipt_v0.json"
    if rcpt.exists():
        rs = rcpt.with_name(rcpt.name + ".sha256")
        ok = rs.exists() and \
            rs.read_text().split()[0] == sha256_file(rcpt)
        report["checks"]["receipt_sidecar"] = ok
        if not ok:
            failures.append("receipt_sidecar")
        body = json.loads(rcpt.read_text())
        recomputed = sha256_canonical(
            {k: v for k, v in body.items()
             if k not in ("report_digest", "problems")})
        report["checks"]["receipt_report_digest"] = {
            "match": recomputed == body.get("report_digest")}
        if recomputed != body.get("report_digest"):
            failures.append("receipt_report_digest")
        report["checks"]["receipt_authority"] = {
            "promotion_eligible": body.get("promotion_eligible"),
            "production_authorized":
                body.get("production_authorized"),
            "warning_path_authorized":
                body.get("warning_path_authorized")}
        if any(report["checks"]["receipt_authority"].values()):
            failures.append("authority_flags")

        # R-04/R-05 — a bound digest is not an artifact: the regime
        # replay state is `artifact_integrity_replayed` ONLY when a persisted
        # artifact exists whose envelope digest recomputes, whose
        # freeze digest verifies, whose producer floor is clean, and
        # whose digest equals the receipt's bound digest.
        art_p = root / "retrieval/p5_glof_regime_artifact_v1.json"
        artifact_ok = False
        if not art_p.exists():
            report["checks"]["daily_artifact"] = {
                "exists": False,
                "note": "receipt-bound only — artifact bytes never "
                        "persisted (amendment v5 records the lineage)"}
            failures.append("daily_artifact_missing")
        else:
            art = json.loads(art_p.read_text())
            pre_freeze = {k: v for k, v in art.items()
                          if k not in ("frozen", "freeze_digest")}
            env_ok = sha256_canonical(
                {k: v for k, v in pre_freeze.items()
                 if k != "regime_artifact_digest"}
                ) == art.get("regime_artifact_digest")
            fz_ok = art.get("frozen") is True and \
                sha256_canonical(pre_freeze) == \
                art.get("freeze_digest")
            from nepal.research_v0.producer_validation import (
                validate_producer_payload)
            probe = dict(art)
            probe.setdefault("frozen", True)
            floor = validate_producer_payload(probe)
            binds = body.get("regime_artifact_digest") == \
                art.get("regime_artifact_digest")
            report["checks"]["daily_artifact"] = {
                "exists": True, "envelope_digest": env_ok,
                "freeze_digest": fz_ok,
                "producer_floor_problems": floor,
                "receipt_binds_artifact": binds,
                "status": art.get("status")}
            for ok_, name in ((env_ok, "artifact_envelope"),
                              (fz_ok, "artifact_freeze"),
                              (not floor, "artifact_floor"),
                              (binds, "artifact_receipt_binding")):
                if not ok_:
                    failures.append("daily_" + name)
            artifact_ok = env_ok and fz_ok and not floor and binds
        # A persisted artifact whose envelope, freeze, producer floor, and
        # receipt binding all validate is an integrity replay only.  It is
        # intentionally not called model-reexecuted: this function never
        # invokes run_regimes.
        report["regime_replay_state"] = (
            "regime_execution_blocked"
            if body.get("status") == "RUN_ERROR"
            else ("artifact_integrity_replayed" if artifact_ok
                  else "chain_recomputed_receipt_only"))

    report["status"] = "REPLAY_FAIL" if failures else "REPLAY_OK"
    report["failures"] = failures
    report["completed_utc"] = datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence-root", default=str(DEFAULT_ROOT))
    ap.add_argument(
        "--report-out",
        default=None,
        help="explicit new path for a report; omitted means stdout only")
    args = ap.parse_args()
    report = replay(Path(args.evidence_root))
    out = None
    if args.report_out:
        from p5_safe_io import write_once_bytes, write_once_text
        out = Path(args.report_out).resolve()
        b = json.dumps(report, indent=2, sort_keys=True).encode() + b"\n"
        try:
            digest = write_once_bytes(out, b)
            write_once_text(Path(str(out) + ".sha256"),
                            f"{digest}  {out.name}\n")
        except FileExistsError as exc:
            print(f"REPORT_WRITE_REFUSED: {exc}")
            return 2
    print(json.dumps(report, indent=1)[:2000])
    print(f"\nstatus: {report['status']}" +
          (f"  -> {out}" if out else "  -> stdout only"))
    return 0 if report["status"] == "REPLAY_OK" else 1


if __name__ == "__main__":
    sys.exit(main())
