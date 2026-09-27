#!/usr/bin/env python3
"""Arm C NetCDF validation and seasonal-frame extension machinery.

The implementation is intentionally data-dependent and fail-closed.  It
does not select scientific features, infer a basin geometry, interpolate a
grid, impute missing values, or silently accept partial years.  A successor
feature-contract record must explicitly name the <=6 extended features before
this module can produce a frame.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd
import xarray as xr

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from armc_cds_retrieve import (  # noqa: E402
    EXPECTED_GRID_DEG,
    EXPECTED_LEVELS,
    EXPECTED_VARIABLES,
    build_request_plan,
    expected_grid,
    load_contract,
)
from p5_safe_io import (  # noqa: E402
    ExistingEvidenceError,
    sha256_bytes,
    write_once_bytes,
    write_once_json,
    write_once_sidecar,
)


FEATURE_SCHEMA = "P5_ARMC_FEATURE_CONTRACT_V0"
SUPPORTED_AGGREGATIONS = frozenset({"jja_mean", "jja_q95"})
JJA_MONTHS = (6, 7, 8)
JJA_DAYS = 92
EXPECTED_ROWS = 75
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verify_sidecar(path: Path) -> str:
    digest = _sha(path)
    sidecar = Path(str(path) + ".sha256")
    if not sidecar.exists():
        raise ValueError(f"missing sidecar for {path}")
    declared = sidecar.read_text(encoding="utf-8").split()
    if not declared or declared[0] != digest:
        raise ValueError(f"sidecar mismatch for {path}")
    if not SHA256_RE.fullmatch(declared[0]):
        raise ValueError(f"malformed sidecar digest for {path}")
    return digest


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def validate_feature_contract(value: Mapping[str, Any]) -> dict[str, Any]:
    if value.get("schema") != FEATURE_SCHEMA:
        raise ValueError(f"feature contract schema must be {FEATURE_SCHEMA}")
    if value.get("claim_scope") != "research_only_no_operational_authorization":
        raise ValueError("feature contract exceeds the research-only claim ceiling")
    base = value.get("base_feature_cols")
    if not isinstance(base, list) or not base or not all(isinstance(c, str) and c for c in base):
        raise ValueError("base_feature_cols must be a non-empty list")
    extended = value.get("extended_features")
    if not isinstance(extended, list) or not 1 <= len(extended) <= 6:
        raise ValueError("extended_features must contain 1 through 6 declared features")
    names: list[str] = []
    for index, item in enumerate(extended):
        if not isinstance(item, Mapping):
            raise ValueError(f"extended_features[{index}] must be an object")
        name = item.get("name")
        variable = item.get("source_variable")
        level = item.get("pressure_level_hpa")
        aggregation = item.get("aggregation")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            raise ValueError(f"extended_features[{index}].name is unsafe")
        if name in names or name in base:
            raise ValueError(f"duplicate feature name {name!r}")
        if variable not in EXPECTED_VARIABLES:
            raise ValueError(f"undeclared source variable {variable!r}")
        if level not in EXPECTED_LEVELS:
            raise ValueError(f"undeclared pressure level {level!r}")
        if aggregation not in SUPPORTED_AGGREGATIONS:
            raise ValueError(f"unsupported aggregation {aggregation!r}")
        names.append(name)
    units = value.get("feature_units")
    if not isinstance(units, Mapping):
        raise ValueError("feature_units mapping is mandatory")
    for name in base + names:
        if not isinstance(units.get(name), str) or not units[name].strip():
            raise ValueError(f"feature_units missing for {name}")
    threshold = value.get("collinearity_threshold")
    if threshold != 0.95:
        raise ValueError("collinearity_threshold must remain the frozen 0.95")
    if value.get("feature_cols") != base + names:
        raise ValueError("feature_cols must equal base_feature_cols + extended_features")
    return dict(value)


def _expected_times(year: int, month: int) -> pd.DatetimeIndex:
    start = pd.Timestamp(year=year, month=month, day=1, tz="UTC")
    end = start + pd.offsets.MonthEnd(1) + pd.Timedelta(hours=23)
    return pd.date_range(start, end, freq="h")


def _to_utc_index(values: Any) -> pd.DatetimeIndex:
    try:
        index = pd.DatetimeIndex(values)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"payload time coordinate is not datetime-like: {exc}") from exc
    if index.tz is None:
        return index.tz_localize("UTC")
    return index.tz_convert("UTC")


def _coordinate_values(ds: xr.Dataset, name: str) -> np.ndarray:
    if name not in ds.coords:
        raise ValueError(f"payload is missing coordinate {name!r}")
    values = np.asarray(ds.coords[name].values, dtype=np.float64)
    if values.ndim != 1 or len(values) == 0 or not np.isfinite(values).all():
        raise ValueError(f"payload coordinate {name!r} is not a finite 1-D axis")
    if len(np.unique(values)) != len(values):
        raise ValueError(f"payload coordinate {name!r} contains duplicates")
    return values


def validate_payload_dataset(
    ds: xr.Dataset,
    chunk: Mapping[str, Any],
    *,
    require_complete: bool = True,
) -> dict[str, Any]:
    """Validate one chunk and return a spatially reduced UTC time series."""
    variable = str(chunk["variable"])
    level = int(chunk["pressure_level_hpa"])
    if variable not in ds.data_vars:
        raise ValueError(f"payload missing declared variable {variable}")
    if "pressure_level" not in ds.coords and "pressure_level" not in ds.dims:
        raise ValueError("payload missing pressure_level coordinate")
    if "time" not in ds.coords and "time" not in ds.dims:
        raise ValueError("payload missing time coordinate")
    if "latitude" not in ds.coords or "longitude" not in ds.coords:
        raise ValueError("payload must expose latitude/longitude coordinates")
    expected = expected_grid(tuple(float(v) for v in chunk["area_nwse"]))
    lat = _coordinate_values(ds, "latitude")
    lon = _coordinate_values(ds, "longitude")
    if not np.allclose(lat, expected["latitude"], atol=1e-8, rtol=0.0):
        raise ValueError("payload latitude axis does not match the complete requested grid")
    if not np.allclose(lon, expected["longitude"], atol=1e-8, rtol=0.0):
        raise ValueError("payload longitude axis does not match the complete requested grid")
    levels = np.asarray(ds.coords["pressure_level"].values)
    if levels.ndim != 1 or level not in {int(v) for v in levels}:
        raise ValueError(f"payload lacks declared pressure level {level}")
    anchor = chunk["anchor_box_nwse"]
    north, west, south, east = (float(v) for v in anchor)
    lat_mask = (lat >= south - 1e-8) & (lat <= north + 1e-8)
    lon_mask = (lon >= west - 1e-8) & (lon <= east + 1e-8)
    if not lat_mask.any() or not lon_mask.any():
        raise ValueError("frozen anchor box contains no complete returned grid cell")
    times = _to_utc_index(ds.coords["time"].values)
    if times.has_duplicates or not times.is_monotonic_increasing:
        raise ValueError("payload time axis must be unique and increasing")
    year, month = int(chunk["year"]), int(chunk["month"])
    expected_times = _expected_times(year, month)
    if require_complete and not times.equals(expected_times):
        raise ValueError(
            f"partial or non-UTC coverage in {chunk['chunk_id']}: "
            f"{len(times)} timestamps, expected {len(expected_times)}"
        )
    if not times.isin(expected_times).all():
        raise ValueError("payload contains timestamps outside its declared year/month")
    data = ds[variable]
    if "pressure_level" not in data.dims:
        raise ValueError("declared variable lacks pressure_level dimension")
    reduced = data.sel(pressure_level=level)
    reduced = reduced.isel(
        latitude=np.flatnonzero(lat_mask), longitude=np.flatnonzero(lon_mask)
    ).mean(dim=("latitude", "longitude"), skipna=False)
    values = np.asarray(reduced.values, dtype=np.float64)
    if values.shape != (len(times),) or not np.isfinite(values).all():
        raise ValueError("payload has non-finite or unexpected reduced values")
    return {
        "chunk_id": str(chunk["chunk_id"]),
        "basin": str(chunk["basin"]),
        "year": year,
        "month": month,
        "variable": variable,
        "pressure_level_hpa": level,
        "time": times,
        "values": values,
        "grid_cells_used": {
            "latitude": [float(v) for v in lat[lat_mask]],
            "longitude": [float(v) for v in lon[lon_mask]],
        },
        "complete_cell_count": int(lat_mask.sum() * lon_mask.sum()),
    }


def _validate_base_frame(path: Path) -> tuple[pd.DataFrame, str]:
    digest = _verify_sidecar(path)
    frame = pd.read_csv(path)
    required = {"unit_id", "season_year", "date", "basin_group", "season"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"base seasonal frame missing carrier columns {missing}")
    if len(frame) != EXPECTED_ROWS:
        raise ValueError(f"base seasonal frame must have exactly {EXPECTED_ROWS} rows")
    if frame.duplicated(["unit_id", "season_year"]).any():
        raise ValueError("base seasonal frame has duplicate basin-year keys")
    if set(frame["season"].astype(str)) != {"JJA"}:
        raise ValueError("base seasonal frame is not JJA-only")
    return frame, digest


def _load_payload_manifest(payload_root: Path) -> dict[str, Any]:
    path = payload_root / "retrieval/armc_retrieval_manifest_v0.json"
    _verify_sidecar(path)
    manifest = _read_json(path)
    if manifest.get("schema") != "P5_ARMC_RETRIEVAL_MANIFEST_V0":
        raise ValueError("payload manifest schema mismatch")
    if manifest.get("status") not in {
        "RETRIEVAL_RECEIVED_PENDING_NETCDF_VALIDATION",
        "RETRIEVAL_VERIFIED",
    }:
        raise ValueError("payload manifest is not an admitted retrieval state")
    return manifest


def _load_split_manifest(path: Path) -> dict[str, Any]:
    path = Path(path)
    _verify_sidecar(path)
    manifest = _read_json(path)
    if manifest.get("schema") != "P5_ARMC_MONTHLY_SPLIT_MANIFEST_V0":
        raise ValueError("monthly split manifest schema mismatch")
    if manifest.get("status") != "SPLIT_VERIFIED":
        raise ValueError("monthly split manifest is not verified")
    records = manifest.get("chunks")
    if not isinstance(records, list):
        raise ValueError("monthly split manifest chunks are missing")
    if manifest.get("monthly_record_count") != len(records):
        raise ValueError("monthly split manifest record count mismatch")
    if manifest.get("claim_scope") != "research_only_no_operational_authorization":
        raise ValueError("monthly split manifest exceeds the research-only scope")
    return manifest


def _chunk_map(
    payload_root: Path,
    contract: Mapping[str, Any],
    plan: list[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    manifest = _load_payload_manifest(payload_root)
    records = manifest.get("chunks")
    if not isinstance(records, list):
        raise ValueError("payload manifest chunks are missing")
    by_id = {str(item.get("chunk_id")): item for item in records if isinstance(item, Mapping)}
    expected = {str(item["chunk_id"]): dict(item) for item in plan}
    if set(by_id) != set(expected):
        raise ValueError("payload manifest is missing chunks or contains undeclared chunks")
    result: dict[str, dict[str, Any]] = {}
    for chunk_id, item in expected.items():
        record = by_id[chunk_id]
        payload_rel = record.get("payload_relpath")
        if not isinstance(payload_rel, str) or not payload_rel.startswith("retrieval/payloads/"):
            raise ValueError(f"unsafe payload path for {chunk_id}")
        path = (payload_root / payload_rel).resolve()
        if payload_root.resolve() not in path.parents:
            raise ValueError(f"payload path escapes root for {chunk_id}")
        actual = _verify_sidecar(path)
        if actual != record.get("response_sha256"):
            raise ValueError(f"payload digest mismatch for {chunk_id}")
        item["anchor_box_nwse"] = contract["anchor_boxes"][item["basin"]]
        result[chunk_id] = {**item, "payload_path": path, "payload_sha256": actual}
    return result


def _split_chunk_map(
    payloads_dir: Path,
    split_manifest: Path,
    contract: Mapping[str, Any],
    plan: list[Mapping[str, Any]],
    source_map: Mapping[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Resolve monthly derived payloads from the independent split manifest."""
    payloads_dir = Path(payloads_dir).resolve()
    manifest = _load_split_manifest(Path(split_manifest))
    records = manifest["chunks"]
    by_id = {str(item.get("chunk_id")): item for item in records if isinstance(item, Mapping)}
    expected = {str(item["chunk_id"]): dict(item) for item in plan}
    if set(by_id) != set(expected):
        raise ValueError("monthly split manifest is missing or has undeclared chunks")
    result: dict[str, dict[str, Any]] = {}
    for chunk_id, item in expected.items():
        record = by_id[chunk_id]
        payload_rel = record.get("payload_relpath")
        if not isinstance(payload_rel, str) or Path(payload_rel).is_absolute():
            raise ValueError(f"unsafe monthly payload path for {chunk_id}")
        path = (payloads_dir / payload_rel).resolve()
        if payloads_dir not in path.parents:
            raise ValueError(f"monthly payload path escapes root for {chunk_id}")
        actual = _verify_sidecar(path)
        declared = record.get("payload_sha256", record.get("response_sha256"))
        if actual != declared or record.get("response_sha256", actual) != actual:
            raise ValueError(f"monthly payload digest mismatch for {chunk_id}")
        item["anchor_box_nwse"] = contract["anchor_boxes"][item["basin"]]
        raw_id = None
        derived = record.get("derived_from")
        if isinstance(derived, Mapping):
            raw_id = derived.get("raw_chunk_id")
        item["source"] = (source_map or {}).get(str(raw_id), "unresolved")
        result[chunk_id] = {
            **item,
            "payload_path": path,
            "payload_sha256": actual,
            "derived_from": record.get("derived_from"),
        }
    return result


