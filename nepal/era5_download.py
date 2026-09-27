"""ERA5-Land download for Nepal Event Anomaly Assessment — Phase 1.

Downloads ERA5-Land hourly data for JJA (Jun-Aug) 2001-2026 for the
nearest grid cell to the Langtang Lirung source zone.

This is a MINIMAL, Nepal-specific download — NOT the multi-winter
Pir Panjal acquisition. It requests only 7 variables (the pre-registered
feature contract) for JJA months only, for a small area around the event.

Strategy:
- Request JJA (June, July, August) for years 2001-2026
- 7 variables only (pre-registered feature contract); snow is requested
  as snow_depth_water_equivalent so the payload carries 'sd' (SWE),
  not 'sde' (geometric depth)
- Small area: 1° × 1° around the event point (28.25°N, 85.50°E);
  enforced by assert_area_contract()
- Sequential with retry (CDS queue can be slow)
- All outputs under --run-root; the frozen data/ tree is never written

Event cutoff: nothing on/after 2026-08-26 is requested. For 2026 only
June, July and August 1-25 contain pre-cutoff dates.

Usage:
    source .venv/bin/activate
    python nepal/era5_download.py [--dry-run] [--year-range 2001-2026] \
        [--run-root research_runs/gmm_confirmation] [--force] [--smoke]

Output (under --run-root):
    raw/era5_land_{year}_{month}.nc          raw CDS payloads (zip or netcdf)
    monthly/era5_land_{year}_{month}.nc      normalized monthly files
    merged/era5_land_nepal_jja_2001_2026.nc  merged event-cell time series
    download_ledger.json                     provenance + completeness ledger
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import shutil
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

# --- Configuration (from feature_contract.py) ---

# CDS dataset
CDS_DATASET = "reanalysis-era5-land"

# Pre-registered variables (7 raw ERA5-Land variables)
# Using CDS long names (Astra correction: GRIB short names are NOT CDS
# request names). "snow_depth_water_equivalent" delivers 'sd' (SWE);
# plain "snow_depth" would deliver 'sde' (geometric depth), which the
# contract rejects.
CDS_VARIABLES = [
    "2m_temperature",
    "2m_dewpoint_temperature",
    "10m_u_component_of_wind",
    "10m_v_component_of_wind",
    "snow_depth_water_equivalent",
    "snowfall",
    "total_precipitation",
]

# Required data variables in a normalized payload (GRIB short names)
REQUIRED_DATA_VARS = ("t2m", "d2m", "u10", "v10", "sd", "sf", "tp")
REQUIRED_DIMS = ("latitude", "longitude", "time")
# 'sde' = geometric snow depth — a wrong-variable payload must be rejected
FORBIDDEN_DATA_VARS = ("sde",)

# Event location (Hausfather's nearest cell)
EVENT_LAT = 28.25
EVENT_LON = 85.50

# Event cutoff: nothing on/after 2026-08-26 (pre-registration).
# For the event year, August is truncated to days 1-25.
EVENT_YEAR = 2026
EVENT_CUTOFF_DAY = 25  # last requestable day of 2026-08
# First instant on/after the event cutoff — a payload carrying any
# timestamp >= this bound is rejected outright (P5-07).
EVENT_CUTOFF_ISO = f"{EVENT_YEAR}-08-{EVENT_CUTOFF_DAY + 1:02d}T00:00"

# Download area: small box around the event point
# CDS area format: [North, West, South, East]
# 1° × 1° box: ~111 km × ~98 km — covers the source and immediate context
AREA = [29.0, 85.0, 28.0, 86.0]


def assert_area_contract() -> None:
    """AUD-02 area contract guard.

    AREA is [North, West, South, East]; the documented contract is a
    1° × 1° box. Verify the longitude width (east - west) and latitude
    height (north - south) are each 1.0 within float tolerance.
    Called in main() before any CDS request is built and referenced in
    the dry-run output.
    """
    north, west, south, east = (float(v) for v in AREA)
    width = east - west
    height = north - south
    tol = 1e-9
    if abs(width - 1.0) > tol or abs(height - 1.0) > tol:
        raise AssertionError(
            f"AREA contract violated: expected a 1° × 1° box "
            f"([North, West, South, East]); got {height}° lat × "
            f"{width}° lon from AREA={AREA}")

# JJA months
JJA_MONTHS = ["06", "07", "08"]

# Years: 2001-2026 (25-year baseline + event year)
YEAR_RANGE = list(range(2001, 2027))
# Inclusive bounds enforced by the --year-range parser (P5-02)
MIN_YEAR = YEAR_RANGE[0]
MAX_YEAR = YEAR_RANGE[-1]

# All 24 hours
HOURS = [f"{h:02d}:00" for h in range(24)]

# Output layout (under --run-root; shared layout contract — the same
# top-level run root is used by the downloader, feature extractor and
# GMM stages: raw/, monthly/, merged/, features/, gmm/)
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUN_ROOT = REPO_ROOT / "research_runs" / "gmm_confirmation"
MERGED_NAME = "era5_land_nepal_jja_2001_2026.nc"
MERGED_MARKER_NAME = "complete.json"
LEDGER_NAME = "download_ledger.json"

# Minimum free disk space required before/while acquiring payloads
# (P5-10). Below this reserve the run stops requesting months.
DISK_MIN_FREE_GIB = 8.0

# Frozen paths — a run root may never resolve inside these
FROZEN_DIRS = (
    REPO_ROOT / "data",
    REPO_ROOT / "pinned",
    REPO_ROOT / "nepal" / "framework_v1",
)
FROZEN_FILES = (REPO_ROOT / "preregistration.md",)


def ensure_not_frozen(path: Path | str, what: str = "path") -> Path:
    """Reject any path that resolves inside the frozen tree.

    The frozen tree is the data/, pinned/ and nepal/framework_v1/
    directories plus preregistration.md. Returns the resolved path.
    Called by resolve_run_root and by every write-capable entry point
    so direct (non-CLI) calls get the same frozen-path rejection as
    main() (P5-11).
    """
    p = Path(path).expanduser().resolve()

    for frozen in FROZEN_DIRS:
        if p == frozen or frozen in p.parents:
            raise ValueError(
                f"{what} {p} resolves inside frozen path {frozen}; "
                f"choose a location outside data/, pinned/ and "
                f"nepal/framework_v1/")
    for frozen in FROZEN_FILES:
        if p == frozen:
            raise ValueError(
                f"{what} {p} resolves onto frozen file {frozen}")
    return p


def check_disk_reserve(path: Path | str,
                       min_free_gib: float = DISK_MIN_FREE_GIB
                       ) -> tuple[bool, float]:
    """P5-10 disk reserve guard.

    Returns ``(ok, free_gib)`` where ``ok`` is True when the filesystem
    containing ``path`` has at least ``min_free_gib`` GiB free. Probes
    the nearest existing ancestor so it works for not-yet-created
    output paths.
    """
    probe = Path(path)
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    free_gib = shutil.disk_usage(probe).free / (1024 ** 3)
    ok = free_gib >= min_free_gib
    if not ok:
        print(f"  DISK RESERVE LOW: {free_gib:.2f} GiB free at {probe} "
              f"(< {min_free_gib} GiB required)")
    return ok, round(free_gib, 2)


def resolve_run_root(arg: str | None, force: bool = False) -> Path:
    """Resolve and validate the run root.

    Rejects any path that resolves inside the frozen tree (data/,
    pinned/, nepal/framework_v1/) or onto preregistration.md.

    Fresh-run semantics: if the merged output already exists under the
    run root, either --force reuses the directory (overwriting outputs)
    or a new timestamped sibling directory is created. There is no
    resume support.
    """
    run_root = ensure_not_frozen(
        arg if arg else DEFAULT_RUN_ROOT, "run_root")

    merged_out = run_root / "merged" / MERGED_NAME
    if merged_out.exists() and not force:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        fresh = run_root.parent / f"{run_root.name}-{stamp}"
        print(f"Merged output already exists under {run_root}; "
              f"using fresh run dir {fresh} (or pass --force to overwrite)")
        run_root = fresh

    return run_root


def months_for_year(year: int) -> list[str]:
    """JJA months with at least one requestable (pre-cutoff) day.

    For the event year 2026 all three JJA months still qualify: June and
    July are complete, August contributes days 1-25. Years after 2026
    have no requestable dates.
    """
    if year > EVENT_YEAR:
        return []
    return list(JJA_MONTHS)


def days_for_month(year: int, month: str) -> list[str]:
    """Valid calendar days for (year, month) via calendar.monthrange.

    For 2026-08 the list is capped at day 25 (event cutoff 2026-08-26 —
    no event-day or post-event data).
    """
    n_days = calendar.monthrange(year, int(month))[1]
    if year == EVENT_YEAR and month == "08":
        n_days = min(n_days, EVENT_CUTOFF_DAY)
    return [f"{d:02d}" for d in range(1, n_days + 1)]


def build_request(year: int, month: str) -> dict:
    """Build a CDS API request for one month."""
    return {
        "variable": CDS_VARIABLES,
        "year": str(year),
        "month": month,
        "day": days_for_month(year, month),
        "time": HOURS,
        "area": AREA,
        "data_format": "netcdf",
    }


def download_month(client, year: int, month: str,
                   output_path: Path, max_retries: int = 3) -> bool:
    """Download one month of ERA5-Land data with retry."""
    request = build_request(year, month)

    for attempt in range(max_retries):
        try:
            print(f"  Requesting {year}-{month} (attempt {attempt + 1}/{max_retries})...")
            client.retrieve(
                CDS_DATASET,
                request,
                str(output_path),
            )
            size_mb = output_path.stat().st_size / (1024 * 1024)
            print(f"  Downloaded {year}-{month}: {size_mb:.1f} MB")
            return True
        except Exception as e:
            wait = 10 * (2 ** attempt)
            print(f"  Error for {year}-{month}: {e}")
            print(f"  Waiting {wait}s before retry...")
            time.sleep(wait)

    print(f"  FAILED after {max_retries} retries: {year}-{month}")
    return False


def detect_payload_format(path: Path) -> str:
    """Classify a downloaded payload by magic bytes.

    Returns "zip" (CDS zip wrapper, PK\x03\x04), "hdf5" (NetCDF-4,
    \\x89HDF) or "netcdf_classic" (CDF). Raises ValueError otherwise.
    """
    with open(path, "rb") as f:
        head = f.read(8)
    if head[:4] == b"PK\x03\x04":
        return "zip"
    if head[:4] == b"\x89HDF":
        return "hdf5"
    if head[:3] == b"CDF":
        return "netcdf_classic"
    raise ValueError(
        f"Unrecognized payload format for {path} (magic bytes {head!r})")


def normalize_payload(path: Path | str,
                      monthly_dir: Path | None = None,
                      year: int | None = None,
                      month: str | None = None,
                      ledger_entry: dict | None = None) -> Path:
    """Normalize a raw CDS payload into the canonical monthly layout.

    - Detect the payload format by magic bytes. For zip payloads, require
      exactly one *.nc member and extract only that member to
      <name>.norm.nc beside the payload (never unzip blindly). Raw
      NetCDF (classic or HDF5) payloads are used as-is.
    - Canonicalize the time coordinate: rename valid_time -> time,
      require dims (latitude, longitude, time), require the 7 contract
      data variables (t2m, d2m, u10, v10, sd, sf, tp), and reject the
      payload outright if 'sde' is present or 'sd' is missing.
    - Sort the time axis and validate the timestamp set is EXACTLY the
      expected UTC hourly set for the requested year/month (P5-07):
      first valid day 00:00 through last valid day 23:00, hourly
      contiguous, count = days*24. Payloads with wrong-month,
      out-of-range, duplicate or post-cutoff (>= 2026-08-26T00:00Z)
      timestamps are rejected, not repaired.
    - Require every required data variable (t2m, d2m, u10, v10, sd, sf,
      tp) to be all-finite across the whole array (P5-07): a single
      NaN or inf rejects the payload.
    - Write monthly_dir/era5_land_{year}_{month}.nc and return its path.

    If ledger_entry is given, payload_format and payload_sha256 (of the
    raw payload) are recorded into it.
    """
    import xarray as xr

    path = Path(path)
    fmt = detect_payload_format(path)
    payload_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()

    if fmt == "zip":
        with zipfile.ZipFile(path) as zf:
            nc_members = [n for n in zf.namelist() if n.endswith(".nc")]
            if len(nc_members) != 1:
                raise ValueError(
                    f"{path}: zip payload must contain exactly one .nc "
                    f"member, found {len(nc_members)}: {nc_members}")
            source = path.parent / f"{path.stem}.norm.nc"
            ensure_not_frozen(source, "zip extraction target")
            source.write_bytes(zf.read(nc_members[0]))
    else:
        source = path

    # Infer year/month from the era5_land_{year}_{month} filename if the
    # caller did not pass them explicitly.
    if year is None or month is None:
        parts = path.stem.split("_")
        if len(parts) >= 4 and parts[-2].isdigit():
            year = year if year is not None else int(parts[-2])
            month = month if month is not None else parts[-1]
    if year is None or month is None:
        raise ValueError(
            f"{path}: cannot infer year/month from filename; "
            f"pass them explicitly")
    year = int(year)
    month = f"{int(month):02d}"
    if monthly_dir is None:
        monthly_dir = path.parent / "monthly"
    monthly_dir = Path(monthly_dir)
    # P5-11 callable guard: direct callers get the same frozen-path
    # rejection as the CLI path.
    ensure_not_frozen(monthly_dir, "monthly_dir")

    ds = xr.open_dataset(source)
    try:
        # Canonicalize the time coordinate
        if "valid_time" in ds.dims or "valid_time" in ds.coords:
            ds = ds.rename({"valid_time": "time"})

        missing_dims = set(REQUIRED_DIMS) - set(ds.dims)
        if missing_dims:
            raise ValueError(
                f"{source}: missing required dims {sorted(missing_dims)}")

        for bad in FORBIDDEN_DATA_VARS:
            if bad in ds.data_vars:
                raise ValueError(
                    f"{source}: contains '{bad}' (geometric snow depth); "
                    f"the contract requires 'sd' "
                    f"(snow_depth_water_equivalent)")
        missing_vars = set(REQUIRED_DATA_VARS) - set(ds.data_vars)
        if missing_vars:
            raise ValueError(
                f"{source}: missing required data_vars "
                f"{sorted(missing_vars)}")

        # P5-07 exact timestamp validation. Sort first, then require the
        # timestamp set to be EXACTLY the expected UTC hourly set for
        # the requested year/month — duplicates, wrong-month,
        # out-of-range and post-cutoff payloads are all rejected.
        ds = ds.sortby("time")
        import numpy as np
        times = np.asarray(ds["time"].values)
        if np.unique(times).size != times.size:
            raise ValueError(
                f"{source}: duplicate timestamps in payload; rejecting "
                f"rather than silently deduplicating")

        days = days_for_month(year, month)
        expected = np.arange(
            np.datetime64(f"{year}-{month}-{days[0]}T00:00"),
            np.datetime64(f"{year}-{month}-{days[-1]}T23:00")
            + np.timedelta64(1, "h"),
            np.timedelta64(1, "h"),
        )
        cutoff = np.datetime64(EVENT_CUTOFF_ISO)
        if (times >= cutoff).any():
            raise ValueError(
                f"{source}: contains post-cutoff timestamps "
                f"(>= {EVENT_CUTOFF_ISO}Z)")
        if times.size != expected.size or not (times == expected).all():
            first = str(times[0]) if times.size else "<none>"
            last = str(times[-1]) if times.size else "<none>"
            raise ValueError(
                f"{source}: timestamps do not exactly match the "
                f"expected UTC hourly set for {year}-{month} "
                f"(expected {expected.size} hours "
                f"{expected[0]}..{expected[-1]}; "
                f"got {times.size} hours {first}..{last})")

        # P5-07 finite-value validation: every required data variable
        # must be finite across the whole array — a single NaN or inf
        # anywhere rejects the payload outright.
        for var in REQUIRED_DATA_VARS:
            values = np.asarray(ds[var].values)
            bad = int(np.count_nonzero(~np.isfinite(values)))
            if bad:
                raise ValueError(
                    f"{source}: required variable '{var}' has {bad} "
                    f"non-finite value(s); rejecting payload")

        monthly_dir.mkdir(parents=True, exist_ok=True)
        out_path = monthly_dir / f"era5_land_{year}_{month}.nc"
        ds.to_netcdf(out_path)
    finally:
        ds.close()

    if ledger_entry is not None:
        ledger_entry["payload_format"] = fmt
        ledger_entry["payload_sha256"] = payload_sha256
        ledger_entry["normalized_file"] = str(out_path)

    return out_path


def select_cell(ds, target_lat: float = EVENT_LAT,
                target_lon: float = EVENT_LON) -> tuple[float, float]:
    """Deterministic nearest-cell selection with explicit tie-breaks.

    Manual argmin — xarray's method='nearest' tie behavior is not relied
    on. On equidistant latitude choose the HIGHER latitude (e.g. 28.3
    over 28.2 for the requested 28.25 exact tie); on equidistant
    longitude choose the LOWER longitude.
    """
    import numpy as np

    lats = np.asarray(ds["latitude"].values, dtype=float)
    lons = np.asarray(ds["longitude"].values, dtype=float)

    lat_dist = np.abs(lats - target_lat)
    lat_tied = np.flatnonzero(
        np.isclose(lat_dist, lat_dist.min(), rtol=0, atol=1e-9))
    lat_idx = max(lat_tied, key=lambda i: lats[i])  # higher lat wins ties

    lon_dist = np.abs(lons - target_lon)
    lon_tied = np.flatnonzero(
        np.isclose(lon_dist, lon_dist.min(), rtol=0, atol=1e-9))
    lon_idx = min(lon_tied, key=lambda i: lons[i])  # lower lon wins ties

    return float(lats[lat_idx]), float(lons[lon_idx])


def merge_monthly(run_root: Path, completed: list[dict],
                  ledger: dict) -> Path | None:
    """Merge normalized monthly files and select the event cell.

    Concatenates along time, sorts, drops duplicated timestamps and
    verifies per-month hourly completeness (expected hours = days*24).
    Incomplete months are reported and recorded in the ledger.

    P5-08 completeness gate: if ANY month is incomplete the merged file
    is NOT produced (quarantined) — downstream must never consume
    partial data as complete. When the merged file passes all
    validation a merged/complete.json marker is written beside it.
    """
    import numpy as np
    import xarray as xr

    # P5-11 callable guard
    ensure_not_frozen(run_root, "run_root")

    monthly_dir = run_root / "monthly"
    merged_dir = run_root / "merged"
    out_path = merged_dir / MERGED_NAME

    datasets = []
    for entry in completed:
        f = monthly_dir / f"era5_land_{entry['year']}_{entry['month']}.nc"
        if not f.exists():
            continue
        ds = xr.open_dataset(f)
        if "valid_time" in ds.dims or "valid_time" in ds.coords:
            ds = ds.rename({"valid_time": "time"})
        datasets.append((entry, ds))

    if not datasets:
        return None

    merged = xr.concat([ds for _, ds in datasets], dim="time")
    merged = merged.sortby("time")
    _, unique_idx = np.unique(merged["time"].values, return_index=True)
    if len(unique_idx) < merged.sizes["time"]:
        merged = merged.isel(time=np.sort(unique_idx))

    # Per-month completeness: expected hours = days * 24
    print("\nMonthly completeness (hours):")
    incomplete = []
    for entry, ds in datasets:
        year, month = entry["year"], entry["month"]
        expected = len(days_for_month(year, month)) * 24
        actual = int(ds.sizes["time"])
        entry["expected_hours"] = expected
        entry["actual_hours"] = actual
        entry["complete"] = actual == expected
        if actual == expected:
            print(f"  {year}-{month}: {actual}/{expected} hours")
        else:
            print(f"  {year}-{month}: INCOMPLETE {actual}/{expected} hours")
            incomplete.append({
                "year": year, "month": month,
                "expected_hours": expected, "actual_hours": actual,
            })
    ledger["incomplete_months"] = incomplete

    if incomplete:
        # Quarantine: never emit a merged file from incomplete inputs.
        print(f"\nMERGE QUARANTINED: {len(incomplete)} incomplete "
              f"month(s); merged file not produced.")
        ledger["merged_status"] = "quarantined"
        for _, ds in datasets:
            ds.close()
        return None

    # Deterministic nearest-cell selection (see select_cell tie rules)
    sel_lat, sel_lon = select_cell(merged)
    ledger["requested_cell"] = {"latitude": EVENT_LAT, "longitude": EVENT_LON}
    ledger["selected_cell"] = {"latitude": sel_lat, "longitude": sel_lon}
    print(f"Requested cell: ({EVENT_LAT}, {EVENT_LON}) -> "
          f"selected ({sel_lat}, {sel_lon})")

    merged_dir.mkdir(parents=True, exist_ok=True)
    cell = merged.sel(latitude=sel_lat, longitude=sel_lon)
    cell.to_netcdf(out_path)

    # P5-08: the complete.json marker is written ONLY when the merged
    # file passed all validation above.
    marker = {
        "merged_file": str(out_path),
        "merged_sha256": hashlib.sha256(out_path.read_bytes()).hexdigest(),
        "months": len(datasets),
        "total_hours": int(merged.sizes["time"]),
        "selected_cell": {"latitude": sel_lat, "longitude": sel_lon},
        "validated_at": datetime.now().isoformat(),
    }
    with open(merged_dir / MERGED_MARKER_NAME, "w") as f:
        json.dump(marker, f, indent=2)

    for _, ds in datasets:
        ds.close()

    ledger["merged_status"] = "validated"
    return out_path


def write_ledger(run_root: Path, ledger: dict) -> Path:
    """Write the download ledger (checkpoint-safe)."""
    ensure_not_frozen(run_root, "ledger run_root")  # P5-11
    ledger_path = run_root / LEDGER_NAME
    with open(ledger_path, "w") as f:
        json.dump(ledger, f, indent=2)
    return ledger_path


def download_all(years: list[int], run_root: Path,
                 dry_run: bool = False) -> dict:
    """Download ERA5-Land JJA for all specified years.

    Downloads month-by-month to keep individual CDS requests small and
    allow per-month retry on failure. Fresh-run only — no resume of a
    partially populated run root is claimed.

    P5-08: ledger["status"] is "completed" ONLY when every planned
    month produced a complete normalized file and the merged output
    passed all validation; otherwise it is "incomplete" and no merged
    file is emitted.
    """
    ensure_not_frozen(run_root, "run_root")  # P5-11 callable guard
    ledger = {
        "start_time": datetime.now().isoformat(),
        "dataset": CDS_DATASET,
        "variables": CDS_VARIABLES,
        "area": AREA,
        "requested_cell": {"latitude": EVENT_LAT, "longitude": EVENT_LON},
        "run_root": str(run_root),
        "years": years,
        "months": JJA_MONTHS,
        "status": "started",
        "completed_months": [],
        "failed_months": [],
        "incomplete_months": [],
        "disk_checks": [],
        "total_size_mb": 0,
    }

    planned = [(y, m) for y in years for m in months_for_year(y)]
    ledger["planned_months"] = len(planned)

    if dry_run:
        # P5-05 canonical dry-run: print the deterministic CDS request
        # JSON for every planned month. No files are created and the
        # cdsapi client is never imported/constructed on this path.
        print(f"DRY RUN: {len(planned)} monthly requests "
              f"({len(years)} years x JJA)")
        print(f"Dataset: {CDS_DATASET}")
        print(f"Variables: {CDS_VARIABLES}")
        assert_area_contract()
        print(f"Area: {AREA} (1° × 1° — verified by "
              f"assert_area_contract)")
        print(f"Run root: {run_root}")
        print("Planned requests:")
        for y, m in planned:
            days = days_for_month(y, m)
            print(f"  {y}-{m}: {len(days)} days "
                  f"({days[0]}..{days[-1]}) x 24 hours")
            print(f"    raw        -> {run_root / 'raw' / f'era5_land_{y}_{m}.nc'}")
            print(f"    normalized -> {run_root / 'monthly' / f'era5_land_{y}_{m}.nc'}")
            print(f"    CDS request ({CDS_DATASET}):")
            print(json.dumps(build_request(y, m), indent=2,
                             sort_keys=True))
        print(f"Merged output -> {run_root / 'merged' / MERGED_NAME}")
        print(f"Ledger        -> {run_root / LEDGER_NAME}")
        ledger["status"] = "dry_run"
        return ledger

    run_root.mkdir(parents=True, exist_ok=True)
    raw_dir = run_root / "raw"
    monthly_dir = run_root / "monthly"
    raw_dir.mkdir(exist_ok=True)
    monthly_dir.mkdir(exist_ok=True)

    # P5-10 disk reserve guard — before acquisition starts.
    ok, free_gib = check_disk_reserve(run_root)
    ledger["disk_checks"].append({
        "stage": "pre_acquisition", "free_gib": free_gib, "ok": ok})
    if not ok:
        ledger["disk_reserve_failure"] = {
            "stage": "pre_acquisition", "free_gib": free_gib,
            "min_free_gib": DISK_MIN_FREE_GIB}
        ledger["status"] = "incomplete"
        ledger["end_time"] = datetime.now().isoformat()
        write_ledger(run_root, ledger)
        return ledger

    # Initialize CDS client (uses ~/.cdsapirc automatically).
    # cdsapi is imported lazily so this module stays importable without
    # network access or credentials.
    import cdsapi
    print("Initializing CDS client...")
    client = cdsapi.Client(quiet=True)
    print("CDS client initialized.")

    # Download month by month
    for year, month in planned:
        month_file = raw_dir / f"era5_land_{year}_{month}.nc"

        success = download_month(client, year, month, month_file)
        if not success:
            ledger["failed_months"].append({
                "year": year,
                "month": month,
                "status": "failed",
                "stage": "download",
            })
            write_ledger(run_root, ledger)
            continue

        size_mb = month_file.stat().st_size / (1024 * 1024)
        entry = {
            "year": year,
            "month": month,
            "days": len(days_for_month(year, month)),
            "size_mb": round(size_mb, 1),
            "status": "downloaded",
        }
        try:
            normalize_payload(month_file, monthly_dir,
                              year=year, month=month,
                              ledger_entry=entry)
        except Exception as e:
            print(f"  NORMALIZE FAILED {year}-{month}: {e}")
            ledger["failed_months"].append({
                "year": year,
                "month": month,
                "status": "failed",
                "stage": "normalize",
                "error": str(e),
            })
            write_ledger(run_root, ledger)
            continue

        ledger["completed_months"].append(entry)
        ledger["total_size_mb"] += size_mb

        # Save ledger after each month (checkpoint)
        write_ledger(run_root, ledger)

        # P5-10 disk reserve guard — after each monthly write.
        ok, free_gib = check_disk_reserve(run_root)
        ledger["disk_checks"].append({
            "stage": f"post_{year}_{month}",
            "free_gib": free_gib, "ok": ok})
        if not ok:
            print("  Disk reserve below threshold; "
                  "stopping further month requests.")
            ledger["disk_reserve_failure"] = {
                "stage": f"post_{year}_{month}", "free_gib": free_gib,
                "min_free_gib": DISK_MIN_FREE_GIB}
            write_ledger(run_root, ledger)
            break

    # P5-08 completeness gate: every planned month must have a complete
    # normalized file on disk and no month may have failed — otherwise
    # the merged file is not produced.
    all_normalized = all(
        (monthly_dir / f"era5_land_{y}_{m}.nc").exists()
        for y, m in planned)
    ready_to_merge = (
        len(ledger["completed_months"]) == len(planned)
        and not ledger["failed_months"]
        and all_normalized)

    # Merge all normalized monthly files into one
    if ready_to_merge:
        # P5-10 disk reserve guard — before merge.
        ok, free_gib = check_disk_reserve(run_root)
        ledger["disk_checks"].append({
            "stage": "pre_merge", "free_gib": free_gib, "ok": ok})
        if not ok:
            ledger["disk_reserve_failure"] = {
                "stage": "pre_merge", "free_gib": free_gib,
                "min_free_gib": DISK_MIN_FREE_GIB}
        else:
            print("\nMerging monthly files into single NetCDF...")
            try:
                out_path = merge_monthly(run_root,
                                         ledger["completed_months"],
                                         ledger)
                if out_path is not None:
                    final_size_mb = out_path.stat().st_size / (1024 * 1024)
                    print(f"Merged file: {out_path} "
                          f"({final_size_mb:.1f} MB)")
                    ledger["final_file"] = str(out_path)
                    ledger["final_size_mb"] = round(final_size_mb, 1)
            except Exception as e:
                print(f"Merge error: {e}")
                ledger["merge_error"] = str(e)
    elif ledger["completed_months"]:
        print("\nCompleteness gate: not all planned months completed — "
              "merged output withheld (run is incomplete).")

    ledger["end_time"] = datetime.now().isoformat()
    merged_ok = (bool(ledger.get("final_file"))
                 and ledger.get("merged_status") == "validated"
                 and not ledger["incomplete_months"])
    ledger["status"] = ("completed"
                        if ready_to_merge and merged_ok
                        and not ledger["failed_months"]
                        else "incomplete")

    write_ledger(run_root, ledger)

    print(f"\nDownload finished: {len(ledger['completed_months'])} months, "
          f"{len(ledger['failed_months'])} failed, "
          f"{ledger['total_size_mb']:.1f} MB total")
    print(f"Status: {ledger['status']}")
    print(f"Ledger: {run_root / LEDGER_NAME}")

    return ledger


def netcdf_smoke() -> bool:
    """P5-16 NetCDF backend smoke test.

    Trivial xarray round-trip: build a 3-point dataset, write it to an
    in-memory NetCDF buffer, read it back and compare. Returns True on
    an exact round-trip; raises (or returns False) if the installed
    NetCDF backend is broken.
    """
    import io

    import numpy as np
    import xarray as xr

    ds = xr.Dataset(
        {"v": ("time", np.array([1.0, 2.0, 3.0], dtype=np.float64))},
        coords={"time": np.arange(3)},
    )
    buf = io.BytesIO()
    ds.to_netcdf(buf)
    buf.seek(0)
    with xr.open_dataset(buf) as back:
        return (back.sizes.get("time") == 3
                and bool(np.array_equal(back["v"].values,
                                        ds["v"].values)))


def parse_year_range(spec: str) -> list[int]:
    """P5-02 strict --year-range parser.

    Requires the format ``START-END`` (a bare ``YYYY`` is accepted as
    shorthand for ``YYYY-YYYY``) with
    ``MIN_YEAR <= start <= end <= MAX_YEAR``. Raises ValueError with a
    clear message on any violation; the caller prints it to stderr and
    exits 1 BEFORE any cdsapi import or file creation. This replaces
    the old loose parse that crashed IndexError on a reversed range,
    accepted out-of-bounds years like 2000, and silently planned zero
    requests for years after the event year.
    """
    parts = spec.strip().split("-")
    if len(parts) == 1:
        start_s = end_s = parts[0]
    elif len(parts) == 2:
        start_s, end_s = parts
    else:
        raise ValueError(
            f"invalid --year-range {spec!r}: expected format START-END "
            f"(e.g. {MIN_YEAR}-{MAX_YEAR})")
    if not start_s.strip() or not end_s.strip():
        raise ValueError(
            f"invalid --year-range {spec!r}: expected format START-END "
            f"(e.g. {MIN_YEAR}-{MAX_YEAR})")
    try:
        start, end = int(start_s), int(end_s)
    except ValueError:
        raise ValueError(
            f"invalid --year-range {spec!r}: START and END must be "
            f"integer years (e.g. {MIN_YEAR}-{MAX_YEAR})") from None
    if start > end:
        raise ValueError(
            f"invalid --year-range {spec!r}: start year {start} is "
            f"after end year {end}; require start <= end")
    if start < MIN_YEAR or end > MAX_YEAR:
        raise ValueError(
            f"invalid --year-range {spec!r}: out of bounds; require "
            f"{MIN_YEAR} <= start <= end <= {MAX_YEAR}")
    return list(range(start, end + 1))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Download ERA5-Land for Nepal event analysis")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print planned requests and output paths; "
                             "create no files")
    parser.add_argument("--run-root", type=str, default=None,
                        help=f"Output run directory "
                             f"(default: {DEFAULT_RUN_ROOT.relative_to(REPO_ROOT)})")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite an existing run root instead of "
                             "creating a new timestamped run dir")
    parser.add_argument("--year-range", type=str, default="2001-2026",
                        help=f"Year range START-END, inclusive "
                             f"({MIN_YEAR}-{MAX_YEAR}; e.g. 2001-2026)")
    parser.add_argument("--smoke", action="store_true",
                        help="Run the NetCDF backend smoke test "
                             "(xarray round-trip) and exit")
    args = parser.parse_args(argv)

    # AUD-02: verify the request area is the documented 1° × 1° box
    # before any CDS request is built (dry-run output included).
    try:
        assert_area_contract()
    except AssertionError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if args.smoke:
        print("NetCDF backend smoke test...")
        try:
            ok = netcdf_smoke()
        except Exception as e:
            print(f"SMOKE FAILED: {e}", file=sys.stderr)
            return 1
        if not ok:
            print("SMOKE FAILED: round-trip mismatch", file=sys.stderr)
            return 1
        print("NetCDF smoke test passed.")
        return 0

    # P5-02: strict validation happens before run-root resolution, so
    # an invalid range exits before any cdsapi import or file creation.
    try:
        years = parse_year_range(args.year_range)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    try:
        run_root = resolve_run_root(args.run_root, force=args.force)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print("Nepal Event Anomaly — ERA5-Land Download")
    print(f"Years: {years[0]}-{years[-1]} ({len(years)} years)")
    print(f"Months: JJA ({JJA_MONTHS})")
    print(f"Variables: {len(CDS_VARIABLES)}")
    print(f"Area: {AREA} (1° × 1° around event)")
    print(f"Event cell: ({EVENT_LAT}°N, {EVENT_LON}°E)")
    print(f"Run root: {run_root}")
    print()

    ledger = download_all(years, run_root, dry_run=args.dry_run)

    if ledger["status"] in ("completed", "dry_run"):
        return 0
    if ledger["failed_months"]:
        print(f"\nWARNING: {len(ledger['failed_months'])} months failed. "
              f"Re-run with a fresh run root or --force to retry.")
    elif ledger["status"] == "incomplete":
        print("\nWARNING: run is incomplete — merged output was not "
              "validated. Re-run with a fresh run root or --force.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
