#!/usr/bin/env python3
"""P5 seasonal lane (amendment v3) — basin-year JJA regime run.

Executes the locked seasonal estimand:
  Arm A  — the daily P5-A2 result bound by reference (never rerun).
           The full reference chain is verified before binding:
           receipt bytes, embedded artifact digest, status, and
           authority flags must all agree, or Arm A is rejected.
  Arm B  — the six-feature seasonal contract on the byte-verified
           basin-year frame (tied covariance primary).
  Arm NC — a declared negative-control input (feature-shuffled
           basin-years, input_role="negative_control") that MUST be
           rejected before any model fitting; if the engine ever
           produced an artifact from it, the harness is broken.

Writes into the lane evidence root (LANE_ROOT):
  features/seasonal_frame_jja_2001_2025.csv (+sha256)
  features/seasonal_frame_provenance_v0.json (+sha256)
  features/negative_control_frame.csv (+sha256)
  run/seasonal_regime_artifact_v0.json (+sha256)
  run/seasonal_lane_receipt_v0.json (+sha256)
Verified independently by scripts/replay_seasonal_p5.py.

Portable: --daily-root / --lane-root override the canonical evidence
roots; the resolved roots are bound into the receipt.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import platform
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from nepal.science_v0.regimes import (RegimeRunConfig, run_regimes,
                                    freeze_regime_artifact)
from nepal.science_v0.seasonal_frame import (SEASONAL_FEATURES,
                                           SEASONAL_UNITS,
                                           build_seasonal_frame)
from p5_safe_io import (ExistingEvidenceError, write_once_bytes,
                        write_once_json, write_once_sidecar)

DEFAULT_DAILY_ROOT = Path(
    "/Users/sanjayb/nepal-event-anomaly-evidence/p5-glof-2026-09-19")
DEFAULT_LANE_ROOT = Path(
    "/Users/sanjayb/nepal-event-anomaly-evidence/"
    "p5-seasonal-v1-2026-09-20")

# Arm A — the bound daily artifact (v1 lineage, amendment v5: the
# v0 artifact was receipt-bound only — its bytes were never
# persisted; the v1 artifact is serialized and byte-bound under the
# declared config).  Verified against live bytes at run time — a
# stale or mutated daily reference rejects the arm.
DAILY_ARTIFACT_RELPATH = "retrieval/p5_glof_regime_artifact_v1.json"
DAILY_RECEIPT_RELPATH = "retrieval/p5_glof_descriptive_receipt_v1.json"
DAILY_TERMINAL_STATUSES = {"CANDIDATE_ONLY", "DESCRIPTIVE_REGIME_ONLY",
                           "UNSUPERVISED_STRUCTURE_NOT_STABLE",
                           "UNDERPOWERED_DESCRIPTIVE_ONLY"}
LANE_TERMINAL_STATUSES = DAILY_TERMINAL_STATUSES | {"RUN_ERROR"}


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _environment() -> dict:
    versions = {}
    for name in ("numpy", "pandas", "scikit-learn"):
        try:
            from importlib.metadata import version
            versions[name] = version(name)
        except Exception:
            versions[name] = "unavailable"
    return {"python": sys.version.split()[0],
            "platform": platform.platform(), "packages": versions}


def verify_daily_reference(daily_root: Path) -> dict:
    """Arm A reference-chain verification — the daily result is bound
    by reference ONLY when the live receipt AND the live artifact
    bytes agree: receipt sidecar, embedded digest, terminal status,
    all-false authority, PLUS the artifact file's envelope digest
    recomputing to the receipt's bound digest and the freeze digest
    verifying.  A missing/mutated artifact fails closed — a digest
    alone is not an artifact."""
    checks = {"receipt_exists": False, "artifact_exists": False,
              "receipt_sha256": None, "artifact_sha256": None,
              "artifact_digest": None, "status": None,
              "status_terminal": None,
              "authority_flags_all_false": None,
              "artifact_envelope_digest_match": None,
              "artifact_freeze_digest_ok": None,
              "verified": False, "problems": []}
    receipt_path = daily_root / DAILY_RECEIPT_RELPATH
    artifact_path = daily_root / DAILY_ARTIFACT_RELPATH
    checks["receipt_exists"] = receipt_path.exists()
    checks["artifact_exists"] = artifact_path.exists()
    if not checks["receipt_exists"]:
        checks["problems"].append(
            f"daily receipt {receipt_path} missing — Arm A cannot "
            "bind a reference that does not exist")
        return checks
    checks["receipt_sha256"] = _sha(receipt_path)
    try:
        rcpt = json.loads(receipt_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        checks["problems"].append(
            f"daily reference unreadable: {exc}")
        return checks
    # receipt semantics are checked BEFORE the artifact early-return —
    # a receipt with true authority flags must report that violation
    # even when its artifact is missing
    status = rcpt.get("status")
    checks["status"] = status
    checks["status_terminal"] = status in DAILY_TERMINAL_STATUSES
    if not checks["status_terminal"]:
        checks["problems"].append(
            f"daily receipt status {status!r} is not a declared "
            "terminal status")
    flags = {k: rcpt.get(k) for k in
             ("promotion_eligible", "production_authorized",
              "warning_path_authorized")}
    checks["authority_flags"] = flags
    checks["authority_flags_all_false"] = all(v is False
                                              for v in flags.values())
    if not checks["authority_flags_all_false"]:
        checks["problems"].append(
            f"daily receipt authority flags are not all false: "
            f"{flags}")
    if rcpt.get("claim_scope") != \
            "research_only_no_operational_authorization":
        checks["problems"].append(
            "daily receipt claim_scope is not research-only")
    embedded = rcpt.get("regime_artifact_digest")
    checks["artifact_digest"] = embedded
    if not checks["artifact_exists"]:
        checks["problems"].append(
            f"daily artifact {artifact_path} missing — a "
            "receipt-bound digest without artifact bytes is not a "
            "verified reference")
        return checks
    checks["artifact_sha256"] = _sha(artifact_path)
    try:
        art = json.loads(artifact_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        checks["problems"].append(
            f"daily reference unreadable: {exc}")
        return checks
    # the artifact file's envelope digest must recompute to the
    # receipt's bound digest — the receipt binds THESE bytes
    from nepal.research_v0._hashing import sha256_canonical
    pre_freeze = {k: v for k, v in art.items()
                  if k not in ("frozen", "freeze_digest")}
    recomputed = sha256_canonical(
        {k: v for k, v in pre_freeze.items()
         if k != "regime_artifact_digest"})
    checks["artifact_envelope_digest_match"] = \
        recomputed == art.get("regime_artifact_digest")
    checks["artifact_freeze_digest_ok"] = (
        art.get("frozen") is True and
        sha256_canonical(pre_freeze) == art.get("freeze_digest"))
    checks["receipt_binds_artifact"] = (
        embedded == art.get("regime_artifact_digest"))
    if not checks["artifact_envelope_digest_match"]:
        checks["problems"].append(
            "daily artifact envelope digest does not recompute — "
            "the artifact may be tampered")
    if not checks["artifact_freeze_digest_ok"]:
        checks["problems"].append(
            "daily artifact freeze digest does not verify")
    if not checks["receipt_binds_artifact"]:
        checks["problems"].append(
            f"daily receipt binds {embedded} but the artifact "
            f"carries {art.get('regime_artifact_digest')} — the "
            "reference chain is stale or mutated")
    # producer floor on the live artifact
    from nepal.research_v0.producer_validation import (
        validate_producer_payload)
    floor = validate_producer_payload(art)
    if floor:
        checks["problems"].append(
            f"daily artifact fails producer floor: {floor[:2]}")
    checks["verified"] = not checks["problems"]
    return checks


def _manifest(frame_sha: str, prov_sha: str,
              lane_root: Path) -> dict:
    return {
        "source_id": "era5-land-seasonal-jja-basin-year-v1",
        "source_digests": sorted([frame_sha, prov_sha]),
        "units": list(dict.fromkeys(SEASONAL_UNITS.values())),
        "feature_allowlist": list(SEASONAL_FEATURES),
        "lineage": "derived: seasonal aggregation of byte-verified "
                   "daily JJA frame (era5-multibasin/features/"
                   "regime_frame_hma_jja_2001_2025.csv) under "
                   "p5_amendment_v3_seasonal_estimand",
        # The source-manifest contract must resolve byte-bound source files
        # on the executing host.  Receipt/report pointers use logical IDs;
        # this legacy byte-verification field remains physical by contract.
        "evidence_root": str(lane_root),
        "source_files": [
            {"relpath": "features/seasonal_frame_jja_2001_2025.csv",
             "sha256": frame_sha},
            {"relpath": "features/seasonal_frame_provenance_v0.json",
             "sha256": prov_sha}]}


def _config(manifest: dict, *, input_role: str = "scientific",
            covariance_type: str = "tied") -> RegimeRunConfig:
    return RegimeRunConfig(
        seeds=(42, 7, 2024),
        k_candidates=(1, 2, 3, 4),
        n_bootstrap=200,
        n_null_replicates=50,
        null_alpha=0.05,
        season_col="season",
        group_col="basin_group",
        era_col="era",
        era_boundaries=("2013-01-01",),
        era_drift_max=0.5,
        missingness_policy="listwise",
        effort_waiver_reason="ERA5-Land reanalysis is "
                             "assimilation-complete — no observing-"
                             "effort axis exists at seasonal grain",
        unit_col="unit_id",
        date_col="date",
        label_blinding=True,
        fitted_on="TRAIN_ONLY",
        train_groups=("gandaki", "karnali", "koshi"),
        heldout_groups=(),
        holdout_axis="temporal",
        temporal_train_interval=("2001-06-01", "2017-08-31"),
        temporal_embargo_interval=("2018-06-01", "2019-08-31"),
        temporal_holdout_interval=("2020-06-01", "2025-08-31"),
        cadence="365D",
        bootstrap_block_len=3,
        gap_policy="calendar",
        source_manifest=manifest,
        mode="RETROSPECTIVE_REGIME",
        retrospective_data_class="REANALYSIS",
        covariance_type=covariance_type,
        frame_grain="seasonal",
        input_role=input_role,
        null_extra_strata_col="basin_group",
        loro_policy="diagnostic")


def _train_mask(df: pd.DataFrame) -> np.ndarray:
    d = pd.to_datetime(df["date"]).dt.date
    return ((d >= _dt.date(2001, 6, 1))
            & (d <= _dt.date(2017, 8, 31))).to_numpy()


def _negative_control_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Nonphysical contract: the six feature columns are shuffled
    jointly across basin-year rows (seed-fixed), destroying every
    physical seasonal/basin association while preserving marginals."""
    rng = np.random.default_rng(20260920)
    nc = df.copy()
    feats = nc[list(SEASONAL_FEATURES)].to_numpy()
    nc.loc[:, list(SEASONAL_FEATURES)] = feats[
        rng.permutation(len(feats))]
    return nc