def build_extended_frame(
    *,
    amendment_v10: Path,
    base_frame: Path,
    feature_contract_path: Path,
    output_dir: Path,
    payload_root: Path | None = None,
    payloads_dir: Path | None = None,
    split_manifest: Path | None = None,
) -> dict[str, Any]:
    """Build and publish a byte-bound 75-row extended seasonal frame."""
    contract = load_contract(Path(amendment_v10))
    feature_contract = validate_feature_contract(_read_json(Path(feature_contract_path)))
    base, base_sha = _validate_base_frame(Path(base_frame))
    plan = build_request_plan(contract)
    if payload_root is not None and (payloads_dir is not None or split_manifest is not None):
        raise ValueError("payload-root cannot be combined with payloads-dir/split-manifest")
    if payload_root is None:
        if payloads_dir is None or split_manifest is None:
            raise ValueError("payload-root or both payloads-dir and split-manifest are required")
        source_map_path = Path(payloads_dir).resolve() / "retrieval" / "armc_source_map_v15.json"
        source_map = None
        source_map_sha = None
        if source_map_path.is_file():
            _verify_sidecar(source_map_path)
            sm = _read_json(source_map_path)
            if sm.get("schema") == "P5_ARMC_SOURCE_MAP_V15":
                source_map = {k: v.get("source") for k, v in sm.get("chunks", {}).items()}
                source_map_sha = _sha(source_map_path)
        chunks = _split_chunk_map(payloads_dir, split_manifest, contract, plan, source_map=source_map)
        payload_mode = "monthly_split_manifest"
    else:
        chunks = _chunk_map(Path(payload_root).resolve(), contract, plan)
        payload_mode = "retrieval_manifest"

    series: dict[tuple[str, int, str, int], list[dict[str, Any]]] = {}
    provenance_cells: dict[str, Any] = {}
    for chunk_id in sorted(chunks):
        info = chunks[chunk_id]
        path = info["payload_path"]
        with xr.open_dataset(path, engine="netcdf4", decode_times=True) as ds:
            result = validate_payload_dataset(ds, info)
        key = (result["basin"], result["year"], result["variable"], result["pressure_level_hpa"])
        series.setdefault(key, []).append(result)
        provenance_cells[chunk_id] = {
            "payload_sha256": info["payload_sha256"],
            "grid_cells_used": result["grid_cells_used"],
            "complete_cell_count": result["complete_cell_count"],
            "source": info.get("source"),
            "raw_chunk_id": (info.get("derived_from") or {}).get("raw_chunk_id")
            if isinstance(info.get("derived_from"), Mapping) else None,
        }

    new_rows: list[dict[str, Any]] = []
    for basin in sorted(contract["anchor_boxes"]):
        for year in contract["years"]:
            row: dict[str, Any] = {"unit_id": basin, "season_year": int(year)}
            for feature in feature_contract["extended_features"]:
                key = (
                    basin,
                    int(year),
                    str(feature["source_variable"]),
                    int(feature["pressure_level_hpa"]),
                )
                parts = series.get(key, [])
                if len(parts) != len(JJA_MONTHS):
                    raise ValueError(f"missing complete JJA month chunks for {key}")
                times = pd.DatetimeIndex(np.concatenate([part["time"] for part in parts]))
                values = np.concatenate([part["values"] for part in parts])
                expected = pd.date_range(
                    f"{year}-06-01T00:00:00Z",
                    f"{year}-08-31T23:00:00Z",
                    freq="h",
                )
                if not times.equals(expected):
                    raise ValueError(f"incomplete or duplicate JJA coverage for {key}")
                if feature["aggregation"] == "jja_mean":
                    value = float(np.mean(values))
                else:
                    value = float(np.percentile(values, 95))
                row[feature["name"]] = value
            new_rows.append(row)
    new_frame = pd.DataFrame(new_rows)
    if len(new_frame) != EXPECTED_ROWS:
        raise ValueError("extended diagnostic frame did not produce 75 rows")

    joined = base.merge(new_frame, on=["unit_id", "season_year"], how="left", validate="one_to_one")
    if len(joined) != EXPECTED_ROWS:
        raise ValueError("extended frame join changed the 75-row grain")
    extended_names = [str(item["name"]) for item in feature_contract["extended_features"]]
    missingness = {name: float(joined[name].isna().mean()) for name in extended_names}
    too_missing = [name for name, fraction in missingness.items() if fraction > 0.25]
    if too_missing:
        raise ValueError(f"new feature missingness exceeds 0.25: {too_missing}")
    base_names = list(feature_contract["base_feature_cols"])
    missing_base = [name for name in base_names if name not in joined.columns]
    if missing_base:
        raise ValueError(f"base feature contract columns missing: {missing_base}")

    exclusions: list[dict[str, Any]] = []
    retained = list(extended_names)
    threshold = float(feature_contract["collinearity_threshold"])
    for new_name in extended_names:
        for base_name in base_names:
            pair = joined[[new_name, base_name]].dropna()
            if len(pair) < 3:
                continue
            corr = float(pair[new_name].corr(pair[base_name]))
            if np.isfinite(corr) and abs(corr) > threshold:
                exclusions.append({"feature": new_name, "against": base_name, "abs_r": abs(corr), "threshold": threshold})
                if new_name in retained:
                    retained.remove(new_name)
                break
    # The declared surface is kept byte-complete in the frame; collinearity
    # exclusions are recorded here and applied at fit time by the runner.
    output = joined
    feature_units = {
        name: feature_contract["feature_units"][name]
        for name in base_names + extended_names
    }
    provenance = {
        "schema": "P5_ARMC_EXTENDED_FRAME_PROVENANCE_V0",
        "record_type": "armc_extended_seasonal_frame",
        "grain": "seasonal_frame_jja_2001_2025.csv",
        "n_rows": len(output),
        "base_frame_sha256": base_sha,
        "feature_contract_sha256": _sha(Path(feature_contract_path)),
        "feature_units": feature_units,
        "declared_extended_features": extended_names,
        "retained_extended_features": retained,
        "collinearity_exclusions": exclusions,
        "missingness": missingness,
        "grid": {
            "resolution_deg": EXPECTED_GRID_DEG,
            "interpolation": False,
            "complete_cell_policy": contract["spatial_policy"],
            "cells_by_chunk": provenance_cells,
        },
        "aggregation": "hourly UTC -> daily validation -> anchor-box complete-cell mean -> JJA seasonal feature",
        "payload_mode": payload_mode,
        "source_composition": dict(
            sorted(Counter(str(c.get("source")) for c in provenance_cells.values()).items())
        ),
        "source_map_sha256": source_map_sha if payload_mode == "monthly_split_manifest" else None,
        "split_manifest_sha256": _sha(Path(split_manifest)) if payload_mode == "monthly_split_manifest" else None,
        "audit_report_sha256": (
            _sha(Path(payloads_dir).resolve() / "retrieval" / "armc_retrieval_integrity_v15.json")
            if payload_mode == "monthly_split_manifest"
            and (Path(payloads_dir).resolve() / "retrieval" / "armc_retrieval_integrity_v15.json").is_file()
            else None
        ),
        "claim_scope": "research_only_no_operational_authorization",
    }
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    frame_path = output_dir / "seasonal_frame_jja_2001_2025.csv"
    units_path = output_dir / "feature_units_v0.json"
    prov_path = output_dir / "seasonal_frame_provenance_v0.json"
    frame_bytes = output.to_csv(index=False).encode("utf-8")
    write_once_bytes(frame_path, frame_bytes)
    write_once_sidecar(frame_path)
    write_once_json(units_path, {"schema": "P5_FEATURE_UNITS_V0", "units": feature_units}, indent=2)
    write_once_sidecar(units_path)
    provenance["frame_sha256"] = _sha(frame_path)
    write_once_json(prov_path, provenance, indent=2)
    write_once_sidecar(prov_path)
    return {
        "frame": frame_path,
        "feature_units": units_path,
        "provenance": prov_path,
        "rows": len(output),
        "retained_extended_features": retained,
        "collinearity_exclusions": exclusions,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate Arm C NetCDF chunks and build the 75-row extended seasonal frame.")
    parser.add_argument("--amendment", required=True, type=Path)
    parser.add_argument("--base-frame", required=True, type=Path)
    parser.add_argument("--feature-contract", required=True, type=Path)
    parser.add_argument("--payload-root", type=Path)
    parser.add_argument("--payloads-dir", type=Path)
    parser.add_argument("--split-manifest", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.payload_root is None and not (args.payloads_dir and args.split_manifest):
            raise ValueError("payload-root or both payloads-dir and split-manifest are required")
        if args.payload_root is not None and (args.payloads_dir or args.split_manifest):
            raise ValueError("payload-root cannot be combined with payloads-dir/split-manifest")
        result = build_extended_frame(
            amendment_v10=args.amendment,
            base_frame=args.base_frame,
            feature_contract_path=args.feature_contract,
            output_dir=args.output_dir,
            payload_root=args.payload_root,
            payloads_dir=args.payloads_dir,
            split_manifest=args.split_manifest,
        )
    except (OSError, ValueError, ExistingEvidenceError) as exc:
        print(json.dumps({"status": "BLOCKED", "problems": [str(exc)]}, indent=2))
        return 2
    print(json.dumps({"status": "FRAME_BUILT", **{k: str(v) for k, v in result.items() if k in {"frame", "feature_units", "provenance"}}, "rows": result["rows"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
