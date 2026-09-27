"""Phase 1 Exit Gate Verification.

Validates all Phase 1 data acquisition workstreams:
  A: ERA5-Land JJA hourly data (required)
  B: NISAR catalog ledger (required)
  C: Copernicus DEM tile (required)
  D: Hausfather Langtang reference (optional)

Usage:
    source .venv/bin/activate
    python nepal/phase1_exit_gate.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def check_era5_land() -> dict:
    """Workstream A: ERA5-Land dataset."""
    nc_path = DATA_DIR / "era5_land_nepal_jja_2001_2026.nc"
    if not nc_path.exists():
        return {"name": "ERA5-Land", "status": "MISSING", "detail": f"{nc_path} not found"}

    try:
        import xarray as xr
        ds = xr.open_dataset(nc_path)
        n_vars = len(ds.data_vars)
        var_names = list(ds.data_vars)
        time_dim = "time" if "time" in ds.dims else "valid_time" if "valid_time" in ds.dims else None
        n_time = ds.sizes.get(time_dim, 0) if time_dim else 0
        size_mb = nc_path.stat().st_size / (1024 * 1024)

        return {
            "name": "ERA5-Land",
            "status": "PASS",
            "detail": f"{n_vars} vars, {n_time} time steps, {size_mb:.1f} MB",
            "variables": var_names,
            "time_steps": n_time,
            "size_mb": round(size_mb, 1),
        }
    except Exception as e:
        return {"name": "ERA5-Land", "status": "ERROR", "detail": str(e)}


def check_nisar_ledger() -> dict:
    """Workstream B: NISAR catalog ledger."""
    ledger_path = DATA_DIR / "nisar_catalog_ledger.json"
    if not ledger_path.exists():
        return {"name": "NISAR", "status": "MISSING", "detail": f"{ledger_path} not found"}

    try:
        with open(ledger_path) as f:
            ledger = json.load(f)
        n_pre = len(ledger.get("pre_event_granules", []))
        n_post = len(ledger.get("post_event_granules", []))
        n_traps = sum(1 for g in ledger.get("pre_event_granules", []) if g.get("latency_trap"))

        if n_pre == 0:
            return {"name": "NISAR", "status": "FAIL", "detail": "No pre-event granules"}

        return {
            "name": "NISAR",
            "status": "PASS",
            "detail": f"{n_pre} pre-event, {n_post} post-event, {n_traps} latency traps",
            "pre_event": n_pre,
            "post_event": n_post,
            "latency_traps": n_traps,
        }
    except Exception as e:
        return {"name": "NISAR", "status": "ERROR", "detail": str(e)}


def check_dem() -> dict:
    """Workstream C: Copernicus DEM tile."""
    dem_path = DATA_DIR / "dem_n28e085.tif"
    if not dem_path.exists():
        return {"name": "DEM", "status": "MISSING", "detail": f"{dem_path} not found"}

    try:
        with open(dem_path, "rb") as f:
            magic = f.read(4)
        is_tiff = (magic[:2] == b"II" and magic[2:4] == b"*\x00") or \
                  (magic[:2] == b"MM" and magic[2:4] == b"\x00*")
        size_mb = dem_path.stat().st_size / (1024 * 1024)

        if not is_tiff:
            return {"name": "DEM", "status": "FAIL", "detail": f"Not a valid TIFF ({size_mb:.1f} MB)"}

        return {
            "name": "DEM",
            "status": "PASS",
            "detail": f"Valid TIFF, {size_mb:.1f} MB",
            "size_mb": round(size_mb, 1),
        }
    except Exception as e:
        return {"name": "DEM", "status": "ERROR", "detail": str(e)}


def check_hausfather() -> dict:
    """Workstream D: Hausfather Langtang reference (optional)."""
    ref_path = DATA_DIR / "hausfath_reference" / "langtang_t2m_daily_2026.nc"
    if not ref_path.exists():
        return {"name": "Hausfather", "status": "OPTIONAL_MISSING", "detail": "Optional reference not found"}

    try:
        import xarray as xr
        ds = xr.open_dataset(ref_path)
        n_vars = len(ds.data_vars)
        size_kb = ref_path.stat().st_size / 1024

        return {
            "name": "Hausfather",
            "status": "PASS",
            "detail": f"{n_vars} vars, {size_kb:.0f} KB",
            "variables": list(ds.data_vars),
        }
    except Exception as e:
        return {"name": "Hausfather", "status": "ERROR", "detail": str(e)}


def main():
    print("=" * 60)
    print("Phase 1 Exit Gate Verification")
    print("=" * 60)
    print()

    checks = [
        check_era5_land(),
        check_nisar_ledger(),
        check_dem(),
        check_hausfather(),
    ]

    for c in checks:
        status_marker = {
            "PASS": "[PASS]",
            "FAIL": "[FAIL]",
            "MISSING": "[MISSING]",
            "ERROR": "[ERROR]",
            "OPTIONAL_MISSING": "[OPTIONAL]",
        }.get(c["status"], "[?]")
        print(f"  {status_marker} {c['name']}: {c['detail']}")

    print()
    required = ["ERA5-Land", "NISAR", "DEM"]
    all_required_pass = all(
        any(r == c["name"] and c["status"] == "PASS" for c in checks)
        for r in required
    )

    hausfath = next((c for c in checks if c["name"] == "Hausfather"), None)
    hausfath_note = ""
    if hausfath and hausfath["status"] == "PASS":
        hausfath_note = " (Hausfather: present)"
    elif hausfath:
        hausfath_note = f" (Hausfather: {hausfath['status']})"

    result = "PASS" if all_required_pass else "FAIL"
    print(f"PHASE 1 EXIT GATE: {result}{hausfath_note}")

    if not all_required_pass:
        sys.exit(1)


if __name__ == "__main__":
    main()
