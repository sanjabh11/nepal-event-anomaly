from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scripts.armc_cds_retrieve import (
    EXPECTED_YEARS,
    _validate_process_metadata,
    build_request_plan,
    estimate_plan,
    load_contract,
    retrieve_plan,
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


def _process_metadata(contract: dict) -> bytes:
    return json.dumps(
        {
            "id": "reanalysis-era5-pressure-levels",
            "version": "1.0.0",
            "inputs": {
                "variable": {"schema": {"items": {"enum": [item["name"] for item in contract["variables"]]}}},
                "pressure_level": {"schema": {"items": {"enum": ["500", "700"]}}},
                "year": {"schema": {"items": {"enum": [str(year) for year in EXPECTED_YEARS]}}},
                "month": {"schema": {"items": {"enum": ["06", "07", "08"]}}},
                "time": {"schema": {"items": {"enum": list(__import__("scripts.armc_cds_retrieve", fromlist=["HOURS"]).HOURS)}}},
                "area": {"schema": {"minItems": 4}},
                "data_format": {"schema": {"enum": ["grib", "netcdf"]}},
                "download_format": {"schema": {"enum": ["zip", "unarchived"]}},
            },
        },
        sort_keys=True,
    ).encode()


def test_request_plan_reconciles_v10_anchor_envelopes_and_calendar_days(tmp_path: Path) -> None:
    amendment = _amendments(tmp_path)
    contract = load_contract(amendment)
    plan = build_request_plan(contract)
    assert len(plan) == 3 * 4 * 2 * 25 * 3
    assert {tuple(item["area_nwse"]) for item in plan} == {
        (29.0, 84.5, 28.25, 85.0),
        (30.0, 81.75, 29.5, 82.5),
        (28.25, 86.75, 27.75, 87.25),
    }
    for item in plan:
        request = item["request"]
        assert request["month"] == [f"{item['month']:02d}"]
        assert len(request["day"]) in (30, 31)
        assert request["data_format"] == "netcdf"
        assert request["download_format"] == "unarchived"
        assert item["expected_complete_cells"] >= 9
    assert estimate_plan(plan, contract["max_download_bytes"])["fits_cap"] is True


def test_process_metadata_contract_is_checked_without_network(tmp_path: Path) -> None:
    contract = load_contract(_amendments(tmp_path))
    metadata, problems = _validate_process_metadata(_process_metadata(contract), contract)
    assert not problems
    assert metadata["status"] == "METADATA_OK"
    assert metadata["process_version"] == "1.0.0"


def test_retrieve_plan_uses_injected_client_and_exclusive_sidecars(tmp_path: Path) -> None:
    contract = load_contract(_amendments(tmp_path))
    plan = build_request_plan(contract)[:1]

    class FakeClient:
        def retrieve(self, dataset, request, target):
            assert dataset == contract["dataset"]
            assert request["data_format"] == "netcdf"
            Path(target).write_bytes(b"synthetic-netcdf-payload")

    root = tmp_path / "armc-root"
    result = retrieve_plan(contract, plan, root, client=FakeClient(), max_bytes=1024)
    assert result["completed_chunks"] == 1
    payload = root / "retrieval/payloads" / f"{plan[0]['chunk_id']}.nc"
    request = root / "retrieval/requests" / f"{plan[0]['chunk_id']}.json"
    assert payload.exists() and Path(str(payload) + ".sha256").exists()
    assert request.exists() and Path(str(request) + ".sha256").exists()
    with pytest.raises(FileExistsError):
        retrieve_plan(contract, plan, root, client=FakeClient(), max_bytes=1024)


def test_retrieve_plan_stops_when_actual_bytes_exceed_cap(tmp_path: Path) -> None:
    contract = load_contract(_amendments(tmp_path))
    plan = build_request_plan(contract)[:1]

    class FakeClient:
        def retrieve(self, dataset, request, target):
            Path(target).write_bytes(b"0123456789")

    with pytest.raises(ValueError, match="exceed cap"):
        retrieve_plan(contract, plan, tmp_path / "armc-root", client=FakeClient(), max_bytes=5)
