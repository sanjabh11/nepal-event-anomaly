"""HMA held-out copy of the adjm Earthmover worker (only the request-record amendment/derivation labels differ).
Fetches the frozen armc_hma_selector_inventory_v1.json. Original docstring follows.

Earthmover icechunk ERA5 retrieval worker for the v17 per-lake lane.

Reads the frozen v1 selector inventory (armc_v17_request_plan_v1.py output)
and fetches, per (selection_group, variable):

  * climatology groups  -> event calendar month x all years 2001-2025,
    PLUS the trailing 00:00 UTC hour so daily accumulation sums
    (tp/sf end-of-hour convention) can be formed correctly downstream.
  * spillover groups    -> the exact listed dates (+ trailing 00:00 hour).

Chunks: one netcdf per (selection_id, variable) concatenating all years.
Single-level vars come from group single/temporal; 't' comes from
pressure/temporal at 500 hPa.  Source coordinate arrays are exact-matched
to the planned grid centers before any extraction (fail-closed).
"""
import argparse, json, os, sys, time, hashlib
from pathlib import Path
import numpy as np
import pandas as pd
import xarray as xr
import icechunk
import zarr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import armc_cds_retrieve as m
from p5_safe_io import write_once_json, write_once_sidecar, sha256_bytes

STORE_URI = "s3://earthmover-icechunk-era5/icechunkV2"
SNAPSHOT = "ZFKDHBCTBVHVXM3BQFV0"
BASE = pd.Timestamp("1940-01-01", tz="UTC")
SINGLE_VARS = {"tp", "tcwv", "cape", "t2m", "sp", "sf"}
PRESSURE_VAR, PRESSURE_LEVEL = "t", 500.0
H = pd.Timedelta(hours=1)


def open_store():
    storage = icechunk.s3_storage(bucket="earthmover-icechunk-era5",
                                  prefix="icechunkV2", region="us-east-1",
                                  anonymous=True)
    repo = icechunk.Repository.open(storage)
    session = repo.readonly_session(snapshot_id=SNAPSHOT)
    assert session.snapshot_id == SNAPSHOT
    return zarr.open_group(session.store, mode="r")


def hour_index(ts):
    return int((ts - BASE) / H)


