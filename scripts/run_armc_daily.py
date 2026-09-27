"""Arm C daily-grain regime lane runner (amendment v16).

Runs the three declared arms on the byte-bound daily frame under the
frozen daily gate envelope (identical to V1_CONFIG of the bound daily
lane): seeds 42/7/2024, K=1..5, full covariance, 200 bootstrap,
7-day blocks, 50 null replicates, alpha 0.05, temporal holdout,
LORO required, listwise missingness.

- negative-control arm must be refused before any fit (per arm)
- identical row universe across arms; source never a predictor
- honest negatives preserved; all authority flags false
"""
import dataclasses, json, os, sys, hashlib, datetime as _dt, platform
from pathlib import Path
sys.path.insert(0, "scripts")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from p5_safe_io import write_once_bytes, write_once_json, write_once_sidecar, ExistingEvidenceError
from nepal.science_v0.regimes import RegimeRunConfig, run_regimes, freeze_regime_artifact

ROOT = Path("/Users/sanjayb/nepal-event-anomaly-evidence/p5-armc-pressure-levels-2026-09-22")
FRAME_DIR = ROOT / "daily-frame-v1"
FRAME = FRAME_DIR / "armc_daily_frame_v0.csv"
UNITS = FRAME_DIR / "feature_units_v0.json"
PROV = FRAME_DIR / "armc_daily_frame_provenance_v0.json"
FCONTRACT = ROOT / "retrieval" / "armc_daily_feature_contract_v0.json"
RCONTRACT = ROOT / "retrieval" / "armc_daily_run_contract_v0.json"
OUT_ROOT = ROOT / "daily-run-v1"

AUTHORITY_FALSE = {"promotion_eligible": False, "production_authorized": False,
                   "warning_path_authorized": False, "operational_claim": False}
ALLOWED_STATUSES = {"UNSUPERVISED_STRUCTURE_NOT_STABLE", "CANDIDATE_ONLY",
                    "STABLE_REGIME_FOUND", "RUN_ERROR"}

def _sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def _verify_sidecar(p: Path) -> str:
    d = _sha(p)
    s = Path(str(p) + ".sha256")
    if not s.exists() or s.read_text().split()[0] != d:
        raise ValueError(f"sidecar mismatch: {p}")
    return d

def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _env() -> dict:
    v = {}
    for n in ("numpy", "pandas", "scikit-learn"):
        try:
            from importlib.metadata import version
            v[n] = version(n)
        except Exception:
            v[n] = "unavailable"
    return {"python": sys.version.split()[0], "platform": platform.platform(), "packages": v}

def _train_mask(frame: pd.DataFrame) -> np.ndarray:
    d = pd.to_datetime(frame["date"], errors="raise").dt.date
    return ((d >= _dt.date(2001, 6, 1)) & (d <= _dt.date(2017, 8, 31))).to_numpy()

