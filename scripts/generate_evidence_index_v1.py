#!/usr/bin/env python3
"""Generate p5_evidence_index_v1.json — successor to the audited v0.

V1 fixes the audited defects: logical root_ids + relative paths
(absolute paths live only under roots.<id>.path), full provenance
(generator/commit/utc/manifest digest), per-file sidecar digests,
state vocabulary, and a supersedes pointer to v0 (preserved, never
rewritten).  Seismic preflight is a LOGICAL root inside the daily
root — three physical roots, not a fabricated fourth.

Usage:
    generate_evidence_index_v1.py [--report-out PATH]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "docs/science/ARTIFACT_MANIFEST_V0.json"
ER = Path("/Users/sanjayb/nepal-event-anomaly-evidence")
DAILY = ER / "p5-glof-2026-09-19"
SV0 = ER / "p5-seasonal-jja-2026-09-20"
SV1 = ER / "p5-seasonal-v1-2026-09-20"
SEISMIC = DAILY / "seismic-4w"

INDEX_OUT = DAILY / "retrieval/p5_evidence_index_v1.json"
V0_INDEX = DAILY / "retrieval/p5_evidence_index_v0.json"

ROOTS = {
    "daily_p5a2": {"path": str(DAILY),
                   "role": "daily P5-A2 lane — source of truth for "
                           "the daily estimand; Arm A reference for "
                           "both seasonal lanes"},
    "seasonal_v0_immutable": {"path": str(SV0),
                              "role": "first seasonal execution — "
                                      "superseded semantics (gate "
                                      "N/A collapse); IMMUTABLE, "
                                      "never patched"},
    "seasonal_v1_current": {"path": str(SV1),
                            "role": "current seasonal evidence — "
                                    "gate_observations semantics, "
                                    "artifact-bound Arm A, explicit "
                                    "authority fields"},
    "seismic_preflight": {"path": str(SEISMIC),
                          "role": "LOGICAL surface under the daily "
                                  "physical root — preflight only; "
                                  "no waveform/event bytes exist"},
}

# (root_id, relpath, state, activity, entity_role)
FILES = [
    # --- daily lane ---
    ("daily_p5a2", "retrieval/p5_glof_descriptive_receipt_v0.json",
     "superseded",
     "P5-A2 in-session run — receipt-bound only (artifact bytes "
     "never persisted; superseded by v1 receipt under amendment v5)",
     "lane_receipt_historical"),
    ("daily_p5a2", "retrieval/p5_glof_descriptive_receipt_v1.json",
     "current",
     "P5-A2 lineage repair — receipt bound to a persisted artifact "
     "under the declared amendment-v5 config",
     "lane_receipt_current"),
    ("daily_p5a2", "retrieval/p5_glof_regime_artifact_v1.json",
     "current",
     "P5-A2 artifact serialized by scripts/run_daily_p5.py under "
     "the declared amendment-v5 config — byte-bound",
     "regime_artifact"),
    ("daily_p5a2", "retrieval/p5_amendment_v5_artifact_lineage.json",
     "current",
     "lineage amendment — records the v0 receipt-bound gap and the "
     "declared v1 reconstruction config",
     "amendment"),
    ("daily_p5a2",
     "retrieval/p5_amendment_v2_temporal_holdout.json",
     "current", "temporal-holdout amendment (P5-A2)", "amendment"),
    ("daily_p5a2",
     "retrieval/p5_amendment_v3_seasonal_estimand.json",
     "current", "seasonal estimand amendment (Option 2)",
     "amendment"),
    ("daily_p5a2",
     "retrieval/p5_amendment_v4_seismic_observability_preflight.json",
     "current", "seismic observability preflight authorization",
     "amendment"),
    ("daily_p5a2", "retrieval/p5_extension_protocol_v0.json",
     "superseded",
     "extension protocol — historical snapshot; stale manifest/"
     "disk values superseded by P5_EXTENSION_STATUS_V0.md",
     "protocol_historical"),
    ("daily_p5a2", "retrieval/p5_extension_registry_v0.json",
     "current", "extension registry — updated to post-audit states",
     "registry"),
    ("daily_p5a2",
     "retrieval/p5_d_preflight_reconciliation_v0.json", "current",
     "Option 3 preflight reconciliation — no qualified event in "
     "the authorized window", "preflight_record"),
    ("daily_p5a2", "retrieval/p5_d_owner_disposition_v1.json",
     "current",
     "owner-gated Option 3 disposition — BLOCKED pending owner "
     "amendments", "disposition"),
    ("daily_p5a2",
     "retrieval/p5_post_implementation_audit_v0.json", "superseded",
     "post-implementation audit of the PRIOR cycle — preserved "
     "unchanged; superseded by the v1 verification record",
     "audit_historical"),
    ("daily_p5a2",
     "retrieval/p5_o2_semantic_reconciliation_v1.json", "current",
     "Option 2 semantic reconciliation — gate-observation/"
     "year_block/adapter dispositions", "reconciliation"),
    ("daily_p5a2", "retrieval/p5_replay_report_v0.json", "current",
     "daily replay report — regenerated post-repair; validates the "
     "persisted artifact (artifact_replayed)", "replay_report"),
    ("daily_p5a2", "retrieval/p5_evidence_index_v0.json",
     "superseded",
     "v0 index — absolute paths + stale replay digest; preserved "
     "immutable, superseded by this index", "index_historical"),
    ("daily_p5a2",
     "era5-multibasin/features/regime_frame_hma_jja_2001_2025.csv",
     "current", "ERA5-Land daily JJA frame (bound bytes)",
     "feature_frame"),
    ("daily_p5a2",
     "era5-multibasin/features/regime_frame_provenance.json",
     "current", "frame provenance", "provenance"),
    ("daily_p5a2", "glof-events/p3_runner_package_v0.json",
     "current", "event package — opportunity/control/holdout "
                "bindings", "event_package"),
    # --- seismic logical surface (under daily root) ---
    ("seismic_preflight", "obspy_qualification_probe.json",
     "current",
     "ObsPy qualification probe — isolated env only, NOT admitted "
     "to the governed project environment", "preflight_probe"),
    # --- seasonal v0 (immutable historical) ---
    ("seasonal_v0_immutable",
     "features/seasonal_frame_jja_2001_2025.csv", "immutable",
     "v0 seasonal frame — superseded semantics, never patched",
     "feature_frame"),
    ("seasonal_v0_immutable",
     "features/seasonal_frame_provenance_v0.json", "immutable",
     "v0 frame provenance", "provenance"),
    ("seasonal_v0_immutable",
     "features/negative_control_frame.csv", "immutable",
     "v0 negative-control frame (refused before fitting)",
     "negative_control"),
    ("seasonal_v0_immutable",
     "run/seasonal_regime_artifact_v0.json", "immutable",
     "v0 seasonal artifact — pre-gate-observation semantics",
     "regime_artifact"),
    ("seasonal_v0_immutable",
     "run/seasonal_lane_receipt_v0.json", "immutable",
     "v0 seasonal receipt", "lane_receipt"),
    ("seasonal_v0_immutable",
     "run/seasonal_replay_report_v0.json", "immutable",
     "v0 seasonal replay report", "replay_report"),
    # --- seasonal v1 (current) ---
    ("seasonal_v1_current",
     "features/seasonal_frame_jja_2001_2025.csv", "current",
     "v1 seasonal frame — strict input-domain adapter",
     "feature_frame"),
    ("seasonal_v1_current",
     "features/seasonal_frame_provenance_v0.json", "current",
     "v1 frame provenance", "provenance"),
    ("seasonal_v1_current",
     "features/negative_control_frame.csv", "current",
     "v1 negative-control frame — refused before fitting",
     "negative_control"),
    ("seasonal_v1_current",
     "run/seasonal_regime_artifact_v0.json", "current",
     "v1 seasonal artifact — gate_observations semantics; "
     "UNSUPERVISED_STRUCTURE_NOT_STABLE (second honest negative)",
     "regime_artifact"),
    ("seasonal_v1_current",
     "run/seasonal_lane_receipt_v0.json", "current",
     "v1 seasonal receipt — terminal_reason, failed_gates, "
     "artifact-bound Arm A, all-false authority", "lane_receipt"),
    ("seasonal_v1_current",
     "run/seasonal_replay_report_v0.json", "current",
     "v1 seasonal replay report — REPLAY_OK incl. live Arm A "
     "artifact chain", "replay_report"),
]


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report-out", default=None)
    args = ap.parse_args()

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO,
        capture_output=True, text=True).stdout.strip()
    manifest_sha = _sha(MANIFEST)

    files = []
    missing = []
    for root_id, relp, state, activity, role in FILES:
        root_path = Path(ROOTS[root_id]["path"])
        p = root_path / relp
        if not p.exists():
            missing.append(f"{root_id}:{relp}")
            continue
        sc = Path(str(p) + ".sha256")
        files.append({
            "root_id": root_id, "relpath": relp,
            "sha256": _sha(p), "size_bytes": p.stat().st_size,
            "sidecar_sha256": _sha(sc) if sc.exists() else None,
            "state": state, "activity": activity,
            "entity_role": role})

    idx = {
        "schema": "P5_EVIDENCE_INDEX_V1",
        "title": "Cross-root evidence index v1 — logical root_ids, "
                 "relative paths, provenance, sidecar digests",
        "generated_utc": datetime.now(
            timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generator": {"agent": "DEVIN-SEASONAL-V1 coordinator",
                      "repo_commit": head,
                      "tool": "scripts/generate_evidence_index_v1.py"},
        "manifest_sha256": manifest_sha,
        "supersedes": {"relpath": str(V0_INDEX),
                       "sha256": _sha(V0_INDEX)},
        "roots": ROOTS,
        "topology": ("three physical roots (daily_p5a2, "
                     "seasonal_v0_immutable, seasonal_v1_current); "
                     "seismic_preflight is a LOGICAL surface rooted "
                     "inside daily_p5a2 — not a fourth physical root"),
        "files": files,
        "claim_scope":
            "research_only_no_operational_authorization"}
    INDEX_OUT.write_text(json.dumps(idx, indent=2,
                                    sort_keys=True) + "\n")
    Path(str(INDEX_OUT) + ".sha256").write_text(
        f"{_sha(INDEX_OUT)}  {INDEX_OUT.name}\n")
    print(json.dumps({
        "files_indexed": len(files), "missing": missing,
        "index_sha256": _sha(INDEX_OUT)}, indent=1))
    if args.report_out:
        Path(args.report_out).write_text(
            json.dumps({"indexed": len(files)}, indent=1))
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