def fetch_block(pg, var, group, lats_src, lons_src, lat_axis, lon_axis,
                t_start, t_end_exclusive, lvl_idx=None):
    """Contiguous-index fetch (fast path) then exact crop — zarr fancy
    indexing on a list is catastrophically slow on remote stores."""
    li = [int(np.where(lats_src == v)[0][0]) for v in lat_axis]
    lo = [int(np.where(lons_src == v)[0][0]) for v in lon_axis]
    la0, la1 = min(li), max(li) + 1
    lo0, lo1 = min(lo), max(lo) + 1
    ti0, ti1 = hour_index(t_start), hour_index(t_end_exclusive)
    if lvl_idx is None:
        arr = pg[var][ti0:ti1, la0:la1, lo0:lo1]
        sub_lats = lats_src[la0:la1]
        sub_lons = lons_src[lo0:lo1]
    else:
        arr = pg[var][ti0:ti1, lvl_idx, la0:la1, lo0:lo1]
        sub_lats = lats_src[la0:la1]
        sub_lons = lons_src[lo0:lo1]
    arr = np.asarray(arr)
    # exact crop to planned centers (guards against non-contiguous picks)
    lix = [int(np.where(sub_lats == v)[0][0]) for v in lat_axis]
    lox = [int(np.where(sub_lons == v)[0][0]) for v in lon_axis]
    arr = arr[:, lix, :][:, :, lox] if lvl_idx is None else arr[:, lix, :][:, :, lox]
    return arr, (ti0, ti1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inventory", required=True)
    ap.add_argument("--var", required=True)
    ap.add_argument("--out-root", required=True)
    ap.add_argument("--cap-bytes", type=int, default=500_000_000)
    ap.add_argument("--selections", default="",
                    help="comma-separated selection_ids to fetch (default: all)")
    ap.add_argument("--record-tag", default="",
                    help="suffix for the per-worker retrieval record (disjoint selection partitions)")
    a = ap.parse_args()
    var = a.var
    if var not in SINGLE_VARS and var != PRESSURE_VAR:
        raise ValueError(f"undeclared v17 variable {var}")
    group = "pressure/temporal" if var == PRESSURE_VAR else "single/temporal"
    inv = json.loads(Path(a.inventory).read_text())
    root = Path(a.out_root) / f"payload-v17_{var}"
    req_dir, pay_dir = root / "retrieval/requests", root / "retrieval/payloads"
    for d in (req_dir, pay_dir):
        d.mkdir(parents=True, exist_ok=True)

    pg_all = open_store()
    pg = pg_all[group]
    LATS, LONS = pg["latitude"][:], pg["longitude"][:]
    lvl_idx = int(np.where(pg["pressure_level"][:] == PRESSURE_LEVEL)[0][0]) \
        if var == PRESSURE_VAR else None

    sels = list(inv["climatology_selections"]) + \
        [{**s, "spillover": True} for s in inv["antecedent_spillover_selections"]]
    only = set(a.selections.split(",")) if a.selections else None
    records, total_bytes = [], 0
    started = m._utc_now()

    for sel in sels:
        sid = sel["selection_id"]
        if only and sid not in only:
            continue
        gs = sel["grid_selection"]
        lat_axis = np.array(gs["latitudes_descending"])
        lon_axis = np.array(gs["longitudes_ascending"])
        for v in list(lat_axis) + list(lon_axis):
            src = LATS if v in lat_axis else LONS
            if not np.any(np.isclose(src, v, atol=1e-8)):
                raise ValueError(f"{sid}: source grid lacks planned center {v}")
        parts, times_all = [], []
        if sel.get("spillover"):
            for d in sel["dates"]:
                t0 = pd.Timestamp(d, tz="UTC")
                t1 = t0 + pd.Timedelta(hours=25)
                parts.append((t0, t1))
        else:
            for y in sel["years"]:
                t0 = pd.Timestamp(f"{y}-{sel['calendar_month']:02d}-01", tz="UTC")
                t1 = (t0 + pd.offsets.MonthBegin(1)) + pd.Timedelta(hours=1)
                parts.append((t0, t1))
        cid = m._safe_chunk_id(f"v17-{sid}-{var}")
        target = pay_dir / f"{cid}.nc"
        if target.exists():
            total_bytes += target.stat().st_size
            continue
        last_err = None
        for attempt in range(12):
            try:
                arrs, spns = [], []
                for t0, t1 in parts:
                    arr, spn = fetch_block(pg, var, group, LATS, LONS,
                                           lat_axis, lon_axis, t0, t1, lvl_idx)
                    arrs.append(arr); spns.append(spn)
                last_err = None
                break
            except Exception as e:
                last_err = e
                time.sleep(min(300, 30 + attempt * 30))
        if last_err is not None:
            raise RuntimeError(f"{cid}: exhausted retries: {last_err}")
        arrc = np.concatenate(arrs, axis=0)
        tix = np.concatenate([np.arange(s0, s1) for s0, s1 in spns])
        emt = pg["valid_time"]
        store_hours = np.asarray(emt[tix], dtype="int64")
        if not np.all(store_hours == tix):
            raise ValueError(f"{cid}: store time axis mismatch")
        times = np.datetime64("1940-01-01T00:00:00", "ns") + \
            store_hours * np.timedelta64(1, "h")
        coords = {"valid_time": times,
                  "latitude": lat_axis.astype("float64"),
                  "longitude": lon_axis.astype("float64")}
        if lvl_idx is not None:
            arrc = arrc[:, None, :, :]
            dims = ("valid_time", "pressure_level", "latitude", "longitude")
            coords["pressure_level"] = np.array([PRESSURE_LEVEL])
        else:
            dims = ("valid_time", "latitude", "longitude")
        ds = xr.Dataset({var: (dims, arrc.astype("float32"))}, coords=coords)
        fd_tmp = pay_dir / f".{cid}.tmp.nc"
        ds.to_netcdf(fd_tmp)
        fd_tmp.rename(target)
        sz = target.stat().st_size
        total_bytes += sz
        if total_bytes > a.cap_bytes:
            raise ValueError(f"aggregate cap exceeded: {total_bytes}")
        sha = write_once_sidecar(target)
        logical = {"variable": [var], "group": group,
                   "selection_id": sid, "spillover": bool(sel.get("spillover")),
                   "years": sel.get("years") or sorted({d[:4] for d in sel["dates"]}),
                   "month": sel.get("calendar_month") or sorted({d[5:7] for d in sel["dates"]}),
                   "dates": sel.get("dates"), "area": sel["event_box"]}
        rd = {"schema": "P5_ARMC_REQUEST_V0", "chunk_id": cid,
              "dataset": "earthmover_icechunk_era5",
              "request": logical,
              "request_sha256": sha256_bytes(m._canonical_json(logical)),
              "execution": {"mode": "earthmover_icechunk", "store": STORE_URI,
                            "group": group, "snapshot_id": SNAPSHOT},
              "response_sha256": sha, "response_bytes": sz,
              "status": "PAYLOAD_DERIVED_FROM_EARTHMOVER",
              "derivation": "p3 HMA held-out per-lake-cluster slice (2x2 cells); source coords exact-matched",
              "amendment": "p5-hma-heldout-protocol-v1 (Option-A pooled held-out locking test)"}
        write_once_json(req_dir / f"{cid}.json", rd, indent=2)
        write_once_sidecar(req_dir / f"{cid}.json")
        records.append({"chunk_id": cid, "bytes": sz, "sha256": sha})
        print(f"[{var}] {cid} bytes={sz} total={total_bytes}", flush=True)

    write_once_json(root / "retrieval" / f"retrieval_record_v17{('_' + a.record_tag) if a.record_tag else ''}.json",
                    {"lane": f"payload-v17_{var}", "var": var,
                     "started_utc": started, "completed_utc": m._utc_now(),
                     "snapshot": SNAPSHOT, "total_bytes": total_bytes,
                     "records": records}, indent=2)
    print(json.dumps({"var": var, "chunks": len(records),
                      "total_bytes": total_bytes}))


if __name__ == "__main__":
    main()
