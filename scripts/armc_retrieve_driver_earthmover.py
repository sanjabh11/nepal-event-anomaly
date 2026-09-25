"""Earthmover icechunk ERA5 retrieval worker (amendment v15).

Reads canonical per-chunk slices (basin x variable x level x year x JJA)
from the anonymous Earthmover icechunk store (pressure/temporal group) and
writes them in the same canonical layout as CDS payloads:
payloads/{cid}.nc, requests/{cid}.json, chunks/{cid}.json.

Honest provenance:
- receipts carry source=earthmover_icechunk, derived_from=<store URI>,
  snapshot_id, and PAYLOAD_DERIVED_FROM_EARTHMOVER status
- scalar coords number=0, expver=1 are synthesized constants documented in
  v15 (canonical schema parity; they are not fetched data)
- request docs keep the per-chunk logical request verbatim + an
  `execution` block naming the actual earthmover read
"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, "scripts")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import armc_cds_retrieve as m
import numpy as np
import pandas as pd
import xarray as xr
import icechunk

VAR = sys.argv[1]
BASIN_FILTER = sys.argv[2] if len(sys.argv) > 2 else None
AM = "/Users/sanjayb/nepal-event-anomaly-evidence/p5-glof-2026-09-19/retrieval"
ROOT = Path("/Users/sanjayb/nepal-event-anomaly-evidence/p5-armc-pressure-levels-2026-09-22")
gate = json.loads((ROOT / "retrieval" / "armc_metadata_gate_v0.json").read_text())
assert gate["status"] == "METADATA_OK" and gate["retrieval_gate"] == "PAYLOAD_AUTHORIZED"

EM_VAR = {"geopotential":"z","specific_humidity":"q","temperature":"t","vertical_velocity":"w"}[VAR]
STORE_URI = "s3://earthmover-icechunk-era5/icechunkV2"
GROUP = "pressure/temporal"
BASE = pd.Timestamp("1940-01-01", tz="UTC")

contract = m.load_contract(Path(f"{AM}/p5_amendment_v10_arm_c_spatial_correction.json"),
                           v8_path=Path(f"{AM}/p5_amendment_v8_arm_c_scope.json"),
                           v9_path=Path(f"{AM}/p5_amendment_v9_arm_c_grid_provenance.json"))
full = m.build_request_plan(contract)
base = [r for r in full if r["variable"] == VAR]
years = sorted({r["year"] for r in base})
basins = [BASIN_FILTER] if BASIN_FILTER else sorted({r["basin"] for r in base})
lvls = sorted({r["pressure_level_hpa"] for r in base})

root = ROOT / f"payload-{VAR}"
retrieval = root / "retrieval"
req_dir, pay_dir, ch_dir = retrieval/"requests", retrieval/"payloads", retrieval/"chunks"
for d in (req_dir, pay_dir, ch_dir):
    d.mkdir(parents=True, exist_ok=True)

from p5_safe_io import write_once_json, write_once_sidecar, sha256_bytes
plan_path = retrieval/"armc_request_plan_v0.json"
_plan_entries = json.loads(plan_path.read_text())["requests"]
def by_key(b, y, lvl):
    return next(r for r in _plan_entries if r["basin"] == b and r["year"] == y
                and int(r["pressure_level_hpa"]) == lvl)

storage = icechunk.s3_storage(bucket="earthmover-icechunk-era5", prefix="icechunkV2",
                              region="us-east-1", anonymous=True)
repo = icechunk.Repository.open(storage)
session = repo.readonly_session(snapshot_id="ZFKDHBCTBVHVXM3BQFV0")
SNAPSHOT = session.snapshot_id
import zarr
pg = zarr.open_group(session.store, mode="r")[GROUP]
LATS, LONS, LVLS = pg["latitude"][:], pg["longitude"][:], pg["pressure_level"][:]
EMTIME = pg["valid_time"]  # hours since 1940

client_retry = 0
records, total_bytes = [], 0
started = m._utc_now()
cap = int(contract["max_download_bytes"])

def fetch_slice(b, y, lvl):
    """Return (values float32 array, times ns array, lats, lons)."""
    area = dict(zip(("north","west","south","east"),
                    by_key(b, y, lvl)["area_nwse"]))
    lat_axis = np.arange(area["north"], area["south"] - 1e-9, -0.25)
    lon_axis = np.arange(area["west"], area["east"] + 1e-9, 0.25)
    li = [int(np.where(LATS == v)[0][0]) for v in lat_axis]
    lo = [int(np.where(LONS == v)[0][0]) for v in lon_axis]
    lix = int(np.where(LVLS == float(lvl))[0][0])
    ti0 = int((pd.Timestamp(f"{y}-06-01", tz="UTC") - BASE) / pd.Timedelta(hours=1))
    arr = pg[EM_VAR][ti0:ti0 + 2208, lix, li, :][:, :, lo]
    return np.asarray(arr), lat_axis, lon_axis, ti0

for b in basins:
    for lvl in lvls:
        for y in years:
            cid = m._safe_chunk_id(f"{b}-{VAR}-{lvl}-{y}")
            target = pay_dir/f"{cid}.nc"
            if target.exists():
                continue
            ref = by_key(b, y, lvl)
            logical = ref["request"]
            last_err = None
            for attempt in range(12):
                try:
                    arr, lat_axis, lon_axis, ti0 = fetch_slice(b, y, lvl)
                    last_err = None; break
                except Exception as e:
                    last_err = e
                    wait = min(300, 30 + attempt*30)
                    print(f"[{VAR}] {cid} fetch err ({str(e)[:80]}), retry {attempt+1} in {wait}s", flush=True)
                    time.sleep(wait)
            if last_err is not None:
                raise RuntimeError(f"{cid}: exhausted retries: {last_err}")
            # exact shape/timestamp check before write
            if arr.shape != (2208, len(lat_axis), len(lon_axis)):
                raise ValueError(f"{cid}: bad slice shape {arr.shape}")
            store_hours = np.asarray(EMTIME[ti0:ti0+2208], dtype="int64")
            if store_hours[0] != ti0 or not np.all(np.diff(store_hours) == 1):
                raise ValueError(f"{cid}: store time axis not contiguous hourly")
            times = np.datetime64("1940-01-01T00:00:00", "ns") + store_hours * np.timedelta64(1, "h")
            ds = xr.Dataset(
                {EM_VAR: (("valid_time","pressure_level","latitude","longitude"),
                          arr.reshape(len(times), 1, len(lat_axis), len(lon_axis)).astype("float32"))},
                coords={
                    "valid_time": times,
                    "pressure_level": np.array([float(lvl)]),
                    "latitude": lat_axis.astype("float64"),
                    "longitude": lon_axis.astype("float64"),
                    "number": np.int64(0),
                    "expver": ("valid_time", np.array(["0001"]*len(times), dtype="<U4")),
                })
            fd_tmp = pay_dir/f".{cid}.tmp.nc"
            ds.to_netcdf(fd_tmp)
            fd_tmp.rename(target)
            sz = target.stat().st_size
            total_bytes += sz
            if total_bytes > cap: raise ValueError(f"cap exceeded: {total_bytes}")
            sha = write_once_sidecar(target)
            rd = {"schema":"P5_ARMC_REQUEST_V0","chunk_id":cid,"dataset":contract["dataset"],
                  "request":logical,
                  "request_sha256":sha256_bytes(m._canonical_json(logical)),
                  "execution":{"mode":"earthmover_icechunk","store":STORE_URI,
                               "group":GROUP,"branch":"main","snapshot_id":SNAPSHOT,
                               "amendment":"p5_amendment_v15_armc_earthmover_source"}}
            rp = req_dir/f"{cid}.json"
            if not rp.exists():
                write_once_json(rp, rd, indent=2); write_once_sidecar(rp)
            else:
                existing = json.loads(rp.read_text())
                if existing.get("request_sha256") != rd["request_sha256"]:
                    raise ValueError(f"{cid}: existing request doc digest mismatch")
            rec = {"schema":"P5_ARMC_CHUNK_RECEIPT_V0","chunk_id":cid,
                   "request_relpath":f"retrieval/requests/{cid}.json",
                   "payload_relpath":f"retrieval/payloads/{cid}.nc",
                   "request_sha256":rd["request_sha256"],"response_sha256":sha,
                   "response_bytes":sz,"status":"PAYLOAD_DERIVED_FROM_EARTHMOVER",
                   "source":"earthmover_icechunk",
                   "derived_from":f"{STORE_URI}#{GROUP}@{SNAPSHOT}",
                   "derivation":"temporal-group slice [JJA year, level, box]; synthesized number=0 expver=1 scalar coords (v15)",
                   "synthesized_coords":["number","expver"]}
            cp = ch_dir/f"{cid}.json"
            if not cp.exists():
                write_once_json(cp, rec, indent=2); write_once_sidecar(cp)
            records.append(rec)
            print(f"[{VAR}] {cid} {sz/1e6:.2f}MB ({len(records)} new)", flush=True)

man = {"schema":"P5_ARMC_RETRIEVAL_MANIFEST_V0",
       "status":"RETRIEVAL_RECEIVED_PENDING_NETCDF_VALIDATION",
       "execution":"earthmover_icechunk_v15","source":"earthmover_icechunk",
       "snapshot_id":SNAPSHOT,
       "started_utc":started,"completed_utc":m._utc_now(),
       "completed_chunks":len(records),"actual_payload_bytes":total_bytes,
       "max_download_bytes":cap,"chunks":records,
       "claim_scope":"research_only_no_operational_authorization"}
mp = retrieval/f"armc_earthmover_manifest_v15{('_'+BASIN_FILTER) if BASIN_FILTER else ''}.json"
write_once_json(mp, man, indent=2); write_once_sidecar(mp)
print(f"[{VAR}] DONE new_chunks={len(records)} bytes={total_bytes} snapshot={SNAPSHOT}", flush=True)
