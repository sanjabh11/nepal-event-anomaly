"""Pre-audit correction: align earthmover-derived payloads to the CDS coord
contract. Values are untouched; only synthesized coords + CF attrs are fixed.

- expver -> (valid_time,) '<U4' vector of '0001' (CDS final-ERA5 convention)
- number -> int64 scalar 0 + realization attrs
- adds units/standard_name/long_name attrs on the data var + coords
- regenerates payload .sha256, receipt response_sha256/bytes, receipt .sha256
"""
import json, sys, hashlib
from pathlib import Path
import numpy as np
import xarray as xr

ROOT = Path("/Users/sanjayb/nepal-event-anomaly-evidence/p5-armc-pressure-levels-2026-09-22")
VAR_ATTRS = {
    "z": {"units": "m**2 s**-2", "long_name": "Geopotential", "standard_name": "geopotential"},
    "q": {"units": "kg kg**-1", "long_name": "Specific humidity", "standard_name": "specific_humidity"},
    "t": {"units": "K", "long_name": "Temperature", "standard_name": "air_temperature"},
    "w": {"units": "Pa s**-1", "long_name": "Vertical velocity", "standard_name": "lagrangian_tendency_of_air_pressure"},
}
COORD_ATTRS = {
    "number": {"long_name": "ensemble member numerical id", "units": "1", "standard_name": "realization"},
    "valid_time": {"long_name": "time", "standard_name": "time"},
    "pressure_level": {"long_name": "pressure", "units": "hPa", "positive": "down",
                       "stored_direction": "decreasing", "standard_name": "air_pressure"},
    "latitude": {"units": "degrees_north", "standard_name": "latitude", "long_name": "latitude",
                 "stored_direction": "decreasing"},
    "longitude": {"units": "degrees_east", "standard_name": "longitude", "long_name": "longitude"},
}

fixed = skipped = 0
for receipt_path in ROOT.glob("payload-*/retrieval/chunks/*.json"):
    rec = json.loads(receipt_path.read_text())
    if rec.get("source") != "earthmover_icechunk":
        continue
    payload = receipt_path.parent.parent / "payloads" / f"{rec['chunk_id']}.nc"
    ds = xr.open_dataset(payload, decode_times=True).load()
    ev = ds.coords.get("expver")
    already = ev is not None and ev.dims == ("valid_time",) and set(np.unique(ev.values)) == {"0001"}
    var = list(ds.data_vars)[0]
    if already and ds[var].attrs.get("units") == VAR_ATTRS[var]["units"]:
        skipped += 1; continue
    ds = ds.assign_coords(
        number=np.int64(0),
        expver=("valid_time", np.array(["0001"]*ds.sizes["valid_time"], dtype="<U4")),
    )
    for c, attrs in COORD_ATTRS.items():
        if c in ds.coords: ds[c].attrs.update(attrs)
    ds[var].attrs = dict(VAR_ATTRS[var])
    ds.attrs = {}
    tmp = payload.with_suffix(".fix.nc")
    ds.to_netcdf(tmp)
    tmp.replace(payload)
    sha = hashlib.sha256(payload.read_bytes()).hexdigest()
    Path(str(payload) + ".sha256").write_text(f"{sha}  {payload.name}\n")
    rec["response_sha256"] = sha
    rec["response_bytes"] = payload.stat().st_size
    rec["derivation"] = ("temporal-group slice [JJA year, level, box]; synthesized number=0 scalar "
                         "and expver='0001' valid_time vector + CF attrs (v15 coord-correction)")
    rec["synthesized_coords"] = ["number", "expver"]
    receipt_path.write_text(json.dumps(rec, indent=2) + "\n")
    rsha = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    Path(str(receipt_path) + ".sha256").write_text(f"{rsha}  {receipt_path.name}\n")
    fixed += 1
    if fixed % 50 == 0: print("fixed", fixed, flush=True)
print(f"done: fixed={fixed} already_ok={skipped}")
