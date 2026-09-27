#!/usr/bin/env python3
"""Arm C CDS request planner and metadata-only gate.

This module is deliberately separate from the frozen P5 release scripts.  It
does not change an existing evidence root and it never treats a catalogue
response as payload authorization.  The only payload path in this module is
behind an explicit, independently recorded metadata gate and is not used by
the metadata-only command.

The request contract is assembled from amendment v8 (scientific scope), v9
(grid/provenance and conditional authorization), and v10 (anchor-box spatial
correction).  The live CDS retrieve-process document is persisted verbatim by
``metadata_gate`` and its version is bound into the report.
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import re
import shutil
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from nepal.era5_anchor_intake import ANCHOR_BOXES  # noqa: E402
from p5_safe_io import (  # noqa: E402
    ExistingEvidenceError,
    sha256_bytes,
    write_once_bytes,
    write_once_json,
    write_once_sidecar,
)


V8_SCHEMA = "P5_AMENDMENT_V8_ARM_C_SCOPE"
V9_SCHEMA = "P5_AMENDMENT_V9_ARM_C_GRID_PROVENANCE"
V10_SCHEMA = "P5_AMENDMENT_V10_ARM_C_SPATIAL_CORRECTION"
PROCESS_URL = (
    "https://cds.climate.copernicus.eu/api/retrieve/v1/processes/"
    "reanalysis-era5-pressure-levels"
)
EXPECTED_PROCESS_ID = "reanalysis-era5-pressure-levels"
EXPECTED_GRID_DEG = 0.25
EXPECTED_LEVELS = (500, 700)
EXPECTED_VARIABLES = (
    "geopotential",
    "specific_humidity",
    "temperature",
    "vertical_velocity",
)
EXPECTED_YEARS = tuple(range(2001, 2026))
JJA_MONTHS = (6, 7, 8)
HOURS = tuple(f"{hour:02d}:00" for hour in range(24))
MAX_SCOPE_BYTES = 5 * 1024**3
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return raw


def _sibling(amendment: Path, name: str) -> Path:
    candidate = amendment.parent / name
    if not candidate.exists():
        raise FileNotFoundError(f"required amendment sibling is missing: {candidate}")
    return candidate


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _aligned(value: float, grid: float = EXPECTED_GRID_DEG) -> bool:
    return abs(value / grid - round(value / grid)) <= 1e-9


def _area(value: Any, label: str) -> tuple[float, float, float, float]:
    if not isinstance(value, list) or len(value) != 4 or not all(
        _is_number(v) for v in value
    ):
        raise ValueError(f"{label} must be [north, west, south, east]")
    north, west, south, east = (float(v) for v in value)
    if not (-90 <= south < north <= 90 and -180 <= west < east <= 360):
        raise ValueError(f"{label} has invalid bounds: {value!r}")
    return north, west, south, east


def _anchor_area(name: str) -> tuple[float, float, float, float]:
    box = ANCHOR_BOXES[name]
    return (
        float(box["lat"][1]),
        float(box["lon"][0]),
        float(box["lat"][0]),
        float(box["lon"][1]),
    )


def _grid_axis(lo: float, hi: float, grid: float = EXPECTED_GRID_DEG) -> list[float]:
    """Return the complete grid-center axis for an aligned inclusive envelope."""
    count = int(round((hi - lo) / grid))
    return [round(lo + index * grid, 8) for index in range(count + 1)]


def expected_grid(area: tuple[float, float, float, float]) -> dict[str, list[float]]:
    north, west, south, east = area
    if not all(_aligned(v) for v in area):
        raise ValueError("retrieval area is not aligned to the declared 0.25-degree grid")
    return {
        "latitude": list(reversed(_grid_axis(south, north))),
        "longitude": _grid_axis(west, east),
        "selection_semantics": (
            "complete 0.25-degree grid cells whose centers are returned by the "
            "CDS area selection; no interpolation or regridding"
        ),
    }


def _variable_specs(v8: Mapping[str, Any]) -> list[dict[str, Any]]:
    contract = v8.get("cds_request_contract")
    if not isinstance(contract, Mapping):
        raise ValueError("v8 cds_request_contract is missing")
    raw = contract.get("variables")
    if not isinstance(raw, list) or not raw:
        raise ValueError("v8 variable contract is missing")
    specs: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError("v8 variable entries must be objects")
        name = item.get("name")
        levels = item.get("level_hpa")
        if name not in EXPECTED_VARIABLES or item.get("cds_param") != name:
            raise ValueError(f"undeclared Arm C variable: {name!r}")
        if levels != list(EXPECTED_LEVELS):
            raise ValueError(f"{name} must bind levels {list(EXPECTED_LEVELS)}")
        if not isinstance(item.get("units"), str) or not item["units"].strip():
            raise ValueError(f"{name} is missing units")
        specs.append({
            "name": name,
            "cds_param": name,
            "units": item["units"],
            "derived_diagnostic": item.get("derived_diagnostic", ""),
        })
    if tuple(item["name"] for item in specs) != EXPECTED_VARIABLES:
        raise ValueError("v8 variables must be the frozen four in declared order")
    return specs


def load_contract(
    v10_path: Path,
    *,
    v9_path: Path | None = None,
    v8_path: Path | None = None,
) -> dict[str, Any]:
    """Load and reconcile v8/v9/v10 without modifying any input bytes."""
    v10_path = Path(v10_path)
    v9_path = v9_path or _sibling(v10_path, "p5_amendment_v9_arm_c_grid_provenance.json")
    v8_path = v8_path or _sibling(v10_path, "p5_amendment_v8_arm_c_scope.json")
    v8, v9, v10 = _read_json(v8_path), _read_json(v9_path), _read_json(v10_path)
    if v8.get("schema") != V8_SCHEMA:
        raise ValueError("wrong v8 amendment schema")
    if v9.get("schema") != V9_SCHEMA:
        raise ValueError("wrong v9 amendment schema")
    if v10.get("schema") != V10_SCHEMA:
        raise ValueError("wrong v10 amendment schema")
    for label, record in (("v8", v8), ("v9", v9), ("v10", v10)):
        if not isinstance(record.get("approved_by"), str) or not record["approved_by"].strip():
            raise ValueError(f"{label} has no explicit owner decision")
        if record.get("claim_scope") != "research_only_no_operational_authorization":
            raise ValueError(f"{label} exceeds research-only scope")
    v9_auth = v9.get("retrieval_authorization")
    if not isinstance(v9_auth, Mapping) or v9_auth.get("state") != "AUTHORIZED_CONDITIONAL":
        raise ValueError("v9 conditional retrieval authorization is absent")
    v8_contract = v8.get("cds_request_contract")
    if not isinstance(v8_contract, Mapping):
        raise ValueError("v8 request contract is absent")
    if v8_contract.get("dataset") != EXPECTED_PROCESS_ID:
        raise ValueError("v8 dataset does not match the live CDS process")
    if v8_contract.get("format") != "netcdf":
        raise ValueError("v8 must explicitly request NetCDF")
    if int(v8_contract.get("max_download_bytes", -1)) != MAX_SCOPE_BYTES:
        raise ValueError("v8 5 GiB cap is not bound")
    if v9.get("additions_to_v8_contract", {}).get("grid_resolution") != (
        "0.25 x 0.25 degree — CDS native ERA5 pressure-level grid; "
        "no regridding, no interpolation before aggregation"
    ):
        raise ValueError("v9 grid provenance wording is not the frozen contract")
    if v10.get("revised_spatial_contract", {}).get("aggregation", "").find(
        "anchor-box area mean"
    ) < 0:
        raise ValueError("v10 does not bind anchor-box aggregation")
    areas_raw = v10.get("revised_spatial_contract", {}).get("retrieval_areas")
    if not isinstance(areas_raw, Mapping):
        raise ValueError("v10 retrieval_areas are absent")
    areas: dict[str, tuple[float, float, float, float]] = {}
    for basin in ("gandaki", "karnali", "koshi"):
        entry = areas_raw.get(basin)
        if not isinstance(entry, Mapping):
            raise ValueError(f"v10 retrieval area missing for {basin}")
        area = _area(entry.get("bbox_nwse"), f"v10 retrieval_areas.{basin}")
        if not all(_aligned(v) for v in area):
            raise ValueError(f"v10 retrieval area for {basin} is not 0.25 aligned")
        anchor = _anchor_area(basin)
        if not (area[0] >= anchor[0] and area[1] <= anchor[1]
                and area[2] <= anchor[2] and area[3] >= anchor[3]):
            raise ValueError(f"v10 retrieval area does not contain {basin} anchor")
        areas[basin] = area
    temporal = str(v8_contract.get("temporal_range", ""))
    if temporal != "2001-06-01..2025-08-31 (JJA only)":
        raise ValueError("v8 temporal range is not the frozen JJA 2001-2025 range")
    return {
        "schema": "P5_ARMC_REQUEST_CONTRACT_V0",
        "dataset": EXPECTED_PROCESS_ID,
        "process_url": PROCESS_URL,
        "process_contract_expected": {
            "id": EXPECTED_PROCESS_ID,
            "version": "1.0.0",
            "data_format": "netcdf",
            "download_format": "unarchived",
        },
        "source_amendments": {
            "v8": str(v8_path),
            "v9": str(v9_path),
            "v10": str(v10_path),
        },
        "variables": _variable_specs(v8),
        "levels_hpa": list(EXPECTED_LEVELS),
        "years": list(EXPECTED_YEARS),
        "months": list(JJA_MONTHS),
        "grid_resolution_deg": EXPECTED_GRID_DEG,
        "retrieval_areas": {k: list(v) for k, v in sorted(areas.items())},
        "anchor_boxes": {k: list(_anchor_area(k)) for k in sorted(ANCHOR_BOXES)},
        "max_download_bytes": MAX_SCOPE_BYTES,
        "max_new_seasonal_features": int(v8_contract["max_new_seasonal_features"]),
        "spatial_policy": (
            "retrieve the smallest aligned envelope; aggregate only complete "
            "grid-cell centers inside the frozen operative anchor box"
        ),
        "claim_scope": "research_only_no_operational_authorization",
    }


def _days(year: int, month: int) -> list[str]:
    return [f"{day:02d}" for day in range(1, calendar.monthrange(year, month)[1] + 1)]


def build_request_plan(contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Build deterministic calendar-valid monthly CDS request chunks."""
    plan: list[dict[str, Any]] = []
    variables = contract["variables"]
    areas = contract["retrieval_areas"]
    for basin in sorted(areas):
        area = [float(v) for v in areas[basin]]
        grid = expected_grid(tuple(area))
        for variable in variables:
            for level in contract["levels_hpa"]:
                for year in contract["years"]:
                    for month in contract["months"]:
                        chunk_id = (
                            f"{basin}-{variable['name']}-{level}-"
                            f"{year}-{month:02d}"
                        )
                        request = {
                            "product_type": ["reanalysis"],
                            "variable": [variable["cds_param"]],
                            "year": [str(year)],
                            "month": [f"{month:02d}"],
                            "day": _days(year, month),
                            "time": list(HOURS),
                            "pressure_level": [str(level)],
                            "area": area,
                            "data_format": "netcdf",
                            "download_format": "unarchived",
                        }
                        cell_count = len(grid["latitude"]) * len(grid["longitude"])
                        value_count = len(request["day"]) * len(HOURS) * cell_count
                        estimate = int((value_count * 8 * 1.20) + 65536)
                        plan.append({
                            "chunk_id": chunk_id,
                            "basin": basin,
                            "variable": variable["name"],
                            "pressure_level_hpa": int(level),
                            "year": int(year),
                            "month": int(month),
                            "area_nwse": area,
                            "expected_grid": grid,
                            "expected_complete_cells": cell_count,
                            "estimated_bytes": estimate,
                            "request": request,
                        })
    return plan


