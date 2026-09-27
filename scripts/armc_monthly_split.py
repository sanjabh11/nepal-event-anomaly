#!/usr/bin/env python3
"""Normalize and split the coordinator's Arm C three-month payloads.

The coordinator's live retrieval uses one payload per basin, variable, level,
and year.  The governed frame contract uses one logical payload per month.
This module is a local, deterministic bridge between those two shapes.  It
never contacts CDS and it refuses to publish any derived bytes until the full
raw inventory and every raw NetCDF file have passed validation.
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import subprocess
import sys
import tempfile
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

from armc_cds_retrieve import expected_grid  # noqa: E402
from p5_safe_io import (  # noqa: E402
    ExistingEvidenceError,
    sha256_bytes,
    write_once_bytes,
    write_once_json,
    write_once_sidecar,
)


SCHEMA = "P5_ARMC_MONTHLY_SPLIT_MANIFEST_V0"
PLAN_SCHEMA = "P5_ARMC_REQUEST_PLAN_V0"
REQUEST_SCHEMA = "P5_ARMC_REQUEST_V0"
RECEIPT_SCHEMA = "P5_ARMC_CHUNK_RECEIPT_V0"
CLAIM_SCOPE = "research_only_no_operational_authorization"
EXPECTED_LANE_NAMES = (
    "payload-geopotential",
    "payload-specific_humidity",
    "payload-temperature",
    "payload-vertical_velocity",
)
SOURCE_TO_CANONICAL = {
    "z": "geopotential",
    "q": "specific_humidity",
    "t": "temperature",
    "w": "vertical_velocity",
}
CANONICAL_TO_SOURCE = {value: key for key, value in SOURCE_TO_CANONICAL.items()}
EXPECTED_COORDS = {
    "number",
    "valid_time",
    "pressure_level",
    "latitude",
    "longitude",
    "expver",
}
JJA_MONTHS = (6, 7, 8)
EXPECTED_RAW_CHUNKS = 600
EXPECTED_MONTHLY_CHUNKS = 1800
SPLITTER_VERSION = "armc_monthly_split_v0"


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _verify_sidecar(path: Path) -> str:
    if not path.is_file():
        raise ValueError(f"missing file: {path}")
    sidecar = Path(str(path) + ".sha256")
    if not sidecar.is_file():
        raise ValueError(f"missing sidecar for {path}")
    digest = _sha(path)
    fields = sidecar.read_text(encoding="utf-8").split()
    if not fields or fields[0] != digest:
        raise ValueError(f"sidecar mismatch for {path}")
    return digest


def _repo_head() -> str:
    result = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise ValueError("cannot resolve repository HEAD")
    return result.stdout.strip()


def expected_times(year: int, month: int) -> pd.DatetimeIndex:
    start = pd.Timestamp(year=year, month=month, day=1, tz="UTC")
    end = start + pd.offsets.MonthEnd(1) + pd.Timedelta(hours=23)
    return pd.date_range(start, end, freq="h")


def expected_jja_times(year: int) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(
        np.concatenate([expected_times(year, month).values for month in JJA_MONTHS])
    ).tz_localize("UTC")


def _utc_index(values: Any) -> pd.DatetimeIndex:
    try:
        index = pd.DatetimeIndex(values)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"valid_time is not datetime-like: {exc}") from exc
    if index.tz is None:
        return index.tz_localize("UTC")
    return index.tz_convert("UTC")


def _scalar_coord(ds: xr.Dataset, name: str) -> Any:
    if name not in ds.coords:
        raise ValueError(f"payload is missing coordinate {name!r}")
    values = np.asarray(ds.coords[name].values)
    uniq = np.unique(values)
    if uniq.size != 1:
        raise ValueError(f"payload has mixed {name} values")
    return uniq.reshape(-1)[0].item()


def _validate_plan_request(item: Mapping[str, Any], *, allow_v12_day_list: bool = False) -> None:
    request = item.get("request")
    if not isinstance(request, Mapping):
        raise ValueError(f"{item.get('chunk_id')}: missing request object")
    chunk_id = str(item.get("chunk_id"))
    variable = str(item.get("variable"))
    level = int(item.get("pressure_level_hpa"))
    year = int(item.get("year"))
    if request.get("variable") != [variable]:
        raise ValueError(f"{chunk_id}: request variable does not match plan")
    if request.get("pressure_level") != [str(level)]:
        raise ValueError(f"{chunk_id}: request pressure level does not match plan")
    if request.get("year") != [str(year)]:
        raise ValueError(f"{chunk_id}: request year does not match plan")
    if request.get("month") != ["06", "07", "08"]:
        raise ValueError(f"{chunk_id}: request must cover exactly JJA")
    if request.get("time") != [f"{hour:02d}:00" for hour in range(24)]:
        raise ValueError(f"{chunk_id}: request must cover all UTC hours")
    days = request.get("day")
    expected_days = [
        f"{day:02d}"
        for month in JJA_MONTHS
        for day in range(1, calendar.monthrange(year, month)[1] + 1)
    ]
    # The live three-month request has one flat day list.  It must be the
    # concatenation of the three month-specific lists; notably June has no
    # day 31.  The v12 reconciliation amendment authorizes the flat 01..31
    # list that the live run actually used; exact returned JJA coverage is
    # still enforced downstream on the payload timestamps.
    if days == [f"{day:02d}" for day in range(1, 32)]:
        if not allow_v12_day_list:
            raise ValueError(f"{chunk_id}: non-calendar-valid June-31 request")
        return
    if not isinstance(days, list) or not days:
        raise ValueError(f"{chunk_id}: request day list is missing")
    # A multi-month CDS request may use one common valid-day list only when it
    # is a subset valid for all months.  The bridge requires the exact returned
    # timestamps, so the plan is still recorded as non-calendar if it cannot
    # state the month-specific calendar.  The coordinator's current worker
    # uses the invalid 01..31 form above and is intentionally blocked.
    if any(str(day) not in {f"{n:02d}" for n in range(1, 32)} for day in days):
        raise ValueError(f"{chunk_id}: malformed request day value")
    if expected_days != days:
        raise ValueError(
            f"{chunk_id}: request day list is not a month-specific calendar plan"
        )


def _load_lane_plan(
    lane: Path,
    *,
    expected_entries: int = 150,
    allow_v12_day_list: bool = False,
) -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    retrieval = lane / "retrieval"
    plan_path = retrieval / "armc_request_plan_v0.json"
    plan_sha = _verify_sidecar(plan_path)
    document = _read_json(plan_path)
    if document.get("schema") != PLAN_SCHEMA:
        raise ValueError(f"{plan_path}: wrong plan schema")
    requests = document.get("requests")
    if not isinstance(requests, list):
        raise ValueError(f"{plan_path}: requests must be a list")
    if document.get("request_count") != len(requests):
        raise ValueError(f"{plan_path}: request_count mismatch")
    if len(requests) != expected_entries:
        raise ValueError(
            f"{plan_path}: expected {expected_entries} raw requests, got {len(requests)}"
        )
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for raw in requests:
        if not isinstance(raw, Mapping):
            raise ValueError(f"{plan_path}: request entry is not an object")
        item = dict(raw)
        chunk_id = str(item.get("chunk_id"))
        if chunk_id in seen:
            raise ValueError(f"{plan_path}: duplicate chunk {chunk_id}")
        seen.add(chunk_id)
        if item.get("variable") != lane.name.removeprefix("payload-"):
            raise ValueError(f"{chunk_id}: variable does not match lane")
        _validate_plan_request(item, allow_v12_day_list=allow_v12_day_list)
        normalized.append({
            **item,
            "source_plan_path": plan_path,
            "source_plan_sha256": plan_sha,
            "lane": lane.name,
        })
    return plan_path, document, normalized


def _validate_file_chain(
    lane: Path,
    item: Mapping[str, Any],
) -> dict[str, Any]:
    retrieval = lane / "retrieval"
    chunk_id = str(item["chunk_id"])
    request_path = retrieval / "requests" / f"{chunk_id}.json"
    receipt_path = retrieval / "chunks" / f"{chunk_id}.json"
    payload_path = retrieval / "payloads" / f"{chunk_id}.nc"
    request_sha = _verify_sidecar(request_path)
    receipt_sha = _verify_sidecar(receipt_path)
    payload_sha = _verify_sidecar(payload_path)
    request_doc = _read_json(request_path)
    receipt = _read_json(receipt_path)
    if request_doc.get("schema") != REQUEST_SCHEMA:
        raise ValueError(f"{chunk_id}: wrong request schema")
    if receipt.get("schema") != RECEIPT_SCHEMA:
        raise ValueError(f"{chunk_id}: wrong receipt schema")
    if request_doc.get("chunk_id") != chunk_id or receipt.get("chunk_id") != chunk_id:
        raise ValueError(f"{chunk_id}: request/receipt chunk identity mismatch")
    request = request_doc.get("request")
    if not isinstance(request, Mapping):
        raise ValueError(f"{chunk_id}: request document has no request")
    if request_doc.get("request_sha256") != sha256_bytes(_canonical_json(request)):
        raise ValueError(f"{chunk_id}: request digest is not canonical")
    if receipt.get("request_sha256") != request_doc.get("request_sha256"):
        raise ValueError(f"{chunk_id}: receipt request digest mismatch")
    if receipt.get("response_sha256") != payload_sha:
        raise ValueError(f"{chunk_id}: receipt payload digest mismatch")
    if int(receipt.get("response_bytes", -1)) != payload_path.stat().st_size:
        raise ValueError(f"{chunk_id}: receipt payload size mismatch")
    expected_request_rel = f"retrieval/requests/{chunk_id}.json"
    expected_payload_rel = f"retrieval/payloads/{chunk_id}.nc"
    if request_doc.get("chunk_id") != chunk_id:
        raise ValueError(f"{chunk_id}: request identity mismatch")
    if receipt.get("request_relpath") != expected_request_rel:
        raise ValueError(f"{chunk_id}: receipt request path mismatch")
    if receipt.get("payload_relpath") != expected_payload_rel:
        raise ValueError(f"{chunk_id}: receipt payload path mismatch")
    return {
        **dict(item),
        "request_path": request_path,
        "receipt_path": receipt_path,
        "payload_path": payload_path,
        "request_sha256": request_sha,
        "receipt_sha256": receipt_sha,
        "payload_sha256": payload_sha,
        "request_document": request_doc,
        "receipt": receipt,
    }


def _reject_extra_files(lane: Path, chunk_ids: set[str]) -> None:
    retrieval = lane / "retrieval"
    expected = {
        "requests": {f"{chunk_id}.json" for chunk_id in chunk_ids},
        "chunks": {f"{chunk_id}.json" for chunk_id in chunk_ids},
        "payloads": {f"{chunk_id}.nc" for chunk_id in chunk_ids},
    }
    for directory_name, expected_names in expected.items():
        directory = retrieval / directory_name
        actual_names = {path.name for path in directory.iterdir() if path.is_file() and not path.name.endswith(".sha256")}
        extras = sorted(actual_names - expected_names)
        if extras:
            raise ValueError(f"{lane.name}/{directory_name}: undeclared files {extras}")
        sidecar_names = {
            path.name[:-len(".sha256")]
            for path in directory.glob("*.sha256")
        }
        orphan_sidecars = sorted(sidecar_names - actual_names)
        if orphan_sidecars:
            raise ValueError(f"{lane.name}/{directory_name}: orphan sidecars {orphan_sidecars}")


def _validate_raw_dataset(record: Mapping[str, Any]) -> dict[str, Any]:
    source_variable = CANONICAL_TO_SOURCE[str(record["variable"])]
    payload_path = Path(record["payload_path"])
    with xr.open_dataset(payload_path, engine="netcdf4", decode_times=True) as source:
        ds = source.load()
    if set(ds.data_vars) != {source_variable}:
        raise ValueError(
            f"{record['chunk_id']}: expected only CDS variable {source_variable!r}, "
            f"got {sorted(ds.data_vars)}"
        )
    if set(ds.coords) != EXPECTED_COORDS:
        raise ValueError(
            f"{record['chunk_id']}: unexpected CDS coordinate names "
            f"{sorted(set(ds.coords) ^ EXPECTED_COORDS)}"
        )
    if set(ds[source_variable].dims) != {
        "valid_time",
        "pressure_level",
        "latitude",
        "longitude",
    }:
        raise ValueError(f"{record['chunk_id']}: unexpected payload dimensions")
    _scalar_coord(ds, "number")
    _scalar_coord(ds, "expver")
    level = int(record["pressure_level_hpa"])
    levels = np.asarray(ds.coords["pressure_level"].values)
    if levels.ndim != 1 or levels.size != 1 or int(levels[0]) != level:
        raise ValueError(f"{record['chunk_id']}: pressure level mismatch")
    lat = np.asarray(ds.coords["latitude"].values, dtype=np.float64)
    lon = np.asarray(ds.coords["longitude"].values, dtype=np.float64)
    if lat.ndim != 1 or lon.ndim != 1 or not np.isfinite(lat).all() or not np.isfinite(lon).all():
        raise ValueError(f"{record['chunk_id']}: invalid spatial coordinates")
    expected = expected_grid(tuple(float(v) for v in record["area_nwse"]))
    if not np.allclose(lat, expected["latitude"], atol=1e-8, rtol=0.0):
        raise ValueError(f"{record['chunk_id']}: latitude grid mismatch")
    if not np.allclose(lon, expected["longitude"], atol=1e-8, rtol=0.0):
        raise ValueError(f"{record['chunk_id']}: longitude grid mismatch")
    times = _utc_index(ds.coords["valid_time"].values)
    expected_times_all = expected_jja_times(int(record["year"]))
    if times.has_duplicates or not times.is_monotonic_increasing:
        raise ValueError(f"{record['chunk_id']}: duplicate or unordered valid_time")
    if not times.equals(expected_times_all):
        if not set(times.month).issubset(set(JJA_MONTHS)):
            raise ValueError(f"{record['chunk_id']}: non-JJA timestamp")
        raise ValueError(f"{record['chunk_id']}: partial or non-complete JJA hours")
    values = np.asarray(ds[source_variable].values)
    if not np.isfinite(values).all():
        raise ValueError(f"{record['chunk_id']}: non-finite payload value")
    return {
        **dict(record),
        "source_variable": source_variable,
        "canonical_variable": SOURCE_TO_CANONICAL[source_variable],
        "dataset": ds,
        "times": times,
    }


def _preflight(
    evidence_root: Path,
    *,
    allow_v12_day_list: bool = False,
    expected_lanes: tuple[str, ...] = EXPECTED_LANE_NAMES,
    expected_entries_per_lane: int = 150,
    expected_raw_chunks: int = EXPECTED_RAW_CHUNKS,
) -> list[dict[str, Any]]:
    lanes = sorted(path for path in evidence_root.glob("payload-*") if path.is_dir())
    if tuple(path.name for path in lanes) != tuple(sorted(expected_lanes)):
        raise ValueError(
            f"expected payload lanes {tuple(sorted(expected_lanes))}, got "
            f"{tuple(path.name for path in lanes)}"
        )
    records: list[dict[str, Any]] = []
    for lane in lanes:
        _plan_path, _document, plan_records = _load_lane_plan(
            lane, expected_entries=expected_entries_per_lane,
            allow_v12_day_list=allow_v12_day_list,
        )
        _reject_extra_files(lane, {str(item["chunk_id"]) for item in plan_records})
        for item in plan_records:
            records.append(_validate_file_chain(lane, item))
    if len(records) != expected_raw_chunks:
        raise ValueError(f"expected {expected_raw_chunks} raw chunks, got {len(records)}")
    ids = [str(item["chunk_id"]) for item in records]
    if len(set(ids)) != expected_raw_chunks:
        raise ValueError("raw chunk ids are not globally unique")
    return [_validate_raw_dataset(item) for item in records]


def _monthly_bytes(ds: xr.Dataset, source_variable: str, month: int) -> bytes:
    times = _utc_index(ds.coords["valid_time"].values)
    positions = np.flatnonzero(times.month == month)
    expected = expected_times(int(times[0].year), month)
    selected = times[positions]
    if not selected.equals(expected):
        raise ValueError(f"month {month} is not complete")
    monthly = ds.isel(valid_time=positions)
    monthly = monthly.rename({"valid_time": "time", source_variable: SOURCE_TO_CANONICAL[source_variable]})
    with tempfile.TemporaryDirectory(prefix="armc-monthly-") as tmp_dir:
        tmp_path = Path(tmp_dir) / "payload.nc"
        monthly.to_netcdf(tmp_path, engine="netcdf4")
        return tmp_path.read_bytes()


def split_payloads(
    evidence_root: Path,
    *,
    manifest_out: Path | None = None,
    dry_run: bool = False,
    reconcile_v12: Path | None = None,
    _expected_lanes: tuple[str, ...] = EXPECTED_LANE_NAMES,
    _expected_entries_per_lane: int = 150,
    _expected_raw_chunks: int = EXPECTED_RAW_CHUNKS,
) -> dict[str, Any]:
    """Validate all raw bytes and optionally publish the 1,800 monthly bridge."""
    evidence_root = Path(evidence_root).resolve()
    if not evidence_root.is_dir():
        raise ValueError(f"evidence root does not exist: {evidence_root}")
    manifest_out = manifest_out or evidence_root / "retrieval/armc_monthly_split_manifest_v0.json"
    manifest_out = Path(manifest_out).resolve()
    if manifest_out.exists() or Path(str(manifest_out) + ".sha256").exists():
        raise ExistingEvidenceError(f"refusing existing split manifest: {manifest_out}")
    v12_sha = None
    if reconcile_v12 is not None:
        reconcile_v12 = Path(reconcile_v12).resolve()
        doc = _read_json(reconcile_v12)
        if doc.get("schema") != "P5_AMENDMENT_V12_ARMC_RECONCILIATION":
            raise ValueError(f"{reconcile_v12}: wrong reconciliation schema")
        v12_sha = _verify_sidecar(reconcile_v12)
    records = _preflight(
        evidence_root,
        allow_v12_day_list=v12_sha is not None,
        expected_lanes=_expected_lanes,
        expected_entries_per_lane=_expected_entries_per_lane,
        expected_raw_chunks=_expected_raw_chunks,
    )
    if dry_run:
        return {
            "schema": SCHEMA,
            "status": "SPLIT_PREFLIGHT_OK",
            "raw_record_count": len(records),
            "monthly_record_count": len(records) * len(JJA_MONTHS),
            "claim_scope": CLAIM_SCOPE,
        }
    output_dirs = sorted({Path(item["payload_path"]).parent.parent / "payloads_monthly" for item in records})
    for output_dir in output_dirs:
        if output_dir.exists():
            raise ExistingEvidenceError(f"refusing existing monthly output root: {output_dir}")
    monthly_records: list[dict[str, Any]] = []
    repo_head = _repo_head()
    for record in sorted(records, key=lambda item: str(item["chunk_id"])):
        dataset = record["dataset"]
        for month in JJA_MONTHS:
            chunk_id = (
                f"{record['basin']}-{record['variable']}-"
                f"{record['pressure_level_hpa']}-{record['year']}-{month:02d}"
            )
            lane = evidence_root / str(record["lane"])
            output_dir = lane / "retrieval/payloads_monthly"
            output_path = output_dir / f"{chunk_id}.nc"
            output_bytes = _monthly_bytes(dataset, str(record["source_variable"]), month)
            output_sha = write_once_bytes(output_path, output_bytes)
            write_once_sidecar(output_path)
            monthly_records.append({
                "chunk_id": chunk_id,
                "lane": str(record["lane"]),
                "basin": record["basin"],
                "variable": record["variable"],
                "pressure_level_hpa": int(record["pressure_level_hpa"]),
                "year": int(record["year"]),
                "month": month,
                "payload_relpath": str(output_path.relative_to(evidence_root)),
                "payload_sha256": output_sha,
                "response_sha256": output_sha,
                "source_to_canonical": {
                    "valid_time": "time",
                    str(record["source_variable"]): str(record["canonical_variable"]),
                },
                "derived_from": {
                    "raw_payload_relpath": str(Path(record["payload_path"]).relative_to(evidence_root)),
                    "raw_payload_sha256": record["payload_sha256"],
                    "raw_request_sha256": record["request_sha256"],
                    "raw_receipt_sha256": record["receipt_sha256"],
                    "source_plan_sha256": record["source_plan_sha256"],
                    "raw_chunk_id": record["chunk_id"],
                },
                "splitter_version": SPLITTER_VERSION,
                "repository_head": repo_head,
                "claim_scope": CLAIM_SCOPE,
            })
    expected_monthly_chunks = _expected_raw_chunks * len(JJA_MONTHS)
    if len(monthly_records) != expected_monthly_chunks:
        raise ValueError(f"expected {expected_monthly_chunks} monthly outputs")
    if len({item["chunk_id"] for item in monthly_records}) != expected_monthly_chunks:
        raise ValueError("monthly chunk ids are not unique")
    manifest = {
        "schema": SCHEMA,
        "status": "SPLIT_VERIFIED",
        "splitter_version": SPLITTER_VERSION,
        "repository_head": repo_head,
        "source_evidence_root_name": evidence_root.name,
        "raw_record_count": len(records),
        "monthly_record_count": len(monthly_records),
        "source_plan_sha256": sorted({str(item["source_plan_sha256"]) for item in records}),
        "reconcile_v12_sha256": v12_sha,
        "chunks": monthly_records,
        "claim_scope": CLAIM_SCOPE,
    }
    write_once_json(manifest_out, manifest, indent=2)
    write_once_sidecar(manifest_out)
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Split and normalize Arm C JJA NetCDF payloads.")
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--manifest-out", type=Path)
    parser.add_argument("--reconcile-v12", type=Path,
                        help="path to the v12 June-31 request-shape reconciliation amendment")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = split_payloads(
            args.evidence_root,
            manifest_out=args.manifest_out,
            dry_run=args.dry_run,
            reconcile_v12=args.reconcile_v12,
        )
    except (OSError, ValueError, ExistingEvidenceError) as exc:
        print(json.dumps({"status": "BLOCKED", "problems": [str(exc)]}, indent=2))
        return 2
    print(json.dumps({
        "status": result["status"],
        "raw_record_count": result["raw_record_count"],
        "monthly_record_count": result["monthly_record_count"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
