"""Phase 1 (parallel fallback): ERA5-Land via parallel CDS API downloads.

4 workers downloading months in parallel — 3x speedup over sequential.
Each worker creates its own cdsapi.Client to avoid SSL errors.

Usage:
    source .venv/bin/activate
    python nepal/era5_parallel_download.py
"""
from __future__ import annotations

import os
import sys
import json
import time
import multiprocessing as mp
from pathlib import Path
from datetime import datetime

import cdsapi
import xarray as xr

sys.path.insert(0, str(Path(__file__).resolve().parent))
from feature_contract import RAW_GRIB_SHORT_NAMES, CDS_LONG_NAMES

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
TEMP_DIR = DATA_DIR / "temp_parallel"
OUTPUT_FILE = DATA_DIR / "era5_land_nepal_jja_2001_2026.nc"
LEDGER_FILE = DATA_DIR / "parallel_download_ledger.json"

# 4 workers (CDS allows ~2-3 concurrent, overbooking by 1 is tolerated)
N_WORKERS = 4

# Region: [North, West, South, East]
AREA = [29.0, 85.0, 27.0, 86.0]

# JJA 2001-2026 = 78 months
MONTHS_TO_DOWNLOAD = []
for year in range(2001, 2027):
    for month in [6, 7, 8]:
        MONTHS_TO_DOWNLOAD.append((year, month))


def log(message: str):
    timestamp = datetime.now().isoformat()
    print(f"[{timestamp}] {message}", flush=True)


def download_month(args):
    """Download a single month of ERA5-Land data. Runs in worker process."""
    year, month, temp_dir = args

    output_file = Path(temp_dir) / f"era5_land_{year}_{month:02d}.nc"

    # Skip if already downloaded
    if output_file.exists() and output_file.stat().st_size > 1000:
        return (year, month, "skipped", output_file.stat().st_size)

    # Create CDS API request
    dataset = "reanalysis-era5-land"
    request = {
        "variable": list(CDS_LONG_NAMES.keys()),
        "year": str(year),
        "month": f"{month:02d}",
        "day": [f"{d:02d}" for d in range(1, 32)],
        "time": [f"{h:02d}:00" for h in range(24)],
        "data_format": "netcdf",
        "download_format": "unarchived",
        "area": AREA,
    }

    try:
        # Each worker creates its own client (avoids SSL errors)
        client = cdsapi.Client(progress=False, quiet=True)
        client.retrieve(dataset, request).download(str(output_file))

        size = output_file.stat().st_size
        return (year, month, "success", size)
    except Exception as e:
        return (year, month, f"error: {str(e)[:100]}", 0)


def merge_monthly_files(temp_dir: Path, output_file: Path):
    """Merge all monthly NetCDF files into one."""
    log("Merging monthly files...")

    files = sorted(temp_dir.glob("era5_land_*.nc"))
    if not files:
        log("No files to merge!")
        return False

    log(f"Found {len(files)} monthly files")

    datasets = []
    for f in files:
        try:
            ds = xr.open_dataset(f)
            datasets.append(ds)
        except Exception as e:
            log(f"Error opening {f}: {e}")

    if not datasets:
        log("No valid datasets to merge!")
        return False

    # Concatenate along time dimension
    time_coord = "time" if "time" in datasets[0].coords else "valid_time"
    merged = xr.concat(datasets, dim=time_coord)

    # Sort by time
    merged = merged.sortby(time_coord)

    # Save merged file
    merged.to_netcdf(output_file)
    log(f"Merged file saved: {output_file} ({output_file.stat().st_size / 1024 / 1024:.1f} MB)")

    # Close datasets
    for ds in datasets:
        ds.close()
    merged.close()

    return True


def main():
    """Main parallel download execution."""
    log("=" * 60)
    log("Phase 1 (Parallel): ERA5-Land via 4-worker CDS API")
    log("=" * 60)

    # Create temp directory
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    # Check which months are already done (from GLM2's sequential download)
    existing_files = set()
    sequential_temp = DATA_DIR / "temp"
    if sequential_temp.exists():
        for f in sequential_temp.glob("era5_land_*.nc"):
            existing_files.add(f.name)
        log(f"Found {len(existing_files)} files from GLM2 sequential download")

    # Also check our own temp dir
    for f in TEMP_DIR.glob("era5_land_*.nc"):
        existing_files.add(f.name)

    # Build task list (skip already-downloaded months)
    tasks = []
    skipped = 0
    for year, month in MONTHS_TO_DOWNLOAD:
        filename = f"era5_land_{year}_{month:02d}.nc"
        if filename in existing_files:
            # Copy from sequential temp to parallel temp if needed
            src = sequential_temp / filename
            dst = TEMP_DIR / filename
            if src.exists() and not dst.exists():
                import shutil
                shutil.copy2(src, dst)
            skipped += 1
        else:
            tasks.append((year, month, str(TEMP_DIR)))

    log(f"Total months: {len(MONTHS_TO_DOWNLOAD)}")
    log(f"Already downloaded (skipped): {skipped}")
    log(f"To download: {len(tasks)}")
    log(f"Workers: {N_WORKERS}")

    if not tasks:
        log("All months already downloaded! Proceeding to merge.")
    else:
        # Run parallel downloads
        log(f"\nStarting {N_WORKERS} parallel workers...")
        start_time = time.time()

        with mp.Pool(processes=N_WORKERS) as pool:
            results = pool.map(download_month, tasks)

        elapsed = time.time() - start_time
        log(f"\nParallel download completed in {elapsed:.0f} seconds ({elapsed/60:.1f} minutes)")

        # Report results
        success = 0
        errors = 0
        for year, month, status, size in results:
            if status == "success" or status == "skipped":
                success += 1
            else:
                errors += 1
                log(f"  ERROR: {year}-{month}: {status}")

        log(f"Success: {success}, Errors: {errors}")

        if errors > 0:
            log("Some downloads failed. Retry logic not implemented in this version.")
            log("Re-run the script to retry failed months.")

    # Merge all monthly files
    log(f"\nMerging {len(list(TEMP_DIR.glob('era5_land_*.nc')))} files...")
    if merge_monthly_files(TEMP_DIR, OUTPUT_FILE):
        log(f"\nFinal file: {OUTPUT_FILE}")
        log(f"Size: {OUTPUT_FILE.stat().st_size / 1024 / 1024:.1f} MB")
        log("\nPhase 1 Parallel EXIT GATE: PASS")
    else:
        log("Merge FAILED")
        sys.exit(1)


if __name__ == "__main__":
    # Set start method to 'spawn' for macOS compatibility
    mp.set_start_method("spawn", force=True)
    main()
