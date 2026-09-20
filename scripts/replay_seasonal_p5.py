#!/usr/bin/env python3
"""REPLAY for the P5 seasonal lane (amendment v3, v1 semantics).

Independent verification of the seasonal evidence root — mirrors
replay_p5.py's contract: persisted bytes are never trusted, every
check recomputes from live bytes.

Checks:
  1. every artifact in the lane root rehashes against its sidecar
  2. the seasonal frame REBUILDS from the byte-verified daily frame
     into identical bytes (deterministic derivation)
  3. the frozen regime artifact's envelope + freeze digests recompute
     and the shared producer floor accepts the payload
  4. every digest bound inside the lane receipt matches live bytes,
     the Arm A reference verification is present and passed
  5. gate semantics: observed_status is distinguishable from the
     binding bool — diagnostic LORO records SKIPPED/non-binding,
     NOT_APPLICABLE axes are not indistinguishable PASSes
  6. authority fields are all explicitly false; statuses stay inside
     the declared terminal vocabulary; no forecast payload attaches

Usage:
    PYTHONPATH=. .venv/bin/python -B scripts/replay_seasonal_p5.py \
        [--daily-root PATH] [--lane-root PATH]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

DEFAULT_DAILY_ROOT = Path(
    "/Users/sanjayb/nepal-event-anomaly-evidence/p5-glof-2026-09-19")
DEFAULT_LANE_ROOT = Path(
    "/Users/sanjayb/nepal-event-anomaly-evidence/"
    "p5-seasonal-v1-2026-09-20")

TERMINAL = {"DESCRIPTIVE_REGIME_ONLY", "CANDIDATE_ONLY",
            "UNSUPERVISED_STRUCTURE_NOT_STABLE",
            "UNDERPOWERED_DESCRIPTIVE_ONLY", "RUN_ERROR"}
OBSERVED_VOCAB = {"PASS", "FAIL", "SKIPPED", "NOT_APPLICABLE",
                  "NONCONVERGED"}


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _sidecar_ok(p: Path) -> bool:
    s = Path(str(p) + ".sha256")
    return s.exists() and s.read_text().split()[0] == _sha(p)


def replay(lane_root: Path, daily_root: Path) -> dict:
    failures: list[str] = []
    report = {"lane_root": str(lane_root),
              "daily_root": str(daily_root), "checks": {}}
    daily_csv = (daily_root / "era5-multibasin/features/"
                 "regime_frame_hma_jja_2001_2025.csv")
    daily_receipt = (daily_root / "retrieval/"
                     "p5_glof_descriptive_receipt_v0.json")
    frame_p = lane_root / "features/seasonal_frame_jja_2001_2025.csv"
    prov_p = lane_root / "features/seasonal_frame_provenance_v0.json"
    nc_p = lane_root / "features/negative_control_frame.csv"
    art_p = lane_root / "run/seasonal_regime_artifact_v0.json"
    rcpt_p = lane_root / "run/seasonal_lane_receipt_v0.json"

    # 1. sidecars
    sidecars = {p.name: _sidecar_ok(p)
                for p in (frame_p, prov_p, nc_p, art_p, rcpt_p)}
    report["checks"]["sidecars"] = sidecars
    if not all(sidecars.values()):
        failures.append("sidecars")

    # 2. frame rebuild — deterministic derivation from bound daily
    #    bytes, rebuilt into a temp dir so evidence stays untouched
    from nepal.science_v0.seasonal_frame import build_seasonal_frame
    if daily_csv.exists() and frame_p.exists():
        with tempfile.TemporaryDirectory() as td:
            rebuild = build_seasonal_frame(daily_csv, Path(td))
            rebuilt_bytes = Path(rebuild["csv"]).read_bytes()
        match = hashlib.sha256(rebuilt_bytes).hexdigest() == \
            _sha(frame_p)
        report["checks"]["frame_rebuild"] = {
            "deterministic_rebuild": match,
            "n_rows_rebuilt": int(len(rebuild["frame"]))}
        if not match:
            failures.append("frame_rebuild")
    else:
        report["checks"]["frame_rebuild"] = "skipped_missing_inputs"
        failures.append("frame_rebuild_inputs")

    # 3. artifact: envelope digest + freeze digest + shared floor +
    #    gate-observation semantics
    from nepal.research_v0._hashing import sha256_canonical
    from nepal.research_v0.producer_validation import (
        validate_producer_payload)
    if art_p.exists():
        art = json.loads(art_p.read_text())
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
        if art.get("forecast_vintage_digests") or \
                art.get("forecast_feature_set"):
            failures.append("artifact_forecast_payload")
        if art.get("mode") != "RETROSPECTIVE_REGIME":
            failures.append("artifact_mode")
        if art.get("data_class") != "REANALYSIS":
            failures.append("artifact_data_class")

        # 5. gate-observation semantics
        stab = art.get("stability") or {}
        gates = stab.get("required_gates") or {}
        obs = stab.get("gate_observations") or {}
        obs_problems = []
        if set(obs) != set(gates) or not obs:
            obs_problems.append(
                "gate_observations must cover exactly the "
                "required_gates universe")
        for g, o in obs.items():
            if not isinstance(o, dict) or \
                    o.get("observed_status") not in OBSERVED_VOCAB or \
                    not isinstance(o.get("binding"), bool):
                obs_problems.append(f"gate {g}: malformed "
                                    "observation record")
        loro = obs.get("loro") or {}
        if (art.get("config") or {}).get("loro_policy") == \
                "diagnostic":
            if loro.get("binding") is not False:
                obs_problems.append(
                    "diagnostic LORO must carry binding=false — it "
                    "can never masquerade as gate evidence")
            if loro.get("observed_status") == "PASS" and \
                    gates.get("loro") is True:
                obs_problems.append(
                    "diagnostic LORO observed PASS while folds are "
                    "underpowered — recheck fold semantics")
            if gates.get("loro") is True and \
                    loro.get("observed_status") in \
                    ("FAIL", "NONCONVERGED"):
                obs_problems.append(
                    "required_gates.loro=True contradicts observed "
                    f"{loro.get('observed_status')} — a diagnostic "
                    "axis cannot hide a fold failure")
        # a NOT_APPLICABLE observed axis must never be confused with
        # an executed PASS — the bool and the observation must agree
        for g, o in obs.items():
            if o.get("binding") and \
                    o.get("observed_status") == "FAIL" and \
                    gates.get(g) is True:
                obs_problems.append(
                    f"gate {g}: bool True contradicts observed FAIL")
        report["checks"]["gate_observations"] = {
            "problems": obs_problems,
            "loro_observed": loro.get("observed_status"),
            "loro_binding": loro.get("binding")}
        if obs_problems:
            failures.append("gate_observations")
    else:
        report["checks"]["artifact"] = "skipped_missing"
        failures.append("artifact_missing")

    # 4. receipt bindings + Arm A verification + authority fields
    if rcpt_p.exists():
        rcpt = json.loads(rcpt_p.read_text())
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
            "frame": _sha(frame_p) if frame_p.exists() else None,
            "artifact": _sha(art_p) if art_p.exists() else None,
            "nc_frame": _sha(nc_p) if nc_p.exists() else None,
            "daily_receipt": _sha(daily_receipt)
                if daily_receipt.exists() else None,
        }
        mismatched = [k for k, v in binds.items()
                      if v is not None and v != live[k]]
        unbound = [k for k, v in binds.items() if v is None]
        report["checks"]["receipt_bindings"] = {
            "mismatched": mismatched, "unbound": unbound}
        if mismatched:
            failures.append("receipt_bindings")

        # Arm A verification must be present and passed
        ref = (rcpt["arms"].get("A_reference") or {}) \
            .get("verification") or {}
        ref_ok = ref.get("verified") is True
        report["checks"]["arm_a_verification"] = {
            "present": bool(ref), "verified": ref_ok,
            "problems": ref.get("problems")}
        if not ref_ok:
            failures.append("arm_a_verification")

        # receipt-level semantics: failed_gates/terminal_reason
        arm_b = rcpt["arms"].get("B_surface_core") or {}
        fg = arm_b.get("failed_gates")
        art_gates = {}
        if art_p.exists():
            art_gates = (json.loads(art_p.read_text())
                         .get("stability") or {}) \
                        .get("required_gates") or {}
        expected_fg = sorted(g for g, v in art_gates.items()
                             if v is not True)
        report["checks"]["receipt_semantics"] = {
            "failed_gates": fg,
            "expected": expected_fg,
            "terminal_reason_present":
                arm_b.get("terminal_reason") is not None
                or not expected_fg,
            "status": arm_b.get("status")}
        if fg is not None and sorted(fg) != expected_fg:
            failures.append("receipt_failed_gates_mismatch")
        if expected_fg and not arm_b.get("terminal_reason"):
            failures.append("receipt_missing_terminal_reason")

        # authority: every flag must be present and exactly false
        auth = rcpt.get("authority") or {}
        auth_flags = {k: auth.get(k) for k in
                      ("promotion_eligible", "production_authorized",
                       "warning_path_authorized", "operational_claim")}
        report["checks"]["authority"] = auth_flags
        if any(v is not False for v in auth_flags.values()):
            failures.append("authority_not_all_false")

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
        # bound roots must match the invoked roots — a receipt bound
        # to a different evidence root is stale evidence
        roots = rcpt.get("roots") or {}
        if roots and (roots.get("lane_root") != str(lane_root) or
                      roots.get("daily_root") != str(daily_root)):
            report["checks"]["roots_bound"] = roots
            failures.append("stale_bound_roots")

    report["status"] = "REPLAY_FAIL" if failures else "REPLAY_OK"
    report["failures"] = failures
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--daily-root", default=str(DEFAULT_DAILY_ROOT))
    ap.add_argument("--lane-root", default=str(DEFAULT_LANE_ROOT))
    args = ap.parse_args()
    lane_root = Path(args.lane_root).resolve()
    daily_root = Path(args.daily_root).resolve()
    report = replay(lane_root, daily_root)
    out = lane_root / "run/seasonal_replay_report_v0.json"
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
