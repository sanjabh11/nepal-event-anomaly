from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from scripts.armc_monthly_split import expected_jja_times
from scripts.verify_armc_retrieval import audit_retrieval, main
import xarray as xr


LANE = "payload-geopotential"
SPECS = {LANE: ("z", "geopotential")}


def _sidecar(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    Path(str(path) + ".sha256").write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    return digest


def _dataset(*, bad_grid: bool = False, nonfinite: bool = False) -> xr.Dataset:
    times = expected_jja_times(2001).tz_localize(None)
    latitude = np.array([28.25, 28.0, 27.75])
    longitude = np.array([86.75, 87.0, 87.25])
    if bad_grid:
        longitude[-1] = 87.5
    values = np.ones((len(times), 1, len(latitude), len(longitude)))
    if nonfinite:
        values[0, 0, 0, 0] = np.nan
    return xr.Dataset(
        {"z": (("valid_time", "pressure_level", "latitude", "longitude"), values)},
        coords={
            "number": np.array(0),
            "valid_time": times,
            "pressure_level": np.array([500]),
            "latitude": latitude,
            "longitude": longitude,
            "expver": np.array("0001"),
        },
    )


def _root(
    tmp_path: Path,
    *,
    dataset: xr.Dataset | None = None,
    invalid_june31: bool = False,
) -> Path:
    root = tmp_path / "armc"
    retrieval = root / LANE / "retrieval"
    for name in ("requests", "payloads", "chunks"):
        (retrieval / name).mkdir(parents=True)
    chunk_id = "koshi-geopotential-500-2001"
    days = [f"{day:02d}" for day in range(1, 32)] if invalid_june31 else [
        f"{day:02d}"
        for last_day in (30, 31, 31)
        for day in range(1, last_day + 1)
    ]
    request = {
        "product_type": ["reanalysis"],
        "variable": ["geopotential"],
        "year": ["2001"],
        "month": ["06", "07", "08"],
        "day": days,
        "time": [f"{hour:02d}:00" for hour in range(24)],
        "pressure_level": ["500"],
        "area": [28.25, 86.75, 27.75, 87.25],
        "data_format": "netcdf",
        "download_format": "unarchived",
    }
    item = {
        "chunk_id": chunk_id,
        "basin": "koshi",
        "variable": "geopotential",
        "pressure_level_hpa": 500,
        "year": 2001,
        "area_nwse": [28.25, 86.75, 27.75, 87.25],
        "request": request,
    }
    plan = {
        "schema": "P5_ARMC_REQUEST_PLAN_V0",
        "request_count": 1,
        "requests": [item],
    }
    plan_path = retrieval / "armc_request_plan_v0.json"
    plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    _sidecar(plan_path)
    request_digest = hashlib.sha256(
        (json.dumps(request, sort_keys=True, separators=(",", ":")) + "\n").encode()
    ).hexdigest()
    request_doc = {
        "schema": "P5_ARMC_REQUEST_V0",
        "chunk_id": chunk_id,
        "request": request,
        "request_sha256": request_digest,
    }
    request_path = retrieval / "requests" / f"{chunk_id}.json"
    request_path.write_text(json.dumps(request_doc, indent=2), encoding="utf-8")
    _sidecar(request_path)
    payload_path = retrieval / "payloads" / f"{chunk_id}.nc"
    (dataset if dataset is not None else _dataset()).to_netcdf(payload_path, engine="netcdf4")
    payload_digest = _sidecar(payload_path)
    receipt = {
        "schema": "P5_ARMC_CHUNK_RECEIPT_V0",
        "chunk_id": chunk_id,
        "request_relpath": f"retrieval/requests/{chunk_id}.json",
        "payload_relpath": f"retrieval/payloads/{chunk_id}.nc",
        "request_sha256": request_digest,
        "response_sha256": payload_digest,
        "response_bytes": payload_path.stat().st_size,
        "status": "PAYLOAD_RECEIVED_UNPARSED",
    }
    receipt_path = retrieval / "chunks" / f"{chunk_id}.json"
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    _sidecar(receipt_path)
    return root


def _audit(root: Path, **kwargs):
    return audit_retrieval(
        root,
        _expected_lanes=SPECS,
        _expected_entries_per_lane=1,
        _expected_raw_chunks=1,
        **kwargs,
    )


def test_auditor_accepts_complete_raw_fixture(tmp_path: Path) -> None:
    report = _audit(_root(tmp_path))
    assert report["status"] == "RETRIEVAL_INTEGRITY_OK"
    assert report["audited_chunk_count"] == 1
    assert report["problems"] == []


@pytest.mark.parametrize("dataset", [_dataset(bad_grid=True), _dataset(nonfinite=True)])
def test_auditor_blocks_payload_integrity_failures(tmp_path: Path, dataset: xr.Dataset) -> None:
    report = _audit(_root(tmp_path, dataset=dataset))
    assert report["status"] == "BLOCKED"
    assert report["problems"]


def test_auditor_blocks_tampered_payload_and_lists_digest_problem(tmp_path: Path) -> None:
    root = _root(tmp_path)
    payload = next((root / LANE / "retrieval/payloads").glob("*.nc"))
    payload.write_bytes(payload.read_bytes() + b"tamper")
    report = _audit(root)
    assert report["status"] == "BLOCKED"
    assert any("sidecar digest mismatch" in problem for problem in report["problems"])


def test_auditor_blocks_missing_receipt_and_sidecar(tmp_path: Path) -> None:
    root = _root(tmp_path)
    receipt = next((root / LANE / "retrieval/chunks").glob("*.json"))
    Path(str(receipt) + ".sha256").unlink()
    report = _audit(root)
    assert report["status"] == "BLOCKED"
    assert any("missing sidecar" in problem for problem in report["problems"])


def test_auditor_blocks_undeclared_raw_file(tmp_path: Path) -> None:
    root = _root(tmp_path)
    extra = root / LANE / "retrieval/payloads/undeclared.nc"
    extra.write_bytes(b"undeclared")
    _sidecar(extra)
    report = _audit(root)
    assert report["status"] == "BLOCKED"
    assert any("undeclared files" in problem for problem in report["problems"])


def test_auditor_blocks_invalid_june31_plan(tmp_path: Path) -> None:
    report = _audit(_root(tmp_path, invalid_june31=True))
    assert report["status"] == "BLOCKED"
    assert any("June 31" in problem for problem in report["problems"])


def test_default_audit_requires_all_four_lanes_and_600_chunks(tmp_path: Path) -> None:
    report = audit_retrieval(_root(tmp_path))
    assert report["status"] == "BLOCKED"
    assert report["expected_chunk_count"] == 600
    assert any("missing payload lane" in problem for problem in report["problems"])
    assert any("expected 600 audited chunks" in problem for problem in report["problems"])


def test_cli_writes_report_and_returns_nonzero_on_blocked(tmp_path: Path) -> None:
    root = _root(tmp_path, invalid_june31=True)
    report_path = tmp_path / "audit.json"
    code = main(["--evidence-root", str(root), "--report-out", str(report_path)])
    assert code == 2
    assert json.loads(report_path.read_text(encoding="utf-8"))["status"] == "BLOCKED"
    assert Path(str(report_path) + ".sha256").exists()