def _failed_gates(artifact: dict) -> list[str]:
    gates = (artifact.get("stability") or {}).get("required_gates")
    if not isinstance(gates, dict):
        return []
    return sorted(g for g, v in gates.items() if v is not True)


def _terminal_reason(artifact: dict) -> str | None:
    status = artifact.get("status")
    if status == "RUN_ERROR":
        return artifact.get("reason")
    failed = _failed_gates(artifact)
    if not failed:
        return None
    return (f"{status} — failed required gates: "
            + ", ".join(failed))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--daily-root", default=str(DEFAULT_DAILY_ROOT))
    ap.add_argument("--lane-root", default=None,
                    help="deprecated output-root alias")
    ap.add_argument("--output-root", default=None,
                    help="new empty output root; required to prevent overwrite")
    args = ap.parse_args()
    daily_root = Path(args.daily_root).resolve()
    if args.output_root and args.lane_root and \
            Path(args.output_root).resolve() != Path(args.lane_root).resolve():
        print("REFUSED — --lane-root and --output-root disagree")
        return 2
    raw_output = args.output_root or args.lane_root
    if not raw_output:
        print("REFUSED — --output-root is required; canonical evidence "
              "paths are write-once")
        return 2
    lane_root = Path(raw_output).resolve()
    started_utc = _utc_now()
    daily_csv = (daily_root / "era5-multibasin/features/"
                 "regime_frame_hma_jja_2001_2025.csv")
    features_dir = lane_root / "features"
    run_dir = lane_root / "run"
    features_dir.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    output_paths = (
        features_dir / "seasonal_frame_jja_2001_2025.csv",
        features_dir / "seasonal_frame_provenance_v0.json",
        features_dir / "negative_control_frame.csv",
        run_dir / "seasonal_regime_artifact_v0.json",
        run_dir / "seasonal_lane_receipt_v0.json")
    existing = [str(p) for p in output_paths if p.exists()]
    if existing:
        print("REFUSED — output already contains governed evidence: "
              + ", ".join(existing))
        return 2

    receipt = {"record_type": "SEASONAL_LANE_RECEIPT_V1",
               "schema": "P5_SEASONAL_LANE_V1",
               "claim_scope": "research_only_no_operational_"
                              "authorization",
               "authority": {
                   "promotion_eligible": False,
                   "production_authorized": False,
                   "warning_path_authorized": False,
                   "operational_claim": False},
               "roots": {"daily_root_id": "daily_p5a2",
                         "lane_root_id": "seasonal_v1_current"},
               "estimand": "basin-year JJA hydroclimate seasonal "
                           "types (75 rows = 3 basins x 25 seasons)",
               "arms": {}, "status": "RUN_ERROR", "problems": []}
    env = _environment()
    receipt["execution"] = {
        "activity_id": f"p5-seasonal-v1-{started_utc}-{os.getpid()}",
        "started_utc": started_utc,
        "completed_utc": None,
        "environment": env,
        "environment_digest": hashlib.sha256(
            json.dumps(env, sort_keys=True,
                       separators=(",", ":")).encode("utf-8")).hexdigest(),
        "command": list(sys.argv)}

    # ---- frame build ------------------------------------------------
    # Build in a temporary directory first.  The governed output root is
    # published only through write-once atomic copies below.
    with tempfile.TemporaryDirectory() as td:
        build_tmp = build_seasonal_frame(daily_csv, Path(td))
        frame_bytes = Path(build_tmp["csv"]).read_bytes()
        prov_bytes = Path(build_tmp["provenance"]).read_bytes()
    frame_out = features_dir / "seasonal_frame_jja_2001_2025.csv"
    prov_out = features_dir / "seasonal_frame_provenance_v0.json"
    try:
        write_once_bytes(frame_out, frame_bytes)
        write_once_sidecar(frame_out)
        write_once_bytes(prov_out, prov_bytes)
        write_once_sidecar(prov_out)
    except ExistingEvidenceError as exc:
        print(f"REFUSED — output publication failed: {exc}")
        return 2
    build = dict(build_tmp)
    build["csv"] = frame_out
    build["provenance"] = prov_out
    frame = build["frame"]
    receipt["frame"] = {
        "csv": "features/seasonal_frame_jja_2001_2025.csv",
        "sha256": build["sha256"],
        "provenance": "features/seasonal_frame_provenance_v0.json",
        "n_rows": int(len(frame)),
        "dropped_basin_years": build["ledger"]
        ["dropped_basin_years"]}
    if len(frame) != 75:
        receipt["problems"].append(
            f"seasonal frame has {len(frame)} rows, expected 75 — "
            "basin-year ledger must be reconciled")
        _emit(receipt, run_dir)
        return 1
    prov_sha = _sha(Path(build["provenance"]))
    manifest = _manifest(build["sha256"], prov_sha, lane_root)

    # ---- Arm A: daily reference — full chain verified ---------------
    ref = verify_daily_reference(daily_root)
    receipt["arms"]["A_reference"] = {
        "bound_artifact_digest": ref["artifact_digest"],
        "receipt_sha256": ref["receipt_sha256"],
        "artifact_sha256": ref["artifact_sha256"],
        "daily_status": ref["status"],
        "verification": ref,
        "note": "daily P5-A2 result bound by live receipt+artifact "
                "bytes — never rerun under seasonal settings"}
    if not ref["verified"]:
        receipt["problems"].append(
            "Arm A daily reference chain failed verification: "
            + "; ".join(ref["problems"]))
        _emit(receipt, run_dir)
        return 1

    # ---- Arm B: the seasonal run -------------------------------------
    cfg = _config(manifest)
    mask = _train_mask(frame)
    artifact = run_regimes(frame, list(SEASONAL_FEATURES), mask, cfg)
    arm_b = {"covariance_type": cfg.covariance_type,
             "k_candidates": list(cfg.k_candidates),
             "status": artifact.get("status"),
             "reason": artifact.get("reason"),
             "terminal_reason": _terminal_reason(artifact),
             "failed_gates": _failed_gates(artifact)}
    if artifact.get("status") != "RUN_ERROR":
        frozen = freeze_regime_artifact(dict(artifact))
        art_path = run_dir / "seasonal_regime_artifact_v0.json"
        try:
            write_once_json(art_path, frozen, indent=1)
            write_once_sidecar(art_path)
        except ExistingEvidenceError as exc:
            print(f"REFUSED — output publication failed: {exc}")
            return 2
        arm_b["artifact"] = "run/seasonal_regime_artifact_v0.json"
        arm_b["artifact_sha256"] = _sha(art_path)
        arm_b["regime_artifact_digest"] = \
            frozen.get("regime_artifact_digest")
        arm_b["required_gates"] = (frozen.get("stability") or {}) \
            .get("required_gates")
        arm_b["gate_observations"] = (frozen.get("stability") or {}) \
            .get("gate_observations")
        arm_b["config_digest"] = frozen.get("config_digest")
        receipt["declared_config_digest"] = frozen.get("config_digest")
    receipt["arms"]["B_surface_core"] = arm_b

    # ---- Arm NC: declared negative control ---------------------------
    nc = _negative_control_frame(frame)
    nc_path = features_dir / "negative_control_frame.csv"
    try:
        write_once_bytes(nc_path, nc.to_csv(index=False).encode("utf-8"))
        write_once_sidecar(nc_path)
    except ExistingEvidenceError as exc:
        print(f"REFUSED — output publication failed: {exc}")
        return 2
    nc_cfg = _config(manifest, input_role="negative_control")
    nc_result = run_regimes(nc, list(SEASONAL_FEATURES),
                            _train_mask(nc), nc_cfg)
    nc_rejected = nc_result.get("status") == "RUN_ERROR"
    receipt["arms"]["NC_negative_control"] = {
        "frame": "features/negative_control_frame.csv",
        "frame_sha256": _sha(nc_path),
        "input_role": "negative_control",
        "engine_status": nc_result.get("status"),
        "engine_reason": nc_result.get("reason"),
        "rejected_before_fit": nc_rejected,
        "harness_integrity": ("PASS — nonphysical input refused "
                              "before model fitting"
                              if nc_rejected else
                              "HARNESS FAILURE — the engine "
                              "produced an artifact from a "
                              "declared negative-control input")}

    # ---- lane status --------------------------------------------------
    if not nc_rejected:
        receipt["status"] = "RUN_ERROR"
        receipt["problems"].append(
            "negative-control arm produced a scientific artifact — "
            "harness failure")
    else:
        receipt["status"] = arm_b["status"]
        receipt["terminal_reason"] = arm_b["terminal_reason"]
    receipt["execution"]["completed_utc"] = _utc_now()
    _emit(receipt, run_dir)
    print(json.dumps({k: receipt[k] for k in
                      ("status", "terminal_reason", "arms")
                      if k in receipt}, indent=1))
    return 0


def _emit(receipt: dict, run_dir: Path) -> None:
    execution = receipt.get("execution")
    if isinstance(execution, dict) and execution.get("completed_utc") is None:
        execution["completed_utc"] = _utc_now()
    out = run_dir / "seasonal_lane_receipt_v0.json"
    try:
        write_once_json(out, receipt, indent=1)
        write_once_sidecar(out)
    except ExistingEvidenceError as exc:
        raise SystemExit(f"REFUSED — output publication failed: {exc}")


if __name__ == "__main__":
    sys.exit(main())
