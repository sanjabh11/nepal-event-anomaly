#!/usr/bin/env python3
"""Run a metadata-only preflight for the frozen Arm C CDS scope.

The tool validates the owner-approved v8 request contract and, when supplied,
an offline catalogue-metadata fixture.  It estimates the request envelope but
never calls ``cdsapi``, downloads payload bytes, or writes an evidence record.
The ``--write`` option is accepted only to fail closed permanently so an
operator cannot accidentally turn this dry-run into acquisition.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, Mapping


AMENDMENT_SCHEMA = "P5_AMENDMENT_V8_ARM_C_SCOPE"
EXPECTED_DATASET = "reanalysis-era5-pressure-levels"
EXPECTED_FORMAT = "netcdf"
EXPECTED_VARIABLES = {
    "geopotential",
    "specific_humidity",
    "temperature",
    "vertical_velocity",
}
EXPECTED_LEVELS = (500, 700)
EXPECTED_BBOX_NWSE = (31.0, 80.0, 26.0, 89.0)
EXPECTED_START = date(2001, 6, 1)
EXPECTED_END = date(2025, 8, 31)
MAX_SCOPE_BYTES = 5 * 1024**3
DEFAULT_GRID_DEG = 0.25
DATE_RANGE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})(?: \(JJA only\))?$")


def _mapping(value: Any, label: str, problems: list[str]) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        problems.append(f"{label} must be an object")
        return None
    return value


def _parse_date(value: Any, label: str, problems: list[str]) -> date | None:
    if not isinstance(value, str):
        problems.append(f"{label} must be YYYY-MM-DD")
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        problems.append(f"{label} is not a calendar date")
        return None


def _parse_range(value: Any, problems: list[str]) -> tuple[date, date] | None:
    if not isinstance(value, str):
        problems.append("cds_request_contract.temporal_range must be a date range")
        return None
    match = DATE_RANGE_RE.fullmatch(value)
    if not match:
        problems.append("temporal_range must use YYYY-MM-DD..YYYY-MM-DD")
        return None
    start = _parse_date(match.group(1), "temporal_range.start", problems)
    end = _parse_date(match.group(2), "temporal_range.end", problems)
    if start and end and end < start:
        problems.append("temporal_range end precedes start")
    return (start, end) if start and end else None


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _validate_variables(contract: Mapping[str, Any], problems: list[str]) -> tuple[list[str], list[int]]:
    raw = contract.get("variables")
    if not isinstance(raw, list) or not raw:
        problems.append("cds_request_contract.variables must be a non-empty list")
        return [], []
    names: list[str] = []
    levels: set[int] = set()
    for index, item in enumerate(raw):
        label = f"cds_request_contract.variables[{index}]"
        variable = _mapping(item, label, problems)
        if variable is None:
            continue
        name = variable.get("name")
        if not isinstance(name, str) or not name.strip():
            problems.append(f"{label}.name must be non-empty")
            continue
        if name in names:
            problems.append(f"duplicate CDS variable {name!r}")
        names.append(name)
        if variable.get("cds_param") != name:
            problems.append(f"{label}.cds_param must equal its declared name")
        raw_levels = variable.get("level_hpa")
        if not isinstance(raw_levels, list) or not raw_levels:
            problems.append(f"{label}.level_hpa must be a non-empty list")
            continue
        parsed_levels: list[int] = []
        for level in raw_levels:
            if not isinstance(level, int) or isinstance(level, bool):
                problems.append(f"{label}.level_hpa values must be integers")
                continue
            parsed_levels.append(level)
            levels.add(level)
        if tuple(parsed_levels) != EXPECTED_LEVELS:
            problems.append(
                f"{label}.level_hpa must be exactly {list(EXPECTED_LEVELS)}"
            )
        if not isinstance(variable.get("units"), str) or not variable["units"].strip():
            problems.append(f"{label}.units must be non-empty")
    if set(names) != EXPECTED_VARIABLES:
        problems.append("declared variables must be exactly the frozen four diagnostics")
    return names, sorted(levels)


def _validate_contract(amendment: Mapping[str, Any]) -> tuple[list[str], dict[str, Any]]:
    problems: list[str] = []
    contract = _mapping(amendment.get("cds_request_contract"),
                        "cds_request_contract", problems)
    if amendment.get("schema") != AMENDMENT_SCHEMA:
        problems.append(f"schema must be {AMENDMENT_SCHEMA!r}")
    if amendment.get("approval_state") != "SCOPE_APPROVED_RETRIEVAL_DEFERRED":
        problems.append("approval_state must be SCOPE_APPROVED_RETRIEVAL_DEFERRED")
    if not isinstance(amendment.get("approved_by"), str) or not amendment["approved_by"].strip():
        problems.append("approved_by must be an explicit non-empty owner decision")
    if amendment.get("claim_scope") != "research_only_no_operational_authorization":
        problems.append("claim_scope exceeds the research-only boundary")
    if contract is None:
        return problems, {}

    if contract.get("dataset") != EXPECTED_DATASET:
        problems.append(f"dataset must be {EXPECTED_DATASET!r}")
    if contract.get("format") != EXPECTED_FORMAT:
        problems.append(f"format must be {EXPECTED_FORMAT!r}")
    if contract.get("product_type") != "reanalysis":
        problems.append("product_type must be reanalysis")
    names, levels = _validate_variables(contract, problems)
    date_range = _parse_range(contract.get("temporal_range"), problems)
    if date_range:
        start, end = date_range
        if (start, end) != (EXPECTED_START, EXPECTED_END):
            problems.append("temporal_range differs from the frozen JJA 2001–2025 scope")
        if start.month != 6 or start.day != 1 or end.month != 8 or end.day != 31:
            problems.append("temporal_range must cover complete June–August seasons")
    if not isinstance(contract.get("temporal_aggregation"), str) or \
            "hourly CDS fields" not in contract["temporal_aggregation"] or \
            "daily means" not in contract["temporal_aggregation"]:
        problems.append("temporal_aggregation must retain hourly-to-daily basin seasonal semantics")

    bbox = contract.get("area_bbox_nwse")
    if not isinstance(bbox, list) or len(bbox) != 4 or not all(_finite_number(v) for v in bbox):
        problems.append("area_bbox_nwse must be four finite numbers")
        bbox_values = list(EXPECTED_BBOX_NWSE)
    else:
        bbox_values = [float(v) for v in bbox]
        north, west, south, east = bbox_values
        if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
            problems.append("area_bbox_nwse must be [north, west, south, east] in bounds")
        if tuple(bbox_values) != EXPECTED_BBOX_NWSE:
            problems.append("area_bbox_nwse differs from the frozen Nepal bbox")

    cap = contract.get("max_download_bytes")
    if not isinstance(cap, int) or isinstance(cap, bool) or cap <= 0:
        problems.append("max_download_bytes must be a positive integer")
        cap = MAX_SCOPE_BYTES
    elif cap > MAX_SCOPE_BYTES:
        problems.append("max_download_bytes exceeds the 5 GiB owner cap")
    feature_cap = contract.get("max_new_seasonal_features")
    if not isinstance(feature_cap, int) or isinstance(feature_cap, bool) or not 1 <= feature_cap <= 6:
        problems.append("max_new_seasonal_features must be an integer from 1 through 6")

    # The v8 record freezes the scientific scope but deliberately does not
    # freeze a grid resolution.  A planning estimate is still useful, but it
    # cannot authorize a retrieval until that omission is repaired.
    declared_grid = contract.get("grid_resolution_deg")
    grid_bound = _finite_number(declared_grid) and float(declared_grid) > 0
    if not grid_bound:
        problems.append(
            "grid_resolution_deg is not bound in v8; the estimate is planning-only "
            "and retrieval remains blocked"
        )
        grid_deg = DEFAULT_GRID_DEG
    else:
        grid_deg = float(declared_grid)

    years = 0
    if date_range:
        years = date_range[1].year - date_range[0].year + 1
    jja_days = 92 * years
    hourly_steps = jja_days * 24
    field_count = len(names) * len(levels)
    north, west, south, east = bbox_values
    lat_points = max(0, math.floor((north - south) / grid_deg + 1e-9) + 1)
    lon_points = max(0, math.floor((east - west) / grid_deg + 1e-9) + 1)
    grid_cells = lat_points * lon_points
    values = hourly_steps * field_count * grid_cells
    conservative_bytes = math.ceil(values * 8 * 1.10)
    estimate = {
        "grid_resolution_deg": grid_deg,
        "grid_resolution_bound": grid_bound,
        "years": years,
        "jja_days": jja_days,
        "hourly_steps": hourly_steps,
        "field_count": field_count,
        "latitude_points": lat_points,
        "longitude_points": lon_points,
        "grid_cells": grid_cells,
        "value_count": values,
        "bytes_per_value_assumption": 8,
        "metadata_overhead_factor": 1.10,
        "conservative_uncompressed_bytes": conservative_bytes,
        "max_download_bytes": cap,
        "fits_cap_under_estimate": conservative_bytes <= cap,
        "basis": "float64 uncompressed planning upper bound; actual NetCDF encoding not measured",
    }
    return problems, {
        "contract": contract,
        "variables": names,
        "levels_hpa": levels,
        "bbox_nwse": bbox_values,
        "estimate": estimate,
        "grid_resolution_bound": grid_bound,
    }


def _metadata_coverage(
    metadata: Mapping[str, Any] | None,
    contract_info: Mapping[str, Any],
    problems: list[str],
) -> dict[str, Any]:
    if metadata is None:
        return {
            "status": "NOT_PROVIDED",
            "network_call": False,
            "verified": False,
        }
    checks: dict[str, bool] = {}
    dataset = contract_info["contract"].get("dataset")
    checks["dataset"] = metadata.get("dataset") == dataset
    if not checks["dataset"]:
        problems.append("catalogue metadata dataset does not match the amendment")
    formats = metadata.get("formats", metadata.get("available_formats", []))
    checks["format"] = isinstance(formats, list) and EXPECTED_FORMAT in formats
    if not checks["format"]:
        problems.append("catalogue metadata does not advertise NetCDF")

    available = metadata.get("available_variables", metadata.get("variables"))
    if not isinstance(available, Mapping):
        problems.append("catalogue metadata must provide available_variables mapping")
        checks["variables_levels"] = False
    else:
        checks["variables_levels"] = True
        for name in contract_info["variables"]:
            raw_levels = available.get(name)
            if not isinstance(raw_levels, list) or not set(contract_info["levels_hpa"]).issubset(set(raw_levels)):
                checks["variables_levels"] = False
                problems.append(f"catalogue metadata lacks requested levels for {name}")

    requested_start = EXPECTED_START
    requested_end = EXPECTED_END
    available_start = _parse_date(metadata.get("available_from"), "metadata.available_from", problems)
    available_end = _parse_date(metadata.get("available_to"), "metadata.available_to", problems)
    checks["date_coverage"] = bool(
        available_start and available_end and
        available_start <= requested_start <= requested_end <= available_end
    )
    if not checks["date_coverage"]:
        problems.append("catalogue metadata does not cover the complete requested date range")

    metadata_bbox = metadata.get("area_bbox_nwse")
    if metadata_bbox is None:
        checks["spatial_coverage"] = True  # dataset metadata may declare global coverage
    elif isinstance(metadata_bbox, list) and len(metadata_bbox) == 4 and all(_finite_number(v) for v in metadata_bbox):
        mn, mw, ms, me = [float(v) for v in metadata_bbox]
        rn, rw, rs, re = contract_info["bbox_nwse"]
        checks["spatial_coverage"] = ms <= rs and mn >= rn and mw <= rw and me >= re
    else:
        checks["spatial_coverage"] = False
    if not checks["spatial_coverage"]:
        problems.append("catalogue metadata does not cover the requested bbox")

    declared_grid = contract_info["contract"].get("grid_resolution_deg")
    metadata_grid = metadata.get("grid_resolution_deg")
    if declared_grid is not None and metadata_grid is not None:
        checks["grid_resolution"] = (
            _finite_number(metadata_grid) and
            math.isclose(float(declared_grid), float(metadata_grid), rel_tol=0.0, abs_tol=1e-12)
        )
        if not checks["grid_resolution"]:
            problems.append("catalogue metadata grid resolution differs from the amendment")

    return {
        "status": "METADATA_OK" if all(checks.values()) else "METADATA_MISMATCH",
        "network_call": False,
        "verified": all(checks.values()),
        "checks": checks,
        "grid_resolution_deg": metadata.get("grid_resolution_deg"),
    }


def preflight(amendment: Mapping[str, Any], metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
    problems, info = _validate_contract(amendment)
    if info:
        coverage = _metadata_coverage(metadata, info, problems)
        estimate = info["estimate"]
    else:
        coverage = {"status": "NOT_RUN", "network_call": False, "verified": False}
        estimate = None
    if estimate is not None and not estimate["fits_cap_under_estimate"]:
        problems.append("conservative request-size estimate exceeds the 5 GiB cap")
    if metadata is None:
        problems.append("live/fixture catalogue metadata was not supplied; coverage is unverified")
    if estimate is not None and not info["grid_resolution_bound"]:
        # This is already listed as a structural problem; keep the terminal
        # state explicit even if an offline catalogue fixture is complete.
        pass
    status = "PREFLIGHT_OK" if not problems else "PREFLIGHT_BLOCKED"
    return {
        "status": status,
        "schema": "P5_ARMC_CDS_PREFLIGHT_V0",
        "amendment_schema": amendment.get("schema"),
        "coverage": coverage,
        "estimate": estimate,
        "problems": problems,
        "acquisition": {
            "network_calls": 0,
            "payload_bytes_retrieved": 0,
            "writes_performed": False,
            "payload_acquisition": "NOT_PERFORMED",
        },
        "claim_scope": "metadata_only_no_payload_no_operational_authorization",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate Arm C CDS scope without network or payload access.")
    parser.add_argument("--amendment", required=True, type=Path)
    parser.add_argument(
        "--metadata", type=Path,
        help="optional local catalogue-metadata JSON fixture; never fetched by this tool",
    )
    parser.add_argument(
        "--write", action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.write:
        print(json.dumps({
            "status": "WRITE_FORBIDDEN",
            "problems": ["Arm C preflight is permanently metadata-only; --write is refused"],
            "acquisition": {"network_calls": 0, "payload_bytes_retrieved": 0, "writes_performed": False},
        }, indent=2, sort_keys=True))
        return 2
    try:
        amendment = json.loads(args.amendment.read_text(encoding="utf-8"))
        metadata = None
        if args.metadata is not None:
            metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(json.dumps({
            "status": "PREFLIGHT_BLOCKED",
            "problems": [f"cannot read JSON input: {exc}"],
            "acquisition": {"network_calls": 0, "payload_bytes_retrieved": 0, "writes_performed": False},
        }, indent=2, sort_keys=True))
        return 2
    if not isinstance(amendment, Mapping):
        result = {
            "status": "PREFLIGHT_BLOCKED",
            "problems": ["amendment must be a JSON object"],
            "acquisition": {"network_calls": 0, "payload_bytes_retrieved": 0, "writes_performed": False},
        }
    else:
        result = preflight(amendment, metadata if isinstance(metadata, Mapping) else None)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "PREFLIGHT_OK" else 2


if __name__ == "__main__":
    raise SystemExit(main())
