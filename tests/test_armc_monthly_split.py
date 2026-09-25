from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from scripts.armc_monthly_split import (
    CLAIM_SCOPE,
    ExistingEvidenceError,
    expected_jja_times,
    split_payloads,
)


def _sidecar(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    Path(str(path) + ".sha256").write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    return digest


def _request_days() -> list[str]:
    return [
        f"{day:02d}"
        for month, last_day in ((6, 30), (7, 31), (8, 31))
        for day in range(1, last_day + 1)
    ]


def _dataset(
    *,
    non_jja: bool = False,
    duplicate: bool = False,
    partial: bool = False,
    bad_grid: bool = False,
    missing_level: bool = False,
    nonfinite: bool = False,
    unknown_variable: bool = False,
    unknown_coordinate: bool = False,
    mixed_expver: bool = False,
) -> xr.Dataset:
    times = expected_jja_times(2001).tz_localize(None)
    if non_jja:
        values = times.to_numpy().copy()
        values[0] = np.datetime64("2001-05-31T23:00:00")
        times = pd.DatetimeIndex(values)
    if duplicate:
        values = times.to_numpy().copy()
        values[100] = values[99]
        times = pd.DatetimeIndex(values)
    if partial:
        times = times[:-1]
    latitude = np.array([28.25, 28.0, 27.75])
    longitude = np.array([86.75, 87.0, 87.25])
    if bad_grid:
        longitude[-1] = 87.5
    levels = np.array([700]) if missing_level else np.array([500])
    number = np.array([0, 1]) if mixed_expver else np.array(0)
    expver = np.array([1, 5]) if mixed_expver else np.array("0001")
    values = np.ones((len(times), len(levels), len(latitude), len(longitude)))
    if nonfinite:
        values[0, 0, 0, 0] = np.nan
    variable = "unknown" if unknown_variable else "z"
    ds = xr.Dataset(
        {
            variable: (
                ("valid_time", "pressure_level", "latitude", "longitude"),
                values,
            )
        },
        coords={
            "number": number,
            "valid_time": times,
            "pressure_level": levels,
            "latitude": latitude,
            "longitude": longitude,
            "expver": expver,
        },
    )
    if unknown_coordinate:
        ds = ds.assign_coords(mystery=("latitude", np.arange(len(latitude))))
    if mixed_expver:
        # Make the mixed metadata explicit without changing the payload's
        # declared four scientific dimensions.
        ds = ds.assign_coords(number=number, expver=expver)
    return ds


def _write_fixture(
    tmp_path: Path,
    *,
    dataset: xr.Dataset | None = None,
    invalid_june31: bool = False,
    write_payload: bool = True,
) -> Path:
    root = tmp_path / "armc"
    lane = root / "payload-geopotential" / "retrieval"
    for name in ("requests", "payloads", "chunks"):
        (lane / name).mkdir(parents=True)
    chunk_id = "koshi-geopotential-500-2001"
    request = {
        "product_type": ["reanalysis"],
        "variable": ["geopotential"],
        "year": ["2001"],
        "month": ["06", "07", "08"],
        "day": [f"{day:02d}" for day in range(1, 32)] if invalid_june31 else _request_days(),
        "time": [f"{hour:02d}:00" for hour in range(24)],
        "pressure_level": ["500"],
        "area": [28.25, 86.75, 27.75, 87.25],
        "data_format": "netcdf",
        "download_format": "unarchived",
    }
    plan_item = {
        "chunk_id": chunk_id,
        "basin": "koshi",
        "variable": "geopotential",
        "pressure_level_hpa": 500,
        "year": 2001,
        "month": 0,
        "area_nwse": [28.25, 86.75, 27.75, 87.25],
        "request": request,
    }
    plan = {
        "schema": "P5_ARMC_REQUEST_PLAN_V0",
        "request_count": 1,
        "requests": [plan_item],
        "claim_scope": CLAIM_SCOPE,
    }
    plan_path = lane / "armc_request_plan_v0.json"
    plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    _sidecar(plan_path)

    request_doc = {
        "schema": "P5_ARMC_REQUEST_V0",
        "chunk_id": chunk_id,
        "dataset": "reanalysis-era5-pressure-levels",
        "request": request,
        "request_sha256": hashlib.sha256(
            (json.dumps(request, sort_keys=True, separators=(",", ":")) + "\n").encode()
        ).hexdigest(),
    }
    request_path = lane / "requests" / f"{chunk_id}.json"
    request_path.write_text(json.dumps(request_doc, indent=2), encoding="utf-8")
    request_sha = _sidecar(request_path)

    payload_path = lane / "payloads" / f"{chunk_id}.nc"
    if write_payload:
        (dataset if dataset is not None else _dataset()).to_netcdf(payload_path, engine="netcdf4")
        payload_sha = _sidecar(payload_path)
    else:
        payload_sha = "0" * 64
    receipt = {
        "schema": "P5_ARMC_CHUNK_RECEIPT_V0",
        "chunk_id": chunk_id,
        "request_relpath": f"retrieval/requests/{chunk_id}.json",
        "payload_relpath": f"retrieval/payloads/{chunk_id}.nc",
        "request_sha256": request_doc["request_sha256"],
        "response_sha256": payload_sha,
        "response_bytes": payload_path.stat().st_size if payload_path.exists() else 0,
        "status": "PAYLOAD_RECEIVED_UNPARSED",
    }
    receipt_path = lane / "chunks" / f"{chunk_id}.json"
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    _sidecar(receipt_path)
    assert request_sha
    return root


def _run_one(tmp_path: Path, **kwargs):
    root = _write_fixture(tmp_path, **kwargs)
    return split_payloads(
        root,
        manifest_out=root / "split.json",
        _expected_lanes=("payload-geopotential",),
        _expected_entries_per_lane=1,
        _expected_raw_chunks=1,
    )


def test_split_normalizes_real_cds_schema_and_emits_lineage(tmp_path: Path) -> None:
    result = _run_one(tmp_path)
    assert result["status"] == "SPLIT_VERIFIED"
    assert result["raw_record_count"] == 1
    assert result["monthly_record_count"] == 3
    output_dir = tmp_path / "armc/payload-geopotential/retrieval/payloads_monthly"
    outputs = sorted(output_dir.glob("*.nc"))
    assert [path.name for path in outputs] == [
        "koshi-geopotential-500-2001-06.nc",
        "koshi-geopotential-500-2001-07.nc",
        "koshi-geopotential-500-2001-08.nc",
    ]
    for path in outputs:
        assert Path(str(path) + ".sha256").exists()
        with xr.open_dataset(path, engine="netcdf4") as ds:
            assert "time" in ds.coords
            assert "valid_time" not in ds.coords
            assert "geopotential" in ds.data_vars
            assert "z" not in ds.data_vars
    record = result["chunks"][0]
    assert record["derived_from"]["raw_chunk_id"] == "koshi-geopotential-500-2001"
    assert record["source_to_canonical"] == {"valid_time": "time", "z": "geopotential"}


@pytest.mark.parametrize(
    "option",
    [
        "non_jja",
        "duplicate",
        "partial",
        "bad_grid",
        "missing_level",
        "nonfinite",
        "unknown_variable",
        "unknown_coordinate",
        "mixed_expver",
    ],
)
def test_split_rejects_malformed_real_cds_payloads(tmp_path: Path, option: str) -> None:
    with pytest.raises(ValueError):
        _run_one(tmp_path, dataset=_dataset(**{option: True}))


def test_split_rejects_june_31_request_plan(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="June-31"):
        _run_one(tmp_path, invalid_june31=True)


def test_split_requires_complete_raw_plan_before_output(tmp_path: Path) -> None:
    root = tmp_path / "armc"
    root.mkdir()
    with pytest.raises(ValueError, match="expected payload lanes"):
        split_payloads(root, manifest_out=root / "split.json")
    assert not (root / "split.json").exists()


def test_split_refuses_existing_monthly_outputs(tmp_path: Path) -> None:
    root = _write_fixture(tmp_path)
    split_payloads(
        root,
        manifest_out=root / "split-first.json",
        _expected_lanes=("payload-geopotential",),
        _expected_entries_per_lane=1,
        _expected_raw_chunks=1,
    )
    with pytest.raises(ExistingEvidenceError):
        split_payloads(
            root,
            manifest_out=root / "split-second.json",
            _expected_lanes=("payload-geopotential",),
            _expected_entries_per_lane=1,
            _expected_raw_chunks=1,
        )
    # The first root's derived bytes remain intact; no replacement occurred.
    assert len(list((tmp_path / "armc/payload-geopotential/retrieval/payloads_monthly").glob("*.nc"))) == 3


def test_split_dry_run_does_not_publish(tmp_path: Path) -> None:
    root = _write_fixture(tmp_path)
    result = split_payloads(
        root,
        manifest_out=root / "split.json",
        dry_run=True,
        _expected_lanes=("payload-geopotential",),
        _expected_entries_per_lane=1,
        _expected_raw_chunks=1,
    )
    assert result["status"] == "SPLIT_PREFLIGHT_OK"
    assert not (root / "split.json").exists()
    assert not (root / "payload-geopotential/retrieval/payloads_monthly").exists()


def test_split_rejects_orphan_raw_files(tmp_path: Path) -> None:
    root = _write_fixture(tmp_path)
    extra = root / "payload-geopotential/retrieval/payloads/undeclared.nc"
    extra.write_bytes(b"undeclared")
    _sidecar(extra)
    with pytest.raises(ValueError, match="undeclared files"):
        split_payloads(
            root,
            manifest_out=root / "split.json",
            _expected_lanes=("payload-geopotential",),
            _expected_entries_per_lane=1,
            _expected_raw_chunks=1,
        )


def test_frame_split_manifest_resolver_uses_logical_relative_paths(tmp_path: Path) -> None:
    from scripts.armc_frame_extend import _split_chunk_map

    root = _write_fixture(tmp_path)
    result = split_payloads(
        root,
        manifest_out=root / "split.json",
        _expected_lanes=("payload-geopotential",),
        _expected_entries_per_lane=1,
        _expected_raw_chunks=1,
    )
    plan = [
        {
            "chunk_id": record["chunk_id"],
            "basin": record["basin"],
            "variable": record["variable"],
            "pressure_level_hpa": record["pressure_level_hpa"],
            "year": record["year"],
            "month": record["month"],
        }
        for record in result["chunks"]
    ]
    resolved = _split_chunk_map(
        root,
        root / "split.json",
        {"anchor_boxes": {"koshi": [28.2, 86.9, 27.9, 87.1]}},
        plan,
    )
    for record in result["chunks"]:
        assert resolved[record["chunk_id"]]["payload_sha256"] == record["payload_sha256"]
