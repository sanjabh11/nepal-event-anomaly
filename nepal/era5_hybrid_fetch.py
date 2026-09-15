#!/usr/bin/env python3
"""Hybrid fast-path ERA5-Land acquisition for the P5 GMM confirmation.

The MARS queue for `reanalysis-era5-land` is the pacing factor of the
monthly downloader (one queued request per month, ~78 requests). This
script reduces queue exposure using two CDS endpoints:

1. `reanalysis-era5-land-timeseries` — the CDS ARCO (analysis-ready,
   cloud-optimised) regridded copy of ERA5-Land, designed for point
   time-series. ONE request returns the full 1950-present hourly
   series for the requested area. It carries 5 of the 7 contract
   variables: t2m, d2m, u10, v10, tp. It does NOT carry
   snow_depth_water_equivalent or snowfall.

2. `reanalysis-era5-land` (primary archive) — used ONLY for the two
   missing variables (sd, sf), batched as multi-year JJA chunks
   (2 vars stay far under the 12,000-field selection limit), so ~15
   small requests replace 73 of the monthly ones.

Subcommands:
  arco      fetch the 5-variable ARCO time series (one request)
  sd-sf     fetch batched snow_depth_water_equivalent + snowfall chunks
  assemble  join the two sources, emit canonical monthly files via the
            SAME normalize_payload validation gate, then merge and
            write complete.json — output layout is identical to
            era5_download.py so the extract/GMM chain is unchanged.

Provenance disclosure: the ledger records which retrieval path served
each variable. The data content is ERA5-Land either way; the ARCO copy
is ECMWF's own regridded subset of the same archive.

Fresh-run semantics: a run root that already has a completed ledger is
refused unless --force is passed (same rule as era5_download).
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

from nepal.era5_download import (
    AREA,
    CDS_DATASET,
    EVENT_CUTOFF_ISO,
    EVENT_YEAR,
    JJA_MONTHS,
    LEDGER_NAME,
    check_disk_reserve,
    days_for_month,
    detect_payload_format,
    ensure_not_frozen,
    merge_monthly,
    normalize_payload,
    resolve_run_root,
    write_ledger,
)

TIMESERIES_DATASET = "reanalysis-era5-land-timeseries"

# Contract vars available on the ARCO timeseries endpoint
ARCO_VARIABLES = [
    "2m_temperature",
    "2m_dewpoint_temperature",
    "10m_u_component_of_wind",
    "10m_v_component_of_wind",
    "total_precipitation",
]

# Contract vars that must still come from the primary archive
SNOW_VARIABLES = ["snow_depth_water_equivalent", "snowfall"]

# CDS long-name -> GRIB short name used by the contract
NAME_MAP = {
    "2m_temperature": "t2m",
    "2m_dewpoint_temperature": "d2m",
    "10m_u_component_of_wind": "u10",
    "10m_v_component_of_wind": "v10",
    "snow_depth_water_equivalent": "sd",
    "snowfall": "sf",
    "total_precipitation": "tp",
}

ALL_DAYS = [f"{d:02d}" for d in range(1, 32)]
ALL_HOURS = [f"{h:02d}:00" for h in range(24)]

# Requesting the exact grid node (28.3, 85.5) makes the endpoint's
# nearest-point selection return the same cell the deterministic tie
# rule in era5_download.select_cell chooses for EVENT_LAT/EVENT_LON.
SELECTED_CELL = {"latitude": 28.3, "longitude": 85.5}

# sd/sf batches: the CDS cost limit scales with grid points, so the
# requests use a POINT area (the selected cell itself) — ~121x cheaper
# than the 1° box, which makes multi-year JJA chunks legal. 5-year
# chunks; adaptive halving retries on a "too large" rejection. 2026 is
# split so that August can be truncated at the pre-event cutoff day.
POINT_AREA = [SELECTED_CELL["latitude"], SELECTED_CELL["longitude"],
              SELECTED_CELL["latitude"], SELECTED_CELL["longitude"]]
CHUNK_YEARS = 5


def sd_sf_chunks(years: list[int]) -> list[dict]:
    chunks = []
    normal = [y for y in years if y < EVENT_YEAR]
    for i in range(0, len(normal), CHUNK_YEARS):
        grp = normal[i:i + CHUNK_YEARS]
        chunks.append({"years": grp, "months": JJA_MONTHS,
                       "days": ALL_DAYS,
                       "tag": f"{grp[0]}_{grp[-1]}"})
    if EVENT_YEAR in years:
        chunks.append({"years": [EVENT_YEAR], "months": ["06", "07"],
                       "days": ALL_DAYS, "tag": f"{EVENT_YEAR}_junjul"})
        chunks.append({"years": [EVENT_YEAR], "months": ["08"],
                       "days": [f"{d:02d}" for d in range(1, 26)],
                       "tag": f"{EVENT_YEAR}_aug"})
    return chunks


def _client():
    import cdsapi
    return cdsapi.Client(quiet=True)


# The ARCO timeseries endpoint takes `location` + `date` (an `area`
# request errors with MultiAdaptorNoDataError — probed 2026-09-15).
ARCO_DATE_RANGE = "2001-01-01/2026-08-25"  # bounded by pre-event cutoff


def cmd_arco(run_root: Path) -> Path:
    """One request: the 5 contract vars, 2001-2026, selected cell."""
    out = run_root / "raw" / "arco_timeseries.nc"
    if out.exists():
        print(f"ARCO series already present: {out}")
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    req = {
        "variable": ARCO_VARIABLES,
        "data_format": "netcdf",
        "location": SELECTED_CELL,
        "date": ARCO_DATE_RANGE,
    }
    print(f"ARCO request ({TIMESERIES_DATASET}): "
          f"{json.dumps(req, sort_keys=True)}")
    _client().retrieve(TIMESERIES_DATASET, req, str(out))
    print(f"ARCO series: {out} "
          f"({out.stat().st_size / (1024 * 1024):.1f} MB)")
    return out


def cmd_sd_sf(run_root: Path, years: list[int],
              chunk_ids: set[str] | None = None) -> list[Path]:
    """Batched sd+sf requests; each chunk is one queued CDS request."""
    raw_dir = run_root / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    client = _client()
    fetched = []
    pending = [c for c in sd_sf_chunks(years)
               if not chunk_ids or c["tag"] in chunk_ids]
    while pending:
        chunk = pending.pop(0)
        out = raw_dir / f"sd_sf_{chunk['tag']}.nc"
        if out.exists():
            print(f"chunk {chunk['tag']} already fetched")
            fetched.append(out)
            continue
        req = {
            "variable": SNOW_VARIABLES,
            "year": [str(y) for y in chunk["years"]],
            "month": chunk["months"],
            "day": chunk["days"],
            "time": ALL_HOURS,
            "data_format": "netcdf",
            "area": POINT_AREA,
        }
        print(f"sd/sf chunk {chunk['tag']}: years={chunk['years']} "
              f"months={chunk['months']} days={len(chunk['days'])}")
        # CDS caps the number of queued jobs per account+dataset;
        # submissions rejected for queue pressure are retried with
        # backoff rather than counted as data failures.
        try:
            for attempt in range(30):
                try:
                    client.retrieve(CDS_DATASET, req, str(out))
                    break
                except Exception as e:
                    if "temporarily limited" in str(e) and attempt < 29:
                        import time
                        print(f"  queue full, retry in 60s "
                              f"(attempt {attempt + 1})")
                        time.sleep(60)
                        continue
                    raise
        except Exception as e:
            # Adaptive halving on "request too large": requeue the
            # halves at the front; single-year floor raises through.
            if "too large" in str(e).lower() and len(chunk["years"]) > 1:
                mid = len(chunk["years"]) // 2
                halves = []
                for grp in (chunk["years"][:mid], chunk["years"][mid:]):
                    halves.append(dict(chunk, years=grp,
                                       tag=f"{grp[0]}_{grp[-1]}"))
                print(f"  chunk too large, splitting "
                      f"{chunk['years']} -> "
                      f"{[h['tag'] for h in halves]}")
                pending = halves + pending
                continue
            raise
        fetched.append(out)
        print(f"  -> {out} ({out.stat().st_size / 1024:.0f} KB)")
    return fetched


def _open_any(path: Path):
    """Open a CDS payload (zip or netcdf) as an xarray Dataset.

    The ARCO timeseries endpoint splits its zip into one member per
    variable group (wind / 2m-temperature / pressure-precipitation);
    all members are extracted and merged. Single-member zips are
    opened directly.
    """
    import xarray as xr
    import zipfile
    path = Path(path)
    if detect_payload_format(path) == "zip":
        with zipfile.ZipFile(path) as zf:
            members = [n for n in zf.namelist() if n.endswith(".nc")]
            if not members:
                raise ValueError(f"{path}: zip contains no .nc member")
            parts = []
            for i, name in enumerate(members):
                tmp = path.parent / f"{path.stem}.unzipped{i}.nc"
                tmp.write_bytes(zf.read(name))
                parts.append(xr.open_dataset(tmp))
            if len(parts) == 1:
                return parts[0]
            merged = xr.merge(parts, compat="override", join="exact")
            for ds in parts:
                ds.close()
            return merged
    return xr.open_dataset(path)


def _canonical_names(ds):
    """Rename CDS long names / valid_time to contract short names."""
    ren = {}
    for long_name, short in NAME_MAP.items():
        if long_name in ds.data_vars and short not in ds.data_vars:
            ren[long_name] = short
    if "valid_time" in ds.dims or "valid_time" in ds.coords:
        ren["valid_time"] = "time"
    if ren:
        ds = ds.rename(ren)
    return ds


def cmd_assemble(run_root: Path, years: list[int]) -> dict:
    """Join ARCO + sd/sf sources, emit validated monthly files, merge."""
    import numpy as np
    import xarray as xr

    raw_dir = run_root / "raw"
    monthly_dir = run_root / "monthly"
    monthly_dir.mkdir(exist_ok=True)

    arco_path = raw_dir / "arco_timeseries.nc"
    if not arco_path.exists():
        raise SystemExit(f"missing ARCO series: {arco_path}")
    snow_paths = [p for p in sorted(raw_dir.glob("sd_sf_*.nc"))
                  if "unzipped" not in p.name]
    # days_for_month already truncates 2026-08 at the pre-event cutoff
    planned = [(y, m) for y in years for m in JJA_MONTHS
               if days_for_month(y, m)]
    if not snow_paths:
        raise SystemExit("no sd/sf chunk payloads under raw/")

    arco = _canonical_names(_open_any(arco_path))
    # Snow sources: EDH mirror file (scalar lat/lon coords) plus MARS
    # 2026 chunks (dim coords). Canonicalize each, promote scalar
    # coords to size-1 dims, then concat along time.
    snow_parts = []
    for sp in snow_paths:
        part = _canonical_names(_open_any(sp))
        # MARS payloads carry extra coords (expver, number) that the
        # EDH mirror lacks — keep only the contract coords.
        drop = [c for c in set(part.coords) | set(part.data_vars)
                if c not in ("time", "latitude", "longitude",
                             "sd", "sf")]
        if drop:
            part = part.drop_vars(drop)
        for c in ("latitude", "longitude"):
            if c in part.coords and c not in part.dims:
                part = part.expand_dims(c)
        # Verify this part's node is the selected cell (within grid
        # epsilon) BEFORE relabeling — a wrong node must abort, not be
        # silently renamed.
        import numpy as np
        for c, tgt in (("latitude", SELECTED_CELL["latitude"]),
                       ("longitude", SELECTED_CELL["longitude"])):
            vals = np.asarray(part[c].values, dtype=float).ravel()
            if not np.isclose(vals, tgt, atol=0.051).all():
                raise SystemExit(
                    f"{sp}: {c} values {vals} are not the selected "
                    f"cell {tgt} — aborting")
        # Canonicalize coords to the exact contract cell BEFORE concat:
        # EDH stores the node as 28.30000000000097 while MARS uses 28.3
        # — an outer join would split them and NaN out one source.
        part = part.assign_coords(
            latitude=np.asarray([SELECTED_CELL["latitude"]]),
            longitude=np.asarray([SELECTED_CELL["longitude"]]))
        part = part[["sd", "sf"]]
        snow_parts.append(part)
    snow = xr.concat(snow_parts, dim="time", join="exact").sortby("time")
    import numpy as np
    if np.unique(snow["time"].values).size != snow.sizes["time"]:
        raise SystemExit("duplicate timestamps across snow sources; "
                         "sources must be disjoint (EDH<=2025, "
                         "MARS=2026)")

    missing = [v for v in ("t2m", "d2m", "u10", "v10", "tp")
               if v not in arco.data_vars]
    if missing:
        raise SystemExit(f"ARCO series missing vars {missing}: "
                         f"{sorted(arco.data_vars)}")
    for v in ("sd", "sf"):
        if v not in snow.data_vars:
            raise SystemExit(f"snow chunks missing '{v}': "
                             f"{sorted(snow.data_vars)}")

    # The ARCO payload is a single point at SELECTED_CELL; the sd/sf
    # payloads carry the full 1° box. Select the identical grid node
    # from the snow data so both sources share one cell. The node is
    # exact (0.1° grid) — assert it exists rather than approximating.
    import numpy as np
    lats = np.asarray(snow["latitude"].values, dtype=float)
    lons = np.asarray(snow["longitude"].values, dtype=float)
    if not (np.isclose(lats, SELECTED_CELL["latitude"], atol=1e-9).any()
            and np.isclose(lons, SELECTED_CELL["longitude"],
                           atol=1e-9).any()):
        raise SystemExit(
            f"sd/sf grid lacks the selected cell {SELECTED_CELL}; "
            f"lat range {lats.min()}..{lats.max()}, "
            f"lon range {lons.min()}..{lons.max()}")
    snow = snow.sel(latitude=SELECTED_CELL["latitude"],
                    longitude=SELECTED_CELL["longitude"],
                    method="nearest", tolerance=0.051)
    # .sel with scalars drops the dims; restore size-1 dims so the
    # merged monthly files keep the contract's (lat, lon, time) shape.
    if "latitude" not in snow.dims:
        snow = snow.expand_dims(
            {"latitude": [SELECTED_CELL["latitude"]],
             "longitude": [SELECTED_CELL["longitude"]]})

    # Verify the ARCO payload really is the expected cell, then
    # canonicalize BOTH sources' lat/lon to the exact contract value —
    # EDH and MARS encode the node with different float epsilons
    # (28.30000000000097 vs 28.3), which breaks join="exact".
    if "latitude" in arco.coords:
        a_lat = float(np.asarray(arco["latitude"].values).ravel()[0])
        a_lon = float(np.asarray(arco["longitude"].values).ravel()[0])
        if not (np.isclose(a_lat, SELECTED_CELL["latitude"], atol=0.051)
                and np.isclose(a_lon, SELECTED_CELL["longitude"],
                               atol=0.051)):
            raise SystemExit(
                f"ARCO returned cell ({a_lat}, {a_lon}) instead of "
                f"{SELECTED_CELL} — aborting (tie-rule deviation)")
    for c in ("latitude", "longitude"):
        val = SELECTED_CELL[c]
        for ds_ in (snow, arco):
            if c in ds_.dims:
                ds_.coords[c] = np.asarray([val], dtype=np.float64)
            elif c in ds_.coords:
                ds_.coords[c] = np.float64(val)

    ledger = {
        "start_time": datetime.now().isoformat(),
        "dataset": CDS_DATASET,
        "retrieval_paths": {
            "t2m,d2m,u10,v10,tp": TIMESERIES_DATASET,
            "sd,sf (2001-2025)": "earthdatahub destine mirror of "
                                 "reanalysis-era5-land",
            "sd,sf (2026)": CDS_DATASET,
        },
        "variables": ["t2m", "d2m", "u10", "v10", "sd", "sf", "tp"],
        "area": AREA,
        "run_root": str(run_root),
        "years": years,
        "months": ["06", "07", "08"],
        "status": "started",
        "planned_months": len(planned),
        "completed_months": [],
        "failed_months": [],
        "incomplete_months": [],
        "disk_checks": [],
        "total_size_mb": 0.0,
        "hybrid_assembly": True,
    }
    write_ledger(run_root, ledger)

    for year, month in planned:
        days = days_for_month(year, month)
        t0 = np.datetime64(f"{year}-{month}-{days[0]}T00:00")
        t1 = np.datetime64(f"{year}-{month}-{days[-1]}T23:00")
        try:
            a = arco.sel(time=slice(t0, t1))
            s = snow.sel(time=slice(t0, t1))
            joined = xr.merge([a[["t2m", "d2m", "u10", "v10", "tp"]],
                               s[["sd", "sf"]]],
                              compat="override", join="exact")
            virt = raw_dir / f"assembled_{year}_{month}.nc"
            joined.to_netcdf(virt)
            entry = {"year": year, "month": month,
                     "days": len(days), "status": "downloaded",
                     "size_mb": round(
                         virt.stat().st_size / (1024 * 1024), 1),
                     "retrieval": "hybrid"}
            normalize_payload(virt, monthly_dir, year=year,
                              month=month, ledger_entry=entry)
            entry["payload_format"] = "hybrid_assembly"
            ledger["completed_months"].append(entry)
        except Exception as e:
            print(f"  ASSEMBLE FAILED {year}-{month}: {e}")
            ledger["failed_months"].append({
                "year": year, "month": month,
                "status": "failed", "stage": "assemble",
                "error": str(e)})
        write_ledger(run_root, ledger)

    arco.close()
    snow.close()

    ready = (len(ledger["completed_months"]) == len(planned)
             and not ledger["failed_months"]
             and all((monthly_dir / f"era5_land_{y}_{m}.nc").exists()
                     for y, m in planned))
    if ready:
        ok, free = check_disk_reserve(run_root)
        ledger["disk_checks"].append(
            {"stage": "pre_merge", "free_gib": free, "ok": ok})
        if ok:
            out = merge_monthly(run_root, ledger["completed_months"],
                                ledger)
            if out is not None:
                ledger["final_file"] = str(out)
                ledger["final_size_mb"] = round(
                    out.stat().st_size / (1024 * 1024), 1)
    merged_ok = (bool(ledger.get("final_file"))
                 and ledger.get("merged_status") == "validated"
                 and not ledger["incomplete_months"])
    ledger["status"] = ("completed"
                        if ready and merged_ok
                        and not ledger["failed_months"]
                        else "incomplete")
    ledger["end_time"] = datetime.now().isoformat()
    write_ledger(run_root, ledger)
    print(f"Assemble finished: {len(ledger['completed_months'])}"
          f"/{len(planned)} months, status={ledger['status']}")
    return ledger


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["arco", "sd-sf", "assemble"])
    p.add_argument("--run-root", required=True)
    p.add_argument("--year-range", default="2001-2026")
    p.add_argument("--chunks", default=None,
                   help="comma-separated sd/sf chunk tags to fetch "
                        "(default: all)")
    p.add_argument("--force", action="store_true")
    args = p.parse_args(argv)

    lo, hi = (int(v) for v in args.year_range.split("-"))
    years = list(range(lo, hi + 1))
    run_root = resolve_run_root(args.run_root, force=args.force)
    ensure_not_frozen(run_root, "run_root")

    ok, free = check_disk_reserve(run_root)
    if not ok:
        print(f"disk reserve failure: {free:.1f} GiB free")
        return 1

    if args.command == "arco":
        cmd_arco(run_root)
    elif args.command == "sd-sf":
        ids = set(args.chunks.split(",")) if args.chunks else None
        cmd_sd_sf(run_root, years, ids)
    else:
        ledger = cmd_assemble(run_root, years)
        return 0 if ledger["status"] == "completed" else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