def estimate_plan(plan: list[Mapping[str, Any]], cap: int = MAX_SCOPE_BYTES) -> dict[str, Any]:
    estimated = sum(int(item["estimated_bytes"]) for item in plan)
    return {
        "request_count": len(plan),
        "estimated_bytes": estimated,
        "max_download_bytes": cap,
        "fits_cap": estimated <= cap,
        "basis": (
            "float64 values plus 20 percent encoding/header allowance per "
            "calendar-valid monthly chunk; actual bytes must be reconciled "
            "after retrieval"
        ),
    }


def _fetch_bytes(url: str, opener: Callable[..., Any] | None = None) -> bytes:
    opener = opener or urllib.request.urlopen
    try:
        with opener(url, timeout=30) as response:
            return response.read()
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"CDS metadata request failed: {exc}") from exc


def _validate_process_metadata(
    raw: bytes, contract: Mapping[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    problems: list[str] = []
    try:
        metadata = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"status": "UNREADABLE"}, [f"CDS process metadata is not JSON: {exc}"]
    if not isinstance(metadata, Mapping):
        return {"status": "INVALID"}, ["CDS process metadata must be an object"]
    checks: dict[str, bool] = {
        "id": metadata.get("id") == EXPECTED_PROCESS_ID,
        "version_present": isinstance(metadata.get("version"), str)
        and bool(metadata.get("version")),
    }
    inputs = metadata.get("inputs")
    if not isinstance(inputs, Mapping):
        problems.append("CDS process metadata lacks inputs")
        inputs = {}
    def enum(name: str) -> set[str]:
        schema = inputs.get(name, {}).get("schema", {}) if isinstance(inputs.get(name), Mapping) else {}
        items = schema.get("items", {}) if isinstance(schema, Mapping) else {}
        values = items.get("enum", []) if isinstance(items, Mapping) else []
        return {str(v) for v in values} if isinstance(values, list) else set()
    checks["variables"] = set(v["name"] for v in contract["variables"]).issubset(enum("variable"))
    checks["levels"] = set(str(v) for v in contract["levels_hpa"]).issubset(enum("pressure_level"))
    checks["years"] = set(str(v) for v in contract["years"]).issubset(enum("year"))
    checks["months"] = set(str(v).zfill(2) for v in contract["months"]).issubset(enum("month"))
    checks["hours"] = set(HOURS).issubset(enum("time"))
    area_schema = inputs.get("area", {}).get("schema", {}) if isinstance(inputs.get("area"), Mapping) else {}
    checks["area"] = isinstance(area_schema, Mapping) and area_schema.get("minItems") == 4
    data_schema = inputs.get("data_format", {}).get("schema", {}) if isinstance(inputs.get("data_format"), Mapping) else {}
    data_enum = data_schema.get("enum", []) if isinstance(data_schema, Mapping) else []
    checks["netcdf"] = isinstance(data_enum, list) and "netcdf" in data_enum
    download_schema = inputs.get("download_format", {}).get("schema", {}) if isinstance(inputs.get("download_format"), Mapping) else {}
    download_enum = download_schema.get("enum", []) if isinstance(download_schema, Mapping) else []
    checks["unarchived"] = isinstance(download_enum, list) and "unarchived" in download_enum
    for name, passed in checks.items():
        if not passed:
            problems.append(f"CDS process contract check failed: {name}")
    return {
        "status": "METADATA_OK" if not problems else "METADATA_MISMATCH",
        "process_id": metadata.get("id"),
        "process_version": metadata.get("version"),
        "metadata_sha256": sha256_bytes(raw),
        "checks": checks,
        "extent": metadata.get("extent"),
        "provider": metadata.get("provider"),
        "published": metadata.get("published"),
        "updated": metadata.get("updated"),
    }, problems


def _cdsapirc_status() -> dict[str, Any]:
    path = Path.home() / ".cdsapirc"
    result: dict[str, Any] = {"path_present": path.is_file(), "url_present": False, "key_present": False}
    if not path.is_file():
        return result
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return result
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        result[f"{key.strip()}_present"] = bool(value.strip())
    return result


def metadata_gate(
    contract: Mapping[str, Any],
    plan: list[Mapping[str, Any]],
    *,
    metadata_bytes: bytes,
    license_record: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    metadata, problems = _validate_process_metadata(metadata_bytes, contract)
    estimate = estimate_plan(plan, int(contract["max_download_bytes"]))
    if not estimate["fits_cap"]:
        problems.append("request-size estimate exceeds the 5 GiB cap")
    auth = _cdsapirc_status()
    if not auth["path_present"] or not auth["url_present"] or not auth["key_present"]:
        problems.append("CDS authentication configuration is not fully present")
    license_verified = (
        isinstance(license_record, Mapping)
        and license_record.get("accepted") is True
        and isinstance(license_record.get("recorded_utc"), str)
        and isinstance(license_record.get("dataset"), str)
        and license_record.get("dataset") == contract["dataset"]
    )
    if not license_verified:
        problems.append("manual CDS licence acceptance record is absent or unverified")
    return {
        "schema": "P5_ARMC_METADATA_GATE_V0",
        "status": "METADATA_OK" if not problems else "BLOCKED",
        "retrieval_gate": "PAYLOAD_AUTHORIZED" if not problems else "PAYLOAD_BLOCKED",
        "claim_scope": "metadata_only_no_payload_no_operational_authorization",
        "contract": {
            "dataset": contract["dataset"],
            "process_url": contract["process_url"],
            "expected_process_contract": contract["process_contract_expected"],
            "grid_resolution_deg": contract["grid_resolution_deg"],
            "spatial_policy": contract["spatial_policy"],
        },
        "process_metadata": metadata,
        "request_estimate": estimate,
        "authentication": {"config_present": auth["path_present"], "url_present": auth["url_present"], "key_present": auth["key_present"]},
        "license_acceptance": {"verified": license_verified, "recorded": bool(license_record)},
        "acquisition": {
            "network_calls": 1,
            "payload_bytes_retrieved": 0,
            "writes_performed": False,
            "payload_acquisition": "NOT_PERFORMED",
        },
        "problems": problems,
    }


def persist_metadata_gate(
    root: Path,
    contract: Mapping[str, Any],
    plan: list[Mapping[str, Any]],
    metadata_bytes: bytes,
    report: Mapping[str, Any],
) -> list[Path]:
    """Write only new metadata evidence into the supplied exclusive root."""
    root = Path(root).resolve()
    retrieval = root / "retrieval"
    retrieval.mkdir(parents=True, exist_ok=True)
    outputs = [
        retrieval / "armc_request_plan_v0.json",
        retrieval / "cds_process_metadata.json",
        retrieval / "armc_metadata_gate_v0.json",
    ]
    if any(path.exists() or Path(str(path) + ".sha256").exists() for path in outputs):
        raise ExistingEvidenceError("Arm C metadata root already contains governed outputs")
    request_doc = {
        "schema": "P5_ARMC_REQUEST_PLAN_V0",
        "created_utc": _utc_now(),
        "contract": dict(contract),
        "request_count": len(plan),
        "requests": list(plan),
        "plan_sha256": sha256_bytes(_canonical_json({"requests": list(plan)})),
        "claim_scope": "research_only_no_operational_authorization",
    }
    written: list[Path] = []
    for path, payload in ((outputs[0], request_doc), (outputs[2], dict(report))):
        write_once_json(path, payload, indent=2)
        write_once_sidecar(path)
        written.append(path)
    write_once_bytes(outputs[1], metadata_bytes)
    write_once_sidecar(outputs[1])
    written.append(outputs[1])
    return written


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def _safe_chunk_id(chunk_id: str) -> str:
    if not isinstance(chunk_id, str) or not re.fullmatch(
        r"[a-z0-9_]+(?:-[a-z0-9_]+)*", chunk_id
    ):
        raise ValueError(f"unsafe chunk id: {chunk_id!r}")
    return chunk_id


def retrieve_plan(
    contract: Mapping[str, Any],
    plan: list[Mapping[str, Any]],
    evidence_root: Path,
    *,
    client: Any | None = None,
    client_factory: Callable[[], Any] | None = None,
    max_bytes: int | None = None,
) -> dict[str, Any]:
    """Execute a fully planned retrieval into one new exclusive root.

    This is coordinator-owned and is intentionally not called by the local
    test/audit workflow.  ``client``/``client_factory`` are injection points
    so tests can prove the write and stop rules without importing or contacting
    CDS.  A real caller must provide an explicit ``--retrieve`` decision.
    """
    root = Path(evidence_root).resolve()
    if root.exists():
        raise ExistingEvidenceError(
            f"refusing non-exclusive Arm C evidence root: {root}")
    cap = int(max_bytes if max_bytes is not None else contract["max_download_bytes"])
    if cap <= 0:
        raise ValueError("retrieval byte cap must be positive")
    root.mkdir(parents=True, exist_ok=False)
    retrieval = root / "retrieval"
    requests_dir = retrieval / "requests"
    payloads_dir = retrieval / "payloads"
    requests_dir.mkdir(parents=True, exist_ok=False)
    payloads_dir.mkdir(parents=True, exist_ok=False)

    plan_doc = {
        "schema": "P5_ARMC_REQUEST_PLAN_V0",
        "created_utc": _utc_now(),
        "contract": dict(contract),
        "request_count": len(plan),
        "requests": list(plan),
        "plan_sha256": sha256_bytes(_canonical_json({"requests": list(plan)})),
        "claim_scope": "research_only_no_operational_authorization",
    }
    plan_path = retrieval / "armc_request_plan_v0.json"
    write_once_json(plan_path, plan_doc, indent=2)
    write_once_sidecar(plan_path)

    if client is None:
        if client_factory is None:
            def _default_client() -> Any:
                # Lazy import: dry-run and tests can never accidentally load
                # the network client.
                import cdsapi  # type: ignore
                return cdsapi.Client(quiet=True)

            client_factory = _default_client
        client = client_factory()

    records: list[dict[str, Any]] = []
    total_bytes = 0
    started = _utc_now()
    quarantined_payload: str | None = None
    try:
        for item in plan:
            chunk_id = _safe_chunk_id(str(item.get("chunk_id")))
            request = item.get("request")
            if not isinstance(request, Mapping):
                raise ValueError(f"{chunk_id}: request must be an object")
            request_doc = {
                "schema": "P5_ARMC_REQUEST_V0",
                "chunk_id": chunk_id,
                "dataset": contract["dataset"],
                "request": dict(request),
                "request_sha256": sha256_bytes(_canonical_json(request)),
            }
            request_path = requests_dir / f"{chunk_id}.json"
            write_once_json(request_path, request_doc, indent=2)
            write_once_sidecar(request_path)

            target = payloads_dir / f"{chunk_id}.nc"
            # The target is inside the exclusive root and constructed from a
            # validated chunk id; no caller-supplied path can escape it.
            client.retrieve(contract["dataset"], dict(request), str(target))
            if not target.is_file() or target.stat().st_size <= 0:
                raise ValueError(f"{chunk_id}: CDS returned no non-empty payload")
            size = int(target.stat().st_size)
            total_bytes += size
            if total_bytes > cap:
                quarantine_dir = retrieval / "quarantine"
                quarantine_dir.mkdir(parents=True, exist_ok=True)
                quarantine_target = quarantine_dir / target.name
                shutil.move(str(target), str(quarantine_target))
                quarantined_payload = "retrieval/quarantine/" + target.name
                raise ValueError(
                    f"{chunk_id}: actual payload bytes {total_bytes} exceed cap {cap}"
                )
            response_sha = write_once_sidecar(target)
            record = {
                "schema": "P5_ARMC_CHUNK_RECEIPT_V0",
                "chunk_id": chunk_id,
                "request_relpath": f"retrieval/requests/{chunk_id}.json",
                "payload_relpath": f"retrieval/payloads/{chunk_id}.nc",
                "request_sha256": request_doc["request_sha256"],
                "response_sha256": response_sha,
                "response_bytes": size,
                "status": "PAYLOAD_RECEIVED_UNPARSED",
            }
            receipt_path = retrieval / "chunks" / f"{chunk_id}.json"
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            write_once_json(receipt_path, record, indent=2)
            write_once_sidecar(receipt_path)
            records.append(record)
    except BaseException as exc:
        stop = {
            "schema": "P5_ARMC_RETRIEVAL_STOP_V0",
            "status": "BLOCKED",
            "started_utc": started,
            "completed_utc": _utc_now(),
            "completed_chunks": len(records),
            "actual_payload_bytes": total_bytes,
            "max_download_bytes": cap,
            "quarantined_payload_relpath": quarantined_payload,
            "reason": str(exc),
            "claim_scope": "research_only_no_operational_authorization",
        }
        stop_path = retrieval / "armc_retrieval_stop_v0.json"
        if not stop_path.exists():
            write_once_json(stop_path, stop, indent=2)
            write_once_sidecar(stop_path)
        raise

    manifest = {
        "schema": "P5_ARMC_RETRIEVAL_MANIFEST_V0",
        "status": "RETRIEVAL_RECEIVED_PENDING_NETCDF_VALIDATION",
        "started_utc": started,
        "completed_utc": _utc_now(),
        "request_count": len(plan),
        "completed_chunks": len(records),
        "actual_payload_bytes": total_bytes,
        "max_download_bytes": cap,
        "request_plan_sha256": plan_doc["plan_sha256"],
        "chunks": records,
        "claim_scope": "research_only_no_operational_authorization",
    }
    manifest_path = retrieval / "armc_retrieval_manifest_v0.json"
    write_once_json(manifest_path, manifest, indent=2)
    write_once_sidecar(manifest_path)
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan Arm C CDS retrieval and run a metadata-only gate.")
    parser.add_argument("--amendment", required=True, type=Path, help="v10 spatial-correction amendment")
    parser.add_argument("--v8", type=Path)
    parser.add_argument("--v9", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="print the deterministic request plan; no network or writes")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--metadata-only", action="store_true", help="fetch the public CDS process document and persist only metadata evidence")
    mode.add_argument("--retrieve", action="store_true", help="coordinator-only payload retrieval after a passing metadata report")
    parser.add_argument("--evidence-root", type=Path, help="new exclusive Arm C evidence root for --metadata-only")
    parser.add_argument("--license-record", type=Path, help="owner-recorded manual licence acceptance JSON; never inferred")
    parser.add_argument("--metadata-report", type=Path, help="persisted P5_ARMC_METADATA_GATE_V0 required for --retrieve")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        contract = load_contract(args.amendment, v8_path=args.v8, v9_path=args.v9)
        plan = build_request_plan(contract)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "BLOCKED", "problems": [str(exc)], "acquisition": {"network_calls": 0, "payload_bytes_retrieved": 0, "writes_performed": False}}, indent=2))
        return 2
    if args.dry_run or (not args.metadata_only and not args.retrieve):
        result = {"status": "DRY_RUN", "contract": contract, "request_estimate": estimate_plan(plan, contract["max_download_bytes"]), "requests": plan}
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.evidence_root is None:
        print(json.dumps({"status": "BLOCKED", "problems": ["--evidence-root is required for metadata evidence publication"]}, indent=2))
        return 2
    try:
        if args.retrieve:
            if args.metadata_report is None:
                raise ValueError("--metadata-report is required before payload retrieval")
            report = _read_json(args.metadata_report)
            if report.get("schema") != "P5_ARMC_METADATA_GATE_V0":
                raise ValueError("metadata report schema is not admitted")
            if report.get("status") != "METADATA_OK" or report.get("retrieval_gate") != "PAYLOAD_AUTHORIZED":
                raise ValueError("metadata report does not authorize payload retrieval")
            result = retrieve_plan(contract, plan, Path(args.evidence_root))
            print(json.dumps({"status": result["status"], "completed_chunks": result["completed_chunks"], "actual_payload_bytes": result["actual_payload_bytes"]}, indent=2))
            return 0
        raw = _fetch_bytes(contract["process_url"])
        license_record = _read_json(args.license_record) if args.license_record else None
        report = metadata_gate(contract, plan, metadata_bytes=raw, license_record=license_record)
        paths = persist_metadata_gate(Path(args.evidence_root), contract, plan, raw, report)
        report = dict(report)
        report["evidence_paths"] = [str(path.relative_to(Path(args.evidence_root).resolve())) for path in paths]
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["status"] == "METADATA_OK" else 2
    except (OSError, RuntimeError, ValueError, ExistingEvidenceError) as exc:
        print(json.dumps({"status": "BLOCKED", "problems": [str(exc)], "acquisition": {"network_calls": 0, "payload_bytes_retrieved": 0, "writes_performed": False}}, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
