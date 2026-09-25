#!/usr/bin/env python3
"""Run the Arm C extended seasonal regime lane.

This is a new lane, not a wrapper around ``run_seasonal_p5.py``.  The input
feature columns and configuration are supplied by a successor execution
record and are bound into the receipt before the engine is called.  A
negative-control arm is always exercised first and must be refused before any
scientific artifact is accepted.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as _dt
import hashlib
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from armc_frame_extend import _verify_sidecar, validate_feature_contract  # noqa: E402
from nepal.science_v0.regimes import (  # noqa: E402
    RegimeRunConfig,
    freeze_regime_artifact,
    run_regimes,
)
from p5_safe_io import (  # noqa: E402
    ExistingEvidenceError,
    sha256_bytes,
    write_once_bytes,
    write_once_json,
    write_once_sidecar,
)


RUN_SCHEMA = "P5_ARMC_RUN_CONTRACT_V0"
AUTHORITY_FALSE = {
    "promotion_eligible": False,
    "production_authorized": False,
    "warning_path_authorized": False,
    "operational_claim": False,
}
ALLOWED_STATUSES = {
    "CANDIDATE_ONLY",
    "DESCRIPTIVE_REGIME_ONLY",
    "UNSUPERVISED_STRUCTURE_NOT_STABLE",
    "UNDERPOWERED_DESCRIPTIVE_ONLY",
    "RUN_ERROR",
}


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _environment() -> dict[str, Any]:
    from importlib.metadata import version

    packages: dict[str, str] = {}
    for name in ("numpy", "pandas", "scikit-learn", "xarray", "netCDF4"):
        try:
            packages[name] = version(name)
        except Exception:
            packages[name] = "unavailable"
    value = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "packages": packages,
    }
    value["digest"] = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return value


def _validate_run_contract(value: Mapping[str, Any], feature_cols: list[str]) -> dict[str, Any]:
    if value.get("schema") != RUN_SCHEMA:
        raise ValueError(f"run contract schema must be {RUN_SCHEMA}")
    if value.get("claim_scope") != "research_only_no_operational_authorization":
        raise ValueError("run contract exceeds the research-only claim ceiling")
    declared = value.get("feature_cols")
    if declared != feature_cols:
        raise ValueError("run contract feature_cols do not match the byte-bound frame")
    if value.get("frame_grain") != "seasonal":
        raise ValueError("Arm C run must declare seasonal frame grain")
    if value.get("covariance_type") not in ("tied", "diag"):
        raise ValueError("Arm C covariance_type must be tied or diag")
    if value.get("input_role") != "scientific":
        raise ValueError("scientific Arm C run must declare input_role scientific")
    if value.get("holdout_axis") != "temporal":
        raise ValueError("Arm C must retain the temporal holdout axis")
    if value.get("loro_policy") != "diagnostic":
        raise ValueError("Arm C must retain diagnostic LORO policy")
    seeds = value.get("seeds")
    if not isinstance(seeds, list) or len(seeds) < 3 or any(
        isinstance(seed, bool) or not isinstance(seed, int) or seed < 0 for seed in seeds
    ) or len(set(seeds)) != len(seeds):
        raise ValueError("run contract seeds must be distinct non-negative integers")
    ks = value.get("k_candidates")
    if ks != [1, 2, 3, 4]:
        raise ValueError("Arm C must retain K candidates [1, 2, 3, 4]")
    for key in ("n_bootstrap", "n_null_replicates", "bootstrap_block_len"):
        if isinstance(value.get(key), bool) or not isinstance(value.get(key), int) or value[key] <= 0:
            raise ValueError(f"run contract {key} must be a positive integer")
    if value.get("missingness_policy") != "listwise":
        raise ValueError("Arm C must retain listwise missingness")
    if value.get("null_extra_strata_col") != "basin_group":
        raise ValueError("Arm C must retain basin-group null strata")
    return dict(value)


def build_regime_config(
    run_contract: Mapping[str, Any],
    *,
    source_manifest: Mapping[str, Any],
    input_role: str,
) -> RegimeRunConfig:
    """Translate the declared JSON contract without deriving science choices."""
    return RegimeRunConfig(
        seeds=tuple(run_contract["seeds"]),
        k_candidates=tuple(run_contract["k_candidates"]),
        n_bootstrap=int(run_contract["n_bootstrap"]),
        n_null_replicates=int(run_contract["n_null_replicates"]),
        null_alpha=float(run_contract.get("null_alpha", 0.05)),
        season_col="season",
        group_col="basin_group",
        era_col="era",
        era_boundaries=tuple(run_contract["era_boundaries"]),
        era_drift_max=float(run_contract.get("era_drift_max", 0.5)),
        missingness_policy="listwise",
        effort_waiver_reason=str(run_contract["effort_waiver_reason"]),
        unit_col="unit_id",
        date_col="date",
        label_blinding=True,
        fitted_on="TRAIN_ONLY",
        train_groups=("gandaki", "karnali", "koshi"),
        heldout_groups=(),
        holdout_axis="temporal",
        temporal_train_interval=tuple(run_contract["temporal_train_interval"]),
        temporal_embargo_interval=tuple(run_contract["temporal_embargo_interval"]),
        temporal_holdout_interval=tuple(run_contract["temporal_holdout_interval"]),
        cadence="365D",
        bootstrap_block_len=int(run_contract["bootstrap_block_len"]),
        gap_policy="calendar",
        source_manifest=dict(source_manifest),
        mode="RETROSPECTIVE_REGIME",
        retrospective_data_class="REANALYSIS",
        covariance_type=str(run_contract["covariance_type"]),
        frame_grain="seasonal",
        input_role=input_role,
        null_extra_strata_col="basin_group",
        loro_policy="diagnostic",
    )


def _train_mask(frame: pd.DataFrame) -> np.ndarray:
    dates = pd.to_datetime(frame["date"], errors="raise").dt.date
    return ((dates >= _dt.date(2001, 6, 1)) & (dates <= _dt.date(2017, 8, 31))).to_numpy()


def _negative_control(frame: pd.DataFrame, feature_cols: list[str]) -> pd.DataFrame:
    rng = np.random.default_rng(20260922)
    out = frame.copy()
    values = out[feature_cols].to_numpy(copy=True)
    out.loc[:, feature_cols] = values[rng.permutation(len(values))]
    return out


def _failed_gates(artifact: Mapping[str, Any]) -> list[str]:
    gates = (artifact.get("stability") or {}).get("required_gates")
    if not isinstance(gates, Mapping):
        return []
    return sorted(name for name, passed in gates.items() if passed is not True)


def _terminal_reason(artifact: Mapping[str, Any]) -> str | None:
    if artifact.get("status") == "RUN_ERROR":
        return str(artifact.get("reason", "run error"))
    failed = _failed_gates(artifact)
    return None if not failed else f"{artifact.get('status')} — failed required gates: {', '.join(failed)}"


def _load_input(
    frame_path: Path,
    units_path: Path,
    feature_contract_path: Path,
) -> tuple[pd.DataFrame, dict[str, Any], str, str]:
    frame_sha = _verify_sidecar(frame_path)
    units_sha = _verify_sidecar(units_path)
    frame = pd.read_csv(frame_path)
    feature_contract = _read_json(feature_contract_path)
    feature_contract = validate_feature_contract(feature_contract)
    feature_cols = list(feature_contract["feature_cols"])
    missing = sorted(set(feature_cols) - set(frame.columns))
    if missing:
        raise ValueError(f"declared Arm C feature columns missing from frame: {missing}")
    if len(frame) != 75 or frame.duplicated(["unit_id", "season_year"]).any():
        raise ValueError("Arm C frame must remain exactly 75 unique basin-year rows")
    units = _read_json(units_path)
    if units.get("schema") != "P5_FEATURE_UNITS_V0" or not isinstance(units.get("units"), Mapping):
        raise ValueError("feature_units sidecar record has the wrong schema")
    for col in feature_cols:
        if col not in units["units"]:
            raise ValueError(f"feature_units sidecar lacks {col}")
        if units["units"][col] != feature_contract["feature_units"].get(col):
            raise ValueError(f"feature_units mismatch for {col}")
    return frame, feature_contract, frame_sha, units_sha


def run_armc(
    *,
    frame_path: Path,
    units_path: Path,
    feature_contract_path: Path,
    run_contract_path: Path,
    output_root: Path,
) -> dict[str, Any]:
    frame, feature_contract, frame_sha, units_sha = _load_input(
        Path(frame_path), Path(units_path), Path(feature_contract_path)
    )
    feature_cols = list(feature_contract["feature_cols"])
    provenance_path = Path(frame_path).resolve().parent / "seasonal_frame_provenance_v0.json"
    _verify_sidecar(provenance_path)
    frame_prov = _read_json(provenance_path)
    if frame_prov.get("schema") != "P5_ARMC_EXTENDED_FRAME_PROVENANCE_V0":
        raise ValueError("frame provenance schema mismatch")
    excluded = {
        str(item["feature"])
        for item in frame_prov.get("collinearity_exclusions", [])
        if isinstance(item, Mapping)
    }
    fit_cols = [c for c in feature_cols if c not in excluded]
    run_contract = _validate_run_contract(_read_json(Path(run_contract_path)), feature_cols)
    run_contract_sha = _sha(Path(run_contract_path))
    root = Path(output_root).resolve()
    if root.exists():
        raise ExistingEvidenceError(f"refusing to replace existing Arm C run root: {root}")
    root.mkdir(parents=True, exist_ok=False)
    run_dir = root / "run"
    features_dir = root / "features"
    input_dir = root / "input"
    run_dir.mkdir(parents=True, exist_ok=False)
    features_dir.mkdir(parents=True, exist_ok=False)
    input_dir.mkdir(parents=True, exist_ok=False)

    # Copy the exact input bytes into this new run root before fitting.  The
    # receipt never claims that an external path is inside the run root.
    input_frame = input_dir / "seasonal_frame_jja_2001_2025.csv"
    input_units = input_dir / "feature_units_v0.json"
    write_once_bytes(input_frame, Path(frame_path).read_bytes())
    write_once_sidecar(input_frame)
    write_once_bytes(input_units, Path(units_path).read_bytes())
    write_once_sidecar(input_units)
    input_prov = input_dir / "seasonal_frame_provenance_v0.json"
    write_once_bytes(input_prov, provenance_path.read_bytes())
    write_once_sidecar(input_prov)

    started = _utc_now()
    units_doc = _read_json(Path(units_path))
    prov_sha = _sha(provenance_path)
    evidence_root = Path(frame_path).resolve().parent
    source_manifest = {
        "source_id": "armc-pressure-level-extended-seasonal-frame",
        "source_digests": [frame_sha, units_sha, prov_sha],
        "feature_allowlist": feature_cols,
        "evidence_root": str(evidence_root),
        "units": [units_doc["units"][c] for c in feature_cols],
        "lineage": (
            "derived: JJA box-year seasonal aggregates over anchor boxes from byte-verified "
            "600-chunk pressure-level payloads (111 cds + 489 earthmover_icechunk@snapshot "
            "ZFKDHBCTBVHVXM3BQFV0) via armc_monthly_split + armc_frame_extend under "
            "p5_amendment_v8/v10/v15; extended columns fitted minus collinearity exclusions"
        ),
        "source_files": [
            {"relpath": str(Path(frame_path).resolve().relative_to(evidence_root)), "sha256": frame_sha},
            {"relpath": str(Path(units_path).resolve().relative_to(evidence_root)), "sha256": units_sha},
            {"relpath": str(provenance_path.relative_to(evidence_root)), "sha256": prov_sha},
        ],
    }
    cfg = build_regime_config(run_contract, source_manifest=source_manifest, input_role="scientific")
    negative = _negative_control(frame, fit_cols)
    negative_path = features_dir / "negative_control_frame.csv"
    write_once_bytes(negative_path, negative.to_csv(index=False).encode("utf-8"))
    write_once_sidecar(negative_path)
    nc_cfg = dataclasses.replace(cfg, input_role="negative_control")
    nc_result = run_regimes(negative, fit_cols, _train_mask(negative), nc_cfg)
    nc_refused = nc_result.get("status") == "RUN_ERROR" and "negative-control" in str(nc_result.get("reason", ""))

    receipt: dict[str, Any] = {
        "schema": "P5_ARMC_LANE_RECEIPT_V0",
        "record_type": "armc_lane_receipt",
        "claim_scope": "research_only_no_operational_authorization",
        "authority": dict(AUTHORITY_FALSE),
        "estimand": "75-row basin-year JJA descriptive extension with compact pressure-level diagnostics",
        "input": {
            "frame_relpath": "input/seasonal_frame_jja_2001_2025.csv",
            "frame_sha256": frame_sha,
            "feature_units_relpath": "input/feature_units_v0.json",
            "feature_units_sha256": units_sha,
            "run_contract_sha256": run_contract_sha,
            "feature_cols": feature_cols,
            "fit_feature_cols": fit_cols,
            "collinearity_exclusions": sorted(excluded),
            "frame_provenance_sha256": _sha(provenance_path),
        },
        "negative_control": {
            "frame_relpath": "features/negative_control_frame.csv",
            "frame_sha256": _sha(negative_path),
            "engine_status": nc_result.get("status"),
            "engine_reason": nc_result.get("reason"),
            "rejected_before_fit": nc_refused,
            "harness_integrity": "PASS" if nc_refused else "FAIL",
        },
        "status": "RUN_ERROR",
        "problems": [],
        "execution": {
            "activity_id": f"p5-armc-{started}-{os.getpid()}",
            "started_utc": started,
            "completed_utc": None,
            "environment": _environment(),
            "command": list(sys.argv),
        },
    }
    if not nc_refused:
        receipt["problems"].append("negative-control arm was not refused before fitting")
    else:
        artifact = run_regimes(frame, fit_cols, _train_mask(frame), cfg)
        status = artifact.get("status")
        if status not in ALLOWED_STATUSES:
            receipt["problems"].append(f"engine emitted undeclared status {status!r}")
        receipt["status"] = status if status in ALLOWED_STATUSES else "RUN_ERROR"
        receipt["terminal_reason"] = _terminal_reason(artifact)
        receipt["failed_gates"] = _failed_gates(artifact)
        if status != "RUN_ERROR":
            frozen = freeze_regime_artifact(dict(artifact))
            artifact_path = run_dir / "armc_regime_artifact_v0.json"
            write_once_json(artifact_path, frozen, indent=1)
            write_once_sidecar(artifact_path)
            receipt["artifact"] = {
                "relpath": "run/armc_regime_artifact_v0.json",
                "file_sha256": _sha(artifact_path),
                "semantic_digest": frozen.get("regime_artifact_digest"),
                "config_digest": frozen.get("config_digest"),
            }
    receipt["execution"]["completed_utc"] = _utc_now()
    receipt_path = run_dir / "armc_lane_receipt_v0.json"
    write_once_json(receipt_path, receipt, indent=1)
    write_once_sidecar(receipt_path)
    return receipt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Execute the separately declared Arm C seasonal regime lane.")
    parser.add_argument("--frame", required=True, type=Path)
    parser.add_argument("--feature-units", required=True, type=Path)
    parser.add_argument("--feature-contract", required=True, type=Path)
    parser.add_argument("--run-contract", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        receipt = run_armc(
            frame_path=args.frame,
            units_path=args.feature_units,
            feature_contract_path=args.feature_contract,
            run_contract_path=args.run_contract,
            output_root=args.output_root,
        )
    except (OSError, ValueError, ExistingEvidenceError) as exc:
        print(json.dumps({"status": "RUN_ERROR", "problems": [str(exc)], "authority": AUTHORITY_FALSE}, indent=2))
        return 2
    print(json.dumps({"status": receipt["status"], "authority": receipt["authority"], "negative_control": receipt["negative_control"]}, indent=2))
    return 0 if receipt["status"] != "RUN_ERROR" else 1


if __name__ == "__main__":
    raise SystemExit(main())
