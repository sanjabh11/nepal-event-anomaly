from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from scripts.armc_cds_retrieve import load_contract
from scripts.armc_frame_extend import (
    FEATURE_SCHEMA,
    _expected_times,
    build_extended_frame,
    validate_feature_contract,
    validate_payload_dataset,
)


EVIDENCE = Path("/Users/sanjayb/nepal-event-anomaly-evidence/p5-glof-2026-09-19/retrieval")


def _amendments(tmp_path: Path) -> Path:
    for name in (
        "p5_amendment_v8_arm_c_scope.json",
        "p5_amendment_v9_arm_c_grid_provenance.json",
        "p5_amendment_v10_arm_c_spatial_correction.json",
    ):
        shutil.copy2(EVIDENCE / name, tmp_path / name)
    return tmp_path / "p5_amendment_v10_arm_c_spatial_correction.json"


def _chunk() -> dict:
    return {
        "chunk_id": "koshi-geopotential-500-2001-06",
        "basin": "koshi",
        "variable": "geopotential",
        "pressure_level_hpa": 500,
        "year": 2001,
        "month": 6,
        "area_nwse": [28.25, 86.75, 27.75, 87.25],
        "anchor_box_nwse": [28.2, 86.9, 27.9, 87.1],
    }


def _dataset(*, partial: bool = False, bad_grid: bool = False) -> xr.Dataset:
    times = _expected_times(2001, 6)
    if partial:
        times = times[:-1]
    lat = [28.25, 28.0, 27.75]
    lon = [86.75, 87.0, 87.25]
    if bad_grid:
        lon[-1] = 87.5
    values = np.ones((len(times), 2, len(lat), len(lon)), dtype=np.float64)
    values[:, 0, 1, 1] = 2.0
    return xr.Dataset(
        {"geopotential": (("time", "pressure_level", "latitude", "longitude"), values)},
        coords={"time": times.tz_localize(None), "pressure_level": [500, 700], "latitude": lat, "longitude": lon},
    )


def test_synthetic_netcdf_chunk_uses_only_complete_anchor_cells(tmp_path: Path) -> None:
    path = tmp_path / "chunk.nc"
    _dataset().to_netcdf(path)
    with xr.open_dataset(path, engine="netcdf4") as ds:
        result = validate_payload_dataset(ds, _chunk())
    assert len(result["time"]) == 30 * 24
    assert result["complete_cell_count"] == 1
    assert result["grid_cells_used"] == {"latitude": [28.0], "longitude": [87.0]}
    assert np.all(result["values"] == 2.0)


def test_partial_month_and_off_grid_payloads_fail_closed(tmp_path: Path) -> None:
    partial = tmp_path / "partial.nc"
    _dataset(partial=True).to_netcdf(partial)
    with xr.open_dataset(partial, engine="netcdf4") as ds:
        with pytest.raises(ValueError, match="partial or non-UTC"):
            validate_payload_dataset(ds, _chunk())
    bad = tmp_path / "bad.nc"
    _dataset(bad_grid=True).to_netcdf(bad)
    with xr.open_dataset(bad, engine="netcdf4") as ds:
        with pytest.raises(ValueError, match="longitude axis"):
            validate_payload_dataset(ds, _chunk())


def test_feature_contract_requires_explicit_units_and_six_feature_ceiling() -> None:
    value = {
        "schema": FEATURE_SCHEMA,
        "claim_scope": "research_only_no_operational_authorization",
        "base_feature_cols": ["t2m_mean"],
        "extended_features": [{"name": "z500_mean", "source_variable": "geopotential", "pressure_level_hpa": 500, "aggregation": "jja_mean"}],
        "feature_cols": ["t2m_mean", "z500_mean"],
        "feature_units": {"t2m_mean": "degC", "z500_mean": "m"},
        "collinearity_threshold": 0.95,
    }
    assert validate_feature_contract(value)["feature_cols"] == ["t2m_mean", "z500_mean"]
    value["feature_units"] = {}
    with pytest.raises(ValueError, match="feature_units"):
        validate_feature_contract(value)


def test_extended_frame_refuses_non_75_row_base_before_payload_access(tmp_path: Path) -> None:
    amendment = _amendments(tmp_path)
    base = tmp_path / "base.csv"
    pd.DataFrame([{"unit_id": "koshi", "season_year": 2001, "date": "2001-07-16", "basin_group": "koshi", "season": "JJA", "t2m_mean": 1.0}]).to_csv(base, index=False)
    base.write_bytes(base.read_bytes())
    base_digest = __import__("hashlib").sha256(base.read_bytes()).hexdigest()
    Path(str(base) + ".sha256").write_text(f"{base_digest}  {base.name}\n")
    contract_path = tmp_path / "feature_contract.json"
    contract_path.write_text(json.dumps({
        "schema": FEATURE_SCHEMA,
        "claim_scope": "research_only_no_operational_authorization",
        "base_feature_cols": ["t2m_mean"],
        "extended_features": [{"name": "z500_mean", "source_variable": "geopotential", "pressure_level_hpa": 500, "aggregation": "jja_mean"}],
        "feature_cols": ["t2m_mean", "z500_mean"],
        "feature_units": {"t2m_mean": "degC", "z500_mean": "m"},
        "collinearity_threshold": 0.95,
    }))
    with pytest.raises(ValueError, match="exactly 75"):
        build_extended_frame(
            amendment_v10=amendment,
            base_frame=base,
            feature_contract_path=contract_path,
            payload_root=tmp_path / "missing-payload-root",
            output_dir=tmp_path / "output",
        )
