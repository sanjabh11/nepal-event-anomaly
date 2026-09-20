#!/usr/bin/env python3
"""P5 seasonal lane (amendment v3) — basin-year JJA regime run.

Executes the locked seasonal estimand:
  Arm A  — the daily P5-A2 result bound by reference (never rerun).
  Arm B  — the six-feature seasonal contract on the byte-verified
           basin-year frame (tied covariance primary).
  Arm NC — a declared negative-control input (feature-shuffled
           basin-years, input_role="negative_control") that MUST be
           rejected before any model fitting; if the engine ever
           produced an artifact from it, the harness is broken.

Writes into the lane-local evidence root (LANE_ROOT):
  features/seasonal_frame_jja_2001_2025.csv (+sha256)
  features/seasonal_frame_provenance_v0.json (+sha256)
  features/negative_control_frame.csv (+sha256)
  run/seasonal_regime_artifact_v0.json (+sha256)
  run/seasonal_lane_receipt_v0.json (+sha256)
Verified independently by scripts/replay_seasonal_p5.py.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import sys
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

DAILY_ROOT = Path("/Users/sanjayb/nepal-event-anomaly-evidence/"
                  "p5-glof-2026-09-19")
DAILY_CSV = (DAILY_ROOT / "era5-multibasin/features/"
             "regime_frame_hma_jja_2001_2025.csv")
LANE_ROOT = Path("/Users/sanjayb/nepal-event-anomaly-evidence/"
                 "p5-seasonal-jja-2026-09-20")
FEATURES_DIR = LANE_ROOT / "features"
RUN_DIR = LANE_ROOT / "run"

# Arm A — the bound daily artifact digest (from the P5-A2 receipt;
# referenced, never re-derived).
DAILY_ARTIFACT_DIGEST = (
    "982e7b6e270dfd5e990ed6f2957e55485e1e9fbfd8e4d6ac1c6cfa93c3188327")
DAILY_RECEIPT = (DAILY_ROOT / "retrieval/"
                 "p5_glof_descriptive_receipt_v0.json")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _write_sidecar(p: Path) -> None:
    Path(str(p) + ".sha256").write_text(f"{_sha(p)}  {p.name}\n")


def _manifest(frame_sha: str, prov_sha: str) -> dict:
    return {
        "source_id": "era5-land-seasonal-jja-basin-year-v1",
        "source_digests": sorted([frame_sha, prov_sha]),
        "units": list(dict.fromkeys(SEASONAL_UNITS.values())),
        "feature_allowlist": list(SEASONAL_FEATURES),
        "lineage": "derived: seasonal aggregation of byte-verified "
                   "daily JJA frame (era5-multibasin/features/"
                   "regime_frame_hma_jja_2001_2025.csv) under "
                   "p5_amendment_v3_seasonal_estimand",
        "evidence_root": str(LANE_ROOT),
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


def main() -> int:
    FEATURES_DIR.mkdir(parents=True, exist_ok=True)
    RUN_DIR.mkdir(parents=True, exist_ok=True)

    receipt = {"record_type": "SEASONAL_LANE_RECEIPT_V0",
               "schema": "P5_SEASONAL_LANE_V0",
               "claim_scope": "research_only_no_operational_"
                              "authorization",
               "estimand": "basin-year JJA hydroclimate seasonal "
                           "types (75 rows = 3 basins x 25 seasons)",
               "arms": {}, "status": "RUN_ERROR", "problems": []}

    # ---- frame build ------------------------------------------------
    build = build_seasonal_frame(DAILY_CSV, FEATURES_DIR)
    frame = build["frame"]
    receipt["frame"] = {
        "csv": str(build["csv"]), "sha256": build["sha256"],
        "provenance": str(build["provenance"]),
        "n_rows": int(len(frame)),
        "dropped_basin_years": build["ledger"]
        ["dropped_basin_years"]}
    if len(frame) != 75:
        receipt["problems"].append(
            f"seasonal frame has {len(frame)} rows, expected 75 — "
            "basin-year ledger must be reconciled")
        _emit(receipt)
        return 1
    prov_sha = _sha(Path(build["provenance"]))
    manifest = _manifest(build["sha256"], prov_sha)

    # ---- Arm A: daily reference bound by digest ----------------------
    daily_rcpt_sha = _sha(DAILY_RECEIPT) if DAILY_RECEIPT.exists() \
        else None
    daily_status = None
    if DAILY_RECEIPT.exists():
        daily_status = json.loads(DAILY_RECEIPT.read_text()) \
            .get("status")
    receipt["arms"]["A_reference"] = {
        "bound_artifact_digest": DAILY_ARTIFACT_DIGEST,
        "receipt_sha256": daily_rcpt_sha,
        "daily_status": daily_status,
        "note": "daily P5-A2 result bound by reference — never "
                "rerun under seasonal settings"}

    # ---- Arm B: the seasonal run -------------------------------------
    cfg = _config(manifest)
    mask = _train_mask(frame)
    artifact = run_regimes(frame, list(SEASONAL_FEATURES), mask, cfg)
    arm_b = {"covariance_type": cfg.covariance_type,
             "k_candidates": list(cfg.k_candidates),
             "status": artifact.get("status"),
             "reason": artifact.get("reason")}
    if artifact.get("status") != "RUN_ERROR":
        frozen = freeze_regime_artifact(dict(artifact))
        art_path = RUN_DIR / "seasonal_regime_artifact_v0.json"
        art_path.write_text(json.dumps(frozen, indent=1,
                                       sort_keys=True) + "\n")
        _write_sidecar(art_path)
        arm_b["artifact"] = str(art_path)
        arm_b["artifact_sha256"] = _sha(art_path)
        arm_b["regime_artifact_digest"] = \
            frozen.get("regime_artifact_digest")
        arm_b["required_gates"] = (frozen.get("stability") or {}) \
            .get("required_gates")
    receipt["arms"]["B_surface_core"] = arm_b

    # ---- Arm NC: declared negative control ---------------------------
    nc = _negative_control_frame(frame)
    nc_path = FEATURES_DIR / "negative_control_frame.csv"
    nc.to_csv(nc_path, index=False)
    _write_sidecar(nc_path)
    nc_cfg = _config(manifest, input_role="negative_control")
    nc_result = run_regimes(nc, list(SEASONAL_FEATURES),
                            _train_mask(nc), nc_cfg)
    nc_rejected = nc_result.get("status") == "RUN_ERROR"
    receipt["arms"]["NC_negative_control"] = {
        "frame": str(nc_path), "frame_sha256": _sha(nc_path),
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
    _emit(receipt)
    print(json.dumps({k: receipt[k] for k in
                      ("status", "arms")}, indent=1))
    return 0


def _emit(receipt: dict) -> None:
    out = RUN_DIR / "seasonal_lane_receipt_v0.json"
    out.write_text(json.dumps(receipt, indent=1, sort_keys=True)
                   + "\n")
    _write_sidecar(out)


if __name__ == "__main__":
    sys.exit(main())