def _negative_control(frame: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    rng = np.random.default_rng(20260923)
    out = frame.copy()
    v = out[cols].to_numpy(copy=True)
    out.loc[:, cols] = v[rng.permutation(len(v))]
    return out

def main() -> int:
    for p in (FRAME, UNITS, PROV, FCONTRACT, RCONTRACT):
        _verify_sidecar(p)
    fc = json.loads(FCONTRACT.read_text())
    rc = json.loads(RCONTRACT.read_text())
    if fc["schema"] != "P5_ARMC_DAILY_FEATURE_CONTRACT_V0":
        raise ValueError("wrong feature contract")
    if rc["schema"] != "P5_ARMC_DAILY_RUN_CONTRACT_V0":
        raise ValueError("wrong run contract")
    frame = pd.read_csv(FRAME)
    if len(frame) != 6900:
        raise ValueError("daily frame is not 6,900 rows")
    units_doc = json.loads(UNITS.read_text())
    prov_sha, frame_sha, units_sha = _sha(PROV), _sha(FRAME), _sha(UNITS)
    arms = fc["arms"]  # surface_only / pressure_only / combined
    missing = [c for c in rc["feature_cols"] if c not in frame.columns]
    if missing:
        raise ValueError(f"declared feature cols missing from frame: {missing}")
    if OUT_ROOT.exists():
        raise ExistingEvidenceError(f"refusing existing run root: {OUT_ROOT}")
    OUT_ROOT.mkdir(parents=True)
    receipt = {"schema": "P5_ARMC_DAILY_LANE_RECEIPT_V0",
               "claim_scope": "research_only_no_operational_authorization",
               "authority": dict(AUTHORITY_FALSE),
               "input": {"frame_sha256": frame_sha, "units_sha256": units_sha,
                         "frame_provenance_sha256": prov_sha,
                         "feature_contract_sha256": _sha(FCONTRACT),
                         "run_contract_sha256": _sha(RCONTRACT)},
               "arms": {}, "status": "RUN_ERROR", "problems": [],
               "execution": {"started_utc": _utc_now(), "environment": _env(),
                             "command": list(sys.argv)}}
    mask = _train_mask(frame)
    n_train = int(mask.sum())
    all_statuses = []
    for arm_name, cols in arms.items():
        arm_dir = OUT_ROOT / arm_name
        arm_dir.mkdir()
        sm = {"source_id": f"armc-daily-{arm_name}",
              "evidence_root": str(FRAME_DIR),
              "source_digests": [frame_sha, units_sha, prov_sha],
              "feature_allowlist": list(cols),
              "units": [units_doc["units"][c] for c in cols],
              "lineage": ("derived: daily JJA box-day aggregates over anchor boxes from byte-verified "
                          "pressure+single-level payloads (111 cds + 489+300 earthmover_icechunk "
                          "@snapshot ZFKDHBCTBVHVXM3BQFV0) + bound daily surface frame; terrain mask "
                          "sp>level applied; v16"),
              "source_files": [
                  {"relpath": "armc_daily_frame_v0.csv", "sha256": frame_sha},
                  {"relpath": "feature_units_v0.json", "sha256": units_sha},
                  {"relpath": "armc_daily_frame_provenance_v0.json", "sha256": prov_sha}]}
        cfg = RegimeRunConfig(
            seeds=tuple(rc["seeds"]), k_candidates=tuple(rc["k_candidates"]),
            n_bootstrap=int(rc["n_bootstrap"]), n_null_replicates=int(rc["n_null_replicates"]),
            null_alpha=float(rc["null_alpha"]), season_col="season", group_col="basin_group",
            era_col="era", era_boundaries=tuple(rc["era_boundaries"]),
            era_drift_max=float(rc["era_drift_max"]), missingness_policy="listwise",
            effort_waiver_reason=rc["effort_waiver_reason"],
            unit_col="unit_id", date_col="date", label_blinding=True, fitted_on="TRAIN_ONLY",
            train_groups=("gandaki", "karnali", "koshi"), heldout_groups=(), holdout_axis="temporal",
            temporal_train_interval=tuple(rc["temporal_train_interval"]),
            temporal_embargo_interval=tuple(rc["temporal_embargo_interval"]),
            temporal_holdout_interval=tuple(rc["temporal_holdout_interval"]),
            cadence="1D", bootstrap_block_len=int(rc["bootstrap_block_len"]),
            gap_policy="calendar", source_manifest=sm, mode="RETROSPECTIVE_REGIME",
            retrospective_data_class="REANALYSIS", covariance_type=rc["covariance_type"],
            frame_grain="daily", input_role="scientific",
            null_extra_strata_col="basin_group", loro_policy="required")
        nc = _negative_control(frame, cols)
        nc_cfg = dataclasses.replace(cfg, input_role="negative_control")
        nc_result = run_regimes(nc, cols, mask, nc_cfg)
        nc_refused = nc_result.get("status") == "RUN_ERROR" and "negative-control" in str(nc_result.get("reason", ""))
        arm_rec = {"n_rows": len(frame), "n_train": n_train, "feature_cols": cols,
                   "negative_control": {"engine_status": nc_result.get("status"),
                                        "engine_reason": nc_result.get("reason"),
                                        "rejected_before_fit": nc_refused,
                                        "harness_integrity": "PASS" if nc_refused else "FAIL"}}
        if not nc_refused:
            arm_rec["status"] = "RUN_ERROR"
            arm_rec["problems"] = ["negative-control arm was not refused before fitting"]
        else:
            artifact = run_regimes(frame, cols, mask, cfg)
            status = artifact.get("status")
            arm_rec["status"] = status if status in ALLOWED_STATUSES else "RUN_ERROR"
            arm_rec["terminal_reason"] = artifact.get("reason") or artifact.get("terminal_reason")
            gates = (artifact.get("stability") or {}).get("required_gates") or {}
            arm_rec["failed_gates"] = sorted(k for k, v in gates.items() if v is not True)
            if status != "RUN_ERROR":
                frozen = freeze_regime_artifact(dict(artifact))
                ap = arm_dir / f"armc_daily_{arm_name}_artifact_v0.json"
                write_once_json(ap, frozen, indent=1)
                write_once_sidecar(ap)
                arm_rec["artifact"] = {"relpath": f"{arm_name}/{ap.name}", "sha256": _sha(ap)}
        all_statuses.append(arm_rec["status"])
        receipt["arms"][arm_name] = arm_rec
        print(f"[{arm_name}] {arm_rec['status']} {str(arm_rec.get('terminal_reason'))[:100]}", flush=True)
    receipt["status"] = ("RUN_ERROR" if all(s == "RUN_ERROR" for s in all_statuses)
                         else max(set(all_statuses), key=all_statuses.count))
    receipt["execution"]["completed_utc"] = _utc_now()
    rp = OUT_ROOT / "armc_daily_lane_receipt_v0.json"
    write_once_json(rp, receipt, indent=2)
    write_once_sidecar(rp)
    print(json.dumps({"status": receipt["status"], "arms": {k: v["status"] for k, v in receipt["arms"].items()}}, indent=2))
    return 0

if __name__ == "__main__":
    sys.exit(main())
