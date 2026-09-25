#!/usr/bin/env python3
"""Independent, read-only auditor for the Arm C raw retrieval roots.

The auditor intentionally does not import the monthly splitter.  It verifies
the 600-request raw surface independently so a splitter defect cannot make
its own input appear valid.  It never contacts CDS and it only writes a report
when the caller explicitly supplies a report path.
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import subprocess
import sys
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
from p5_safe_io import sha256_bytes, write_once_json, write_once_sidecar  # noqa: E402


CLAIM_SCOPE = "research_only_no_operational_authorization"
PLAN_SCHEMA = "P5_ARMC_REQUEST_PLAN_V0"
REQUEST_SCHEMA = "P5_ARMC_REQUEST_V0"
RECEIPT_SCHEMA = "P5_ARMC_CHUNK_RECEIPT_V0"
REPORT_SCHEMA = "P5_ARMC_RETRIEVAL_INTEGRITY_REPORT_V0"
EXPECTED_LANES = {
    "payload-geopotential": ("z", "geopotential"),
    "payload-specific_humidity": ("q", "specific_humidity"),
    "payload-temperature": ("t", "temperature"),
    "payload-vertical_velocity": ("w", "vertical_velocity"),
}
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
DEFAULT_MAX_NAN_FRACTION = 0.0


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _verify_sidecar(path: Path, problems: list[str], label: str) -> str | None:
    if not path.is_file():
        problems.append(f"{label}: missing file {path}")
        return None
    sidecar = Path(str(path) + ".sha256")
    if not sidecar.is_file():
        problems.append(f"{label}: missing sidecar {sidecar}")
        return None
    actual = _sha(path)
    fields = sidecar.read_text(encoding="utf-8").split()
    if not fields or fields[0] != actual:
        problems.append(f"{label}: sidecar digest mismatch for {path}")
        return None
    return actual


def _utc_index(values: Any, label: str, problems: list[str]) -> pd.DatetimeIndex | None:
    try:
        index = pd.DatetimeIndex(values)
        if index.tz is None:
            index = index.tz_localize("UTC")
        else:
            index = index.tz_convert("UTC")
        return index
    except (TypeError, ValueError) as exc:
        problems.append(f"{label}: invalid UTC timestamps: {exc}")
        return None


def _expected_times(year: int, month: int) -> pd.DatetimeIndex:
    start = pd.Timestamp(year=year, month=month, day=1, tz="UTC")
    end = start + pd.offsets.MonthEnd(1) + pd.Timedelta(hours=23)
    return pd.date_range(start, end, freq="h")


def _expected_jja(year: int) -> pd.DatetimeIndex:
    pieces = [_expected_times(year, month).values for month in JJA_MONTHS]
    return pd.DatetimeIndex(np.concatenate(pieces)).tz_localize("UTC")


def _repo_head() -> str | None:
    result = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _validate_request_shape(
    item: Mapping[str, Any],
    request: Mapping[str, Any],
    problems: list[str],
    *,
    allow_v12_day_list: bool = False,
) -> None:
    chunk_id = str(item.get("chunk_id"))
    variable = str(item.get("variable"))
    try:
        level = int(item.get("pressure_level_hpa"))
    except (TypeError, ValueError):
        level = None
        problems.append(f"{chunk_id}: invalid pressure level in plan")
    try:
        year = int(item.get("year"))
    except (TypeError, ValueError):
        year = None
        problems.append(f"{chunk_id}: invalid year in plan")
    if request.get("variable") != [variable]:
        problems.append(f"{chunk_id}: request variable differs from plan")
    if level is not None and request.get("pressure_level") != [str(level)]:
        problems.append(f"{chunk_id}: request pressure level differs from plan")
    if year is not None and request.get("year") != [str(year)]:
        problems.append(f"{chunk_id}: request year differs from plan")
    if request.get("month") != ["06", "07", "08"]:
        problems.append(f"{chunk_id}: request is not exactly JJA")
    if request.get("time") != [f"{hour:02d}:00" for hour in range(24)]:
        problems.append(f"{chunk_id}: request does not cover every UTC hour")
    days = request.get("day")
    if not isinstance(days, list) or not days:
        problems.append(f"{chunk_id}: request day list is missing")
    elif request.get("month") == ["06", "07", "08"]:
        common_day_set = [f"{day:02d}" for day in range(1, 32)]
        calendar_days = [
            f"{day:02d}"
            for month, last_day in ((6, 30), (7, 31), (8, 31))
            for day in range(1, last_day + 1)
        ]
        if days == common_day_set:
            if not allow_v12_day_list:
                problems.append(f"{chunk_id}: request includes invalid June 31")
        elif days != calendar_days:
            problems.append(f"{chunk_id}: request day list is not calendar-valid")


def _record_extra_files(
    retrieval: Path,
    expected_chunk_ids: set[str],
    problems: list[str],
    lane_name: str,
) -> None:
    expected_names = {
        "requests": {f"{chunk_id}.json" for chunk_id in expected_chunk_ids},
        "chunks": {f"{chunk_id}.json" for chunk_id in expected_chunk_ids},
        "payloads": {f"{chunk_id}.nc" for chunk_id in expected_chunk_ids},
    }
    for directory_name, names in expected_names.items():
        directory = retrieval / directory_name
        if not directory.is_dir():
            problems.append(f"{lane_name}: missing directory {directory}")
            continue
        actual = {
            path.name
            for path in directory.iterdir()
            if path.is_file() and not path.name.endswith(".sha256")
        }
        extras = sorted(actual - names)
        if extras:
            problems.append(f"{lane_name}/{directory_name}: undeclared files {extras}")
        sidecars = {
            path.name[:-len(".sha256")]
            for path in directory.glob("*.sha256")
        }
        orphans = sorted(sidecars - actual)
        if orphans:
            problems.append(f"{lane_name}/{directory_name}: orphan sidecars {orphans}")


def _audit_lane(
    evidence_root: Path,
    lane: Path,
    problems: list[str],
    *,
    max_nan_fraction: float,
    expected_entries: int = 150,
    lane_specs: Mapping[str, tuple[str, str]] = EXPECTED_LANES,
    allow_v12_day_list: bool = False,
    allow_v14_grouped: bool = False,
    allow_v15_earthmover: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    lane_name = lane.name
    source_variable, canonical_variable = lane_specs[lane_name]
    initial_problem_count = len(problems)
    retrieval = lane / "retrieval"
    plan_path = retrieval / "armc_request_plan_v0.json"
    plan_sha = _verify_sidecar(plan_path, problems, lane_name + ":plan")
    if plan_sha is None:
        return [], {"lane": lane_name, "status": "BLOCKED", "chunks": 0}
    try:
        document = _read_json(plan_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        problems.append(f"{lane_name}: unreadable plan: {exc}")
        return [], {"lane": lane_name, "status": "BLOCKED", "chunks": 0}
    if document.get("schema") != PLAN_SCHEMA:
        problems.append(f"{lane_name}: wrong plan schema")
    entries = document.get("requests")
    if not isinstance(entries, list):
        problems.append(f"{lane_name}: plan requests is not a list")
        return [], {"lane": lane_name, "status": "BLOCKED", "chunks": 0}
    if len(entries) != expected_entries or document.get("request_count") != len(entries):
        problems.append(f"{lane_name}: expected {expected_entries} plan entries")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item_index, raw_item in enumerate(entries):
        if not isinstance(raw_item, Mapping):
            problems.append(f"{lane_name}: plan entry {item_index} is not an object")
            continue
        item = dict(raw_item)
        chunk_id = str(item.get("chunk_id"))
        if chunk_id in seen:
            problems.append(f"{lane_name}: duplicate chunk id {chunk_id}")
        seen.add(chunk_id)
        if item.get("variable") != canonical_variable:
            problems.append(f"{chunk_id}: lane variable mismatch")
        request = item.get("request")
        if not isinstance(request, Mapping):
            problems.append(f"{chunk_id}: missing request object")
            continue
        _validate_request_shape(item, request, problems,
                                allow_v12_day_list=allow_v12_day_list)
        request_path = retrieval / "requests" / f"{chunk_id}.json"
        receipt_path = retrieval / "chunks" / f"{chunk_id}.json"
        payload_path = retrieval / "payloads" / f"{chunk_id}.nc"
        request_sha = _verify_sidecar(request_path, problems, f"{chunk_id}:request")
        receipt_sha = _verify_sidecar(receipt_path, problems, f"{chunk_id}:receipt")
        payload_sha = _verify_sidecar(payload_path, problems, f"{chunk_id}:payload")
        if request_sha is None or receipt_sha is None or payload_sha is None:
            continue
        try:
            request_doc = _read_json(request_path)
            receipt = _read_json(receipt_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            problems.append(f"{chunk_id}: unreadable request or receipt: {exc}")
            continue
        if request_doc.get("schema") != REQUEST_SCHEMA:
            problems.append(f"{chunk_id}: wrong request schema")
        if receipt.get("schema") != RECEIPT_SCHEMA:
            problems.append(f"{chunk_id}: wrong receipt schema")
        if request_doc.get("chunk_id") != chunk_id or receipt.get("chunk_id") != chunk_id:
            problems.append(f"{chunk_id}: request/receipt identity mismatch")
        request_doc_body = request_doc.get("request")
        if not isinstance(request_doc_body, Mapping):
            problems.append(f"{chunk_id}: request document has no request body")
        else:
            if request_doc.get("request_sha256") != sha256_bytes(_canonical_json(request_doc_body)):
                problems.append(f"{chunk_id}: request canonical digest mismatch")
            if request_doc_body != request:
                problems.append(f"{chunk_id}: request differs from source plan")
        if receipt.get("request_sha256") != request_doc.get("request_sha256"):
            problems.append(f"{chunk_id}: receipt request digest mismatch")
        if receipt.get("response_sha256") != payload_sha:
            problems.append(f"{chunk_id}: receipt response digest mismatch")
        try:
            declared_bytes = int(receipt.get("response_bytes", -1))
        except (TypeError, ValueError):
            declared_bytes = -1
            problems.append(f"{chunk_id}: receipt response byte count is malformed")
        if declared_bytes != payload_path.stat().st_size:
            problems.append(f"{chunk_id}: receipt response byte count mismatch")
        if receipt.get("request_relpath") != f"retrieval/requests/{chunk_id}.json":
            problems.append(f"{chunk_id}: receipt request path mismatch")
        execution = request_doc.get("execution")
        grouped_rel = receipt.get("grouped_response_relpath")
        em_mode = isinstance(execution, Mapping) and execution.get("mode") == "earthmover_icechunk"
        if em_mode or receipt.get("source") == "earthmover_icechunk" or receipt.get("status") == "PAYLOAD_DERIVED_FROM_EARTHMOVER":
            if not allow_v15_earthmover:
                problems.append(f"{chunk_id}: earthmover execution recorded but no v15 reconciliation bound")
            elif not em_mode:
                problems.append(f"{chunk_id}: malformed earthmover execution record")
            else:
                if execution.get("store") != "s3://earthmover-icechunk-era5/icechunkV2":
                    problems.append(f"{chunk_id}: earthmover store uri mismatch")
                if execution.get("group") != "pressure/temporal":
                    problems.append(f"{chunk_id}: earthmover group mismatch")
                if execution.get("snapshot_id") != "ZFKDHBCTBVHVXM3BQFV0":
                    problems.append(f"{chunk_id}: earthmover snapshot id mismatch")
                if execution.get("amendment") != "p5_amendment_v15_armc_earthmover_source":
                    problems.append(f"{chunk_id}: earthmover amendment binding mismatch")
                if receipt.get("source") != "earthmover_icechunk":
                    problems.append(f"{chunk_id}: earthmover receipt source mismatch")
                if receipt.get("status") != "PAYLOAD_DERIVED_FROM_EARTHMOVER":
                    problems.append(f"{chunk_id}: earthmover receipt status mismatch")
                derived = receipt.get("derived_from", "")
                if not isinstance(derived, str) or "earthmover-icechunk-era5" not in derived:
                    problems.append(f"{chunk_id}: earthmover derived_from lineage malformed")
                if set(receipt.get("synthesized_coords") or []) != {"number", "expver"}:
                    problems.append(f"{chunk_id}: earthmover synthesized coords declaration mismatch")
        elif execution is not None or grouped_rel is not None:
            if not allow_v14_grouped:
                problems.append(f"{chunk_id}: grouped execution recorded but no v14 reconciliation bound")
            elif not isinstance(execution, Mapping) or execution.get("mode") != "grouped_level_pair":
                problems.append(f"{chunk_id}: malformed grouped execution record")
            elif not isinstance(grouped_rel, str) or not grouped_rel.startswith("retrieval/grouped_responses/"):
                problems.append(f"{chunk_id}: grouped response path malformed")
            else:
                gpath = lane / "retrieval" / Path(grouped_rel).relative_to("retrieval")
                gsha = _verify_sidecar(gpath, problems, f"{chunk_id}:grouped_response")
                if gsha is not None and receipt.get("grouped_response_sha256") != gsha:
                    problems.append(f"{chunk_id}: grouped response digest mismatch")
                greq_rel = execution.get("grouped_request_relpath")
                greq_path = lane / "retrieval" / Path(greq_rel).relative_to("retrieval") if isinstance(greq_rel, str) else None
                if greq_path is None or not greq_path.is_file():
                    problems.append(f"{chunk_id}: grouped request record missing")
                else:
                    greq = _read_json(greq_path)
                    if greq.get("request_sha256") != execution.get("grouped_request_sha256"):
                        problems.append(f"{chunk_id}: grouped request digest mismatch")
                    if chunk_id not in (greq.get("grouped_chunk_ids") or []):
                        problems.append(f"{chunk_id}: not listed in grouped request chunk ids")
        if receipt.get("payload_relpath") != f"retrieval/payloads/{chunk_id}.nc":
            problems.append(f"{chunk_id}: receipt payload path mismatch")
        try:
            with xr.open_dataset(payload_path, engine="netcdf4", decode_times=True) as source:
                ds = source.load()
        except Exception as exc:  # xarray/netCDF4 errors are part of the audit report.
            problems.append(f"{chunk_id}: NetCDF cannot be opened: {exc}")
            continue
        if set(ds.data_vars) != {source_variable}:
            problems.append(f"{chunk_id}: expected CDS variable {source_variable}, got {sorted(ds.data_vars)}")
        if set(ds.coords) != EXPECTED_COORDS:
            problems.append(f"{chunk_id}: unexpected coordinate names")
        if source_variable in ds.data_vars and set(ds[source_variable].dims) != {"valid_time", "pressure_level", "latitude", "longitude"}:
            problems.append(f"{chunk_id}: unexpected NetCDF dimensions")
        for coord_name, allowed in (("number", {"0", "0.0"}), ("expver", {"0001"})):
            if coord_name in ds.coords:
                vals = {str(v) for v in np.unique(np.asarray(ds.coords[coord_name].values))}
                if not vals or not vals.issubset(allowed):
                    problems.append(f"{chunk_id}: mixed or wrong {coord_name} values {sorted(vals)[:4]}")
        try:
            level = int(item.get("pressure_level_hpa"))
        except (TypeError, ValueError):
            level = None
            problems.append(f"{chunk_id}: invalid pressure level")
        levels = np.asarray(ds.coords["pressure_level"].values) if "pressure_level" in ds.coords else np.array([])
        if level is not None and (levels.size != 1 or int(levels.reshape(-1)[0]) != level):
            problems.append(f"{chunk_id}: pressure level mismatch")
        if "latitude" in ds.coords and "longitude" in ds.coords:
            try:
                area = item.get("area_nwse")
                if not isinstance(area, list) or len(area) != 4:
                    raise ValueError("area_nwse is not a four-value list")
                expected = expected_grid(tuple(float(v) for v in area))
                if not np.allclose(np.asarray(ds.latitude.values), expected["latitude"], atol=1e-8, rtol=0.0):
                    problems.append(f"{chunk_id}: latitude grid mismatch")
                if not np.allclose(np.asarray(ds.longitude.values), expected["longitude"], atol=1e-8, rtol=0.0):
                    problems.append(f"{chunk_id}: longitude grid mismatch")
            except (KeyError, TypeError, ValueError) as exc:
                problems.append(f"{chunk_id}: invalid declared grid: {exc}")
        times = _utc_index(ds.coords["valid_time"].values, chunk_id, problems) if "valid_time" in ds.coords else None
        if times is not None:
            expected_times = _expected_jja(int(item["year"]))
            if times.has_duplicates or not times.is_monotonic_increasing:
                problems.append(f"{chunk_id}: duplicate or unordered timestamps")
            if not times.equals(expected_times):
                if not set(times.month).issubset(set(JJA_MONTHS)):
                    problems.append(f"{chunk_id}: non-JJA timestamp")
                else:
                    problems.append(f"{chunk_id}: incomplete JJA timestamp coverage")
        if source_variable in ds.data_vars:
            values = np.asarray(ds[source_variable].values)
            try:
                nan_fraction = float(np.isnan(values).mean()) if values.size else 1.0
                finite = bool(np.isfinite(values).all())
            except (TypeError, ValueError):
                nan_fraction = 1.0
                finite = False
            if nan_fraction > max_nan_fraction:
                problems.append(f"{chunk_id}: NaN fraction {nan_fraction} exceeds {max_nan_fraction}")
            if not finite:
                problems.append(f"{chunk_id}: non-finite or non-numeric payload values")
        records.append({
            "chunk_id": chunk_id,
            "lane": lane_name,
            "source_plan_sha256": plan_sha,
            "request_sha256": request_sha,
            "receipt_sha256": receipt_sha,
            "payload_sha256": payload_sha,
            "payload_bytes": payload_path.stat().st_size,
        })
    _record_extra_files(retrieval, seen, problems, lane_name)
    return records, {
        "lane": lane_name,
        "status": "OK" if len(problems) == initial_problem_count else "BLOCKED",
        "chunks": len(records),
    }


V12_SCHEMA = "P5_AMENDMENT_V12_ARMC_RECONCILIATION"
V14_SCHEMA = "P5_AMENDMENT_V14_ARMC_GROUPED_REQUESTS"
V15_SCHEMA = "P5_AMENDMENT_V15_ARMC_EARTHMOVER_SOURCE"


def _verify_amendment(path: Path, schema: str, problems: list[str]) -> str | None:
    """Bind an amendment file by schema; return its sha256 or record a problem."""
    try:
        doc = _read_json(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        problems.append(f"amendment {path.name}: unreadable: {exc}")
        return None
    if doc.get("schema") != schema:
        problems.append(f"amendment {path.name}: wrong schema {doc.get('schema')!r}")
        return None
    return _sha(path)


def audit_retrieval(
    evidence_root: Path,
    *,
    max_nan_fraction: float = DEFAULT_MAX_NAN_FRACTION,
    reconcile_v12: Path | None = None,
    reconcile_v14: Path | None = None,
    reconcile_v15: Path | None = None,
    _expected_lanes: Mapping[str, tuple[str, str]] = EXPECTED_LANES,
    _expected_entries_per_lane: int = 150,
    _expected_raw_chunks: int = EXPECTED_RAW_CHUNKS,
) -> dict[str, Any]:
    evidence_root = Path(evidence_root).resolve()
    problems: list[str] = []
    bound_amendments: dict[str, str] = {}
    if reconcile_v12 is not None:
        sha = _verify_amendment(Path(reconcile_v12), V12_SCHEMA, problems)
        if sha:
            bound_amendments["v12"] = sha
    if reconcile_v14 is not None:
        sha = _verify_amendment(Path(reconcile_v14), V14_SCHEMA, problems)
        if sha:
            bound_amendments["v14"] = sha
    if reconcile_v15 is not None:
        sha = _verify_amendment(Path(reconcile_v15), V15_SCHEMA, problems)
        if sha:
            bound_amendments["v15"] = sha
    if max_nan_fraction < 0 or max_nan_fraction > 1:
        problems.append("max_nan_fraction must be between 0 and 1")
    lanes = sorted(path for path in evidence_root.glob("payload-*") if path.is_dir())
    expected_lanes = set(_expected_lanes)
    actual_lanes = {path.name for path in lanes}
    for missing in sorted(expected_lanes - actual_lanes):
        problems.append(f"missing payload lane {missing}")
    for extra in sorted(actual_lanes - expected_lanes):
        problems.append(f"undeclared payload lane {extra}")
    all_records: list[dict[str, Any]] = []
    lane_reports: list[dict[str, Any]] = []
    for lane in lanes:
        if lane.name not in EXPECTED_LANES:
            continue
        records, lane_report = _audit_lane(
            evidence_root,
            lane,
            problems,
            max_nan_fraction=max_nan_fraction,
            expected_entries=_expected_entries_per_lane,
            lane_specs=_expected_lanes,
            allow_v12_day_list="v12" in bound_amendments,
            allow_v14_grouped="v14" in bound_amendments,
            allow_v15_earthmover="v15" in bound_amendments,
        )
        all_records.extend(records)
        lane_reports.append(lane_report)
    ids = [record["chunk_id"] for record in all_records]
    if len(ids) != _expected_raw_chunks:
        problems.append(f"expected {_expected_raw_chunks} audited chunks, got {len(ids)}")
    if len(set(ids)) != len(ids):
        problems.append("audited chunk ids are not globally unique")
    status = "RETRIEVAL_INTEGRITY_OK" if not problems else "BLOCKED"
    return {
        "schema": REPORT_SCHEMA,
        "status": status,
        "evidence_root_name": evidence_root.name,
        "repository_head": _repo_head(),
        "expected_chunk_count": _expected_raw_chunks,
        "audited_chunk_count": len(all_records),
        "expected_lane_count": len(_expected_lanes),
        "lane_reports": lane_reports,
        "max_nan_fraction": max_nan_fraction,
        "bound_amendments": bound_amendments,
        "problems": sorted(set(problems)),
        "claim_scope": CLAIM_SCOPE,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit the Arm C raw retrieval roots.")
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--max-nan-fraction", type=float, default=DEFAULT_MAX_NAN_FRACTION)
    parser.add_argument("--report-out", type=Path)
    parser.add_argument("--reconcile-v12", type=Path,
                        help="path to the v12 June-31 request-shape reconciliation amendment")
    parser.add_argument("--reconcile-v14", type=Path,
                        help="path to the v14 grouped-level request amendment")
    parser.add_argument("--reconcile-v15", type=Path,
                        help="path to the v15 earthmover source amendment")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = audit_retrieval(args.evidence_root, max_nan_fraction=args.max_nan_fraction,
                             reconcile_v12=args.reconcile_v12,
                             reconcile_v14=args.reconcile_v14,
                             reconcile_v15=args.reconcile_v15)
    if args.report_out is not None:
        write_once_json(args.report_out, report, indent=2)
        write_once_sidecar(args.report_out)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "RETRIEVAL_INTEGRITY_OK" else 2


if __name__ == "__main__":
    raise SystemExit(main())
