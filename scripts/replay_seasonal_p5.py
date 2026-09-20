#!/usr/bin/env python3
"""REPLAY for the P5 seasonal lane (amendment v3).

Independent verification of the seasonal evidence root — mirrors
replay_p5.py's contract: persisted bytes are never trusted, every
check recomputes from live bytes.

Checks:
  1. every artifact in the lane root rehashes against its sidecar
  2. the seasonal frame REBUILDS from the byte-verified daily frame
     into identical bytes (deterministic derivation)
  3. the frozen regime artifact's regime_artifact_digest recomputes
     and the shared producer floor accepts the payload
  4. every digest bound inside the lane receipt matches live bytes
  5. authority fields stay research-only; statuses stay inside the
     declared terminal vocabulary

Usage:
    PYTHONPATH=. .venv/bin/python -B scripts/replay_seasonal_p5.py
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

DAILY_ROOT = Path("/Users/sanjayb/nepal-event-anomaly-evidence/"
                  "p5-glof-2026-09-19")
DAILY_CSV = (DAILY_ROOT / "era5-multibasin/features/"
             "regime_frame_hma_jja_2001_2025.csv")
DAILY_RECEIPT = (DAILY_ROOT / "retrieval/"
                 "p5_glof_descriptive_receipt_v0.json")
LANE_ROOT = Path("/Users/sanjayb/nepal-event-anomaly-evidence/"
                 "p5-seasonal-jja-2026-09-20")

FRAME = LANE_ROOT / "features/seasonal_frame_jja_2001_2025.csv"
PROV = LANE_ROOT / "features/seasonal_frame_provenance_v0.json"
NC = LANE_ROOT / "features/negative_control_frame.csv"
ART = LANE_ROOT / "run/seasonal_regime_artifact_v0.json"
RCPT = LANE_ROOT / "run/seasonal_lane_receipt_v0.json"

TERMINAL = {"DESCRIPTIVE_REGIME_ONLY", "CANDIDATE_ONLY",
            "UNSUPERVISED_STRUCTURE_NOT_STABLE",
            "UNDERPOWERED_DESCRIPTIVE_ONLY", "RUN_ERROR"}


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _sidecar_ok(p: Path) -> bool:
    s = Path(str(p) + ".sha256")
    return s.exists() and s.read_text().split()[0] == _sha(p)


def replay() -> dict:
    failures: list[str] = []
    report = {"lane_root": str(LANE_ROOT), "checks": {}}

    # 1. sidecars
    sidecars = {p.name: _sidecar_ok(p)
                for p in (FRAME, PROV, NC, ART, RCPT)}
    report["checks"]["sidecars"] = sidecars
    if not all(sidecars.values()):
        failures.append("sidecars")

    # 2. frame rebuild — deterministic derivation from bound daily
    #    bytes, rebuilt into a temp dir so evidence stays untouched
    from nepal.science_v0.seasonal_frame import build_seasonal_frame
    if DAILY_CSV.exists() and FRAME.exists():
        with tempfile.TemporaryDirectory() as td:
            rebuild = build_seasonal_frame(DAILY_CSV, Path(td))
            rebuilt_bytes = Path(rebuild["csv"]).read_bytes()
        match = hashlib.sha256(rebuilt_bytes).hexdigest() == _sha(FRAME)
        report["checks"]["frame_rebuild"] = {
            "deterministic_rebuild": match,
            "n_rows_rebuilt": int(len(rebuild["frame"]))}
        if not match:
            failures.append("frame_rebuild")
    else:
        report["checks"]["frame_rebuild"] = "skipped_missing_inputs"
        failures.append("frame_rebuild_inputs")

    # 3. artifact: envelope digest + shared producer floor
    from nepal.research_v0._hashing import sha256_canonical
    from nepal.research_v0.producer_validation import (
        validate_producer_payload)
    if ART.exists():
        art = json.loads(ART.read_text())
        # the persisted file is the FROZEN artifact — `frozen` and
        # `freeze_digest` are added by freeze after the envelope
        # digest was computed, so both are excluded from the envelope
        # recompute; freeze_digest itself re-verifies the pre-freeze
        # surface (persisted minus those two keys).
        pre_freeze = {k: v for k, v in art.items()
                      if k not in ("frozen", "freeze_digest")}
        recomputed = sha256_canonical(
            {k: v for k, v in pre_freeze.items()
             if k != "regime_artifact_digest"})
        env_ok = recomputed == art.get("regime_artifact_digest")
        fz_ok = art.get("frozen") is True and \
            sha256_canonical(pre_freeze) == art.get("freeze_digest")
        probe = dict(art)
        probe.setdefault("frozen", True)
        floor = validate_producer_payload(probe)
        report["checks"]["artifact"] = {
            "envelope_digest": env_ok,
            "freeze_digest": fz_ok,
            "producer_floor_problems": floor,
            "status": art.get("status")}
        if not env_ok:
            failures.append("artifact_envelope_digest")
        if not fz_ok:
            failures.append("artifact_freeze_digest")
        if floor:
            failures.append("artifact_producer_floor")
        if art.get("status") not in TERMINAL:
            failures.append("artifact_status_vocabulary")
        # claim surface: no forecast/operational payload may attach
        if art.get("forecast_vintage_digests") or \
                art.get("forecast_feature_set"):
            failures.append("artifact_forecast_payload")
        if art.get("mode") != "RETROSPECTIVE_REGIME":
            failures.append("artifact_mode")
        if art.get("data_class") != "REANALYSIS":
            failures.append("artifact_data_class")
    else:
        report["checks"]["artifact"] = "skipped_missing"
        failures.append("artifact_missing")

    # 4. receipt-bound digests match live bytes
    if RCPT.exists():
        rcpt = json.loads(RCPT.read_text())
        binds = {
            "frame": (rcpt.get("frame") or {}).get("sha256"),
            "artifact": (rcpt["arms"].get("B_surface_core") or {})
                .get("artifact_sha256"),
            "nc_frame": (rcpt["arms"].get("NC_negative_control") or {})
                .get("frame_sha256"),
            "daily_receipt": (rcpt["arms"].get("A_reference") or {})
                .get("receipt_sha256"),
        }
        live = {
            "frame": _sha(FRAME) if FRAME.exists() else None,
            "artifact": _sha(ART) if ART.exists() else None,
            "nc_frame": _sha(NC) if NC.exists() else None,
            "daily_receipt": _sha(DAILY_RECEIPT)
                if DAILY_RECEIPT.exists() else None,
        }
        mismatched = [k for k, v in binds.items()
                      if v is not None and v != live[k]]
        unbound = [k for k, v in binds.items() if v is None]
        report["checks"]["receipt_bindings"] = {
            "mismatched": mismatched, "unbound": unbound}
        if mismatched:
            failures.append("receipt_bindings")
        nc_arm = rcpt["arms"].get("NC_negative_control") or {}
        report["checks"]["negative_control"] = {
            "rejected_before_fit":
                nc_arm.get("rejected_before_fit") is True,
            "engine_status": nc_arm.get("engine_status")}
        if nc_arm.get("rejected_before_fit") is not True:
            failures.append("negative_control_not_rejected")
        if rcpt.get("claim_scope") != \
                "research_only_no_operational_authorization":
            failures.append("claim_scope")
        if rcpt.get("status") not in TERMINAL:
            failures.append("receipt_status_vocabulary")

    report["status"] = "REPLAY_FAIL" if failures else "REPLAY_OK"
    report["failures"] = failures
    return report


def main() -> int:
    report = replay()
    out = LANE_ROOT / "run/seasonal_replay_report_v0.json"
    if out.parent.is_dir():
        b = json.dumps(report, indent=2, sort_keys=True).encode()
        out.write_bytes(b)
        Path(str(out) + ".sha256").write_text(
            hashlib.sha256(b).hexdigest() + f"  {out.name}\n")
    print(json.dumps(report, indent=1))
    print(f"\nstatus: {report['status']}")
    return 0 if report["status"] == "REPLAY_OK" else 1


if __name__ == "__main__":
    sys.exit(main())
