"""Focused tests for metadata-only Arm C CDS preflight."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

from scripts.armc_cds_preflight import preflight


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/armc_cds_preflight.py"


def _amendment() -> dict:
    variables = []
    for name, units in (
        ("geopotential", "m2 s-2"),
        ("specific_humidity", "kg kg-1"),
        ("temperature", "K"),
        ("vertical_velocity", "Pa s-1"),
    ):
        variables.append({
            "name": name,
            "cds_param": name,
            "level_hpa": [500, 700],
            "units": units,
        })
    return {
        "schema": "P5_AMENDMENT_V8_ARM_C_SCOPE",
        "approval_state": "SCOPE_APPROVED_RETRIEVAL_DEFERRED",
        "approved_by": "owner:fixture",
        "claim_scope": "research_only_no_operational_authorization",
        "cds_request_contract": {
            "dataset": "reanalysis-era5-pressure-levels",
            "format": "netcdf",
            "product_type": "reanalysis",
            "area_bbox_nwse": [31.0, 80.0, 26.0, 89.0],
            "temporal_range": "2001-06-01..2025-08-31",
            "temporal_aggregation": (
                "hourly CDS fields -> daily means -> basin-area means -> "
                "basin-year JJA seasonal aggregates"
            ),
            "variables": variables,
            "max_download_bytes": 5 * 1024**3,
            "max_new_seasonal_features": 6,
        },
    }


def _metadata() -> dict:
    return {
        "dataset": "reanalysis-era5-pressure-levels",
        "formats": ["netcdf"],
        "available_variables": {
            "geopotential": [500, 700],
            "specific_humidity": [500, 700],
            "temperature": [500, 700],
            "vertical_velocity": [500, 700],
        },
        "available_from": "1940-01-01",
        "available_to": "2026-12-31",
        "grid_resolution_deg": 0.25,
    }


def test_missing_grid_and_catalogue_are_explicitly_blocked() -> None:
    result = preflight(_amendment())
    assert result["status"] == "PREFLIGHT_BLOCKED"
    assert result["acquisition"]["payload_bytes_retrieved"] == 0
    assert result["acquisition"]["writes_performed"] is False
    assert any("grid_resolution_deg" in problem for problem in result["problems"])
    assert any("metadata" in problem for problem in result["problems"])
    assert result["estimate"]["fits_cap_under_estimate"] is True


def test_complete_offline_metadata_fixture_can_pass_without_network() -> None:
    amendment = _amendment()
    amendment["cds_request_contract"]["grid_resolution_deg"] = 0.25
    result = preflight(amendment, _metadata())
    assert result["status"] == "PREFLIGHT_OK"
    assert result["coverage"]["status"] == "METADATA_OK"
    assert result["acquisition"]["network_calls"] == 0
    assert result["acquisition"]["payload_bytes_retrieved"] == 0


def test_variable_substitution_fails_closed() -> None:
    amendment = _amendment()
    amendment["cds_request_contract"]["variables"][0]["name"] = "total_precipitation"
    result = preflight(amendment, _metadata())
    assert result["status"] == "PREFLIGHT_BLOCKED"
    assert any("frozen four" in problem for problem in result["problems"])


def test_write_option_is_permanently_refused_without_reading_or_writing(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--amendment", str(tmp_path / "missing.json"), "--write"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "WRITE_FORBIDDEN" in result.stdout
    assert not list(tmp_path.iterdir())


def test_bad_metadata_date_coverage_is_not_silently_accepted() -> None:
    metadata = _metadata()
    metadata["available_to"] = "2024-12-31"
    amendment = _amendment()
    amendment["cds_request_contract"]["grid_resolution_deg"] = 0.25
    result = preflight(amendment, metadata)
    assert result["status"] == "PREFLIGHT_BLOCKED"
    assert result["coverage"]["status"] == "METADATA_MISMATCH"
    assert any("date range" in problem for problem in result["problems"])


def test_metadata_grid_mismatch_is_not_hidden(tmp_path: Path) -> None:
    amendment = _amendment()
    amendment["cds_request_contract"]["grid_resolution_deg"] = 0.25
    metadata = _metadata()
    metadata["grid_resolution_deg"] = 0.5
    result = preflight(amendment, metadata)
    assert result["status"] == "PREFLIGHT_BLOCKED"
    assert any("grid resolution differs" in problem for problem in result["problems"])
