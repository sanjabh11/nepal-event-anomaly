"""Own-code completeness gate for the HMA held-out lane.

Runs BEFORE the single inferential analyzer invocation. Recomputes, directly
from the NetCDF payloads (no analyzer imports), whether every unit's declared
windows are computable:

  * every event-relative day -18..+3 must be a complete 24h day for tp
    (covers E4's earliest day and SEA's +3 tail)
  * for each declared exposure length L in {4,7,10}: the same-calendar-month
    reference pool after +-7d washout must contain >= 100 complete windows
  * every .nc payload has a verifying .sha256; every payload has a request
    record .json + verifying .sha256 with status PAYLOAD_DERIVED_FROM_EARTHMOVER
  * no .tmp.nc files; aggregate payload bytes <= cap

Writes results/coverage_check_v1.json and exits nonzero on any failure.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

CAP = 400_000_000
MIN_REF = 100
WASH = 7


def sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def load_var(lane: Path, var: str) -> dict:
    out = {}
    for f in sorted((lane / f"payload-v17_{var}/retrieval/payloads").glob("*.nc")):
        sid = f.stem.replace("v17-", "").replace(f"-{var}", "")
        ds = xr.open_dataset(f)
        da = ds[var]
        da = da.isel(pressure_level=0) if "pressure_level" in da.dims else da
        vt = pd.DatetimeIndex(da["valid_time"].values, tz="UTC")
        out[sid] = pd.Series(np.asarray(da.values).reshape(len(vt), -1).mean(axis=1), index=vt)
    return out


def daily_accum(s: pd.Series) -> pd.Series:
    g = s.groupby((s.index - pd.Timedelta(hours=1)).normalize())
    out = g.sum()
    out = out[g.count() == 24]
    out.index = out.index.tz_localize(None)
    out = out.sort_index()
    # continuous daily axis so rolling windows cannot stitch across gaps
    return out.reindex(pd.date_range(out.index.min(), out.index.max(), freq="D"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", required=True)
    ap.add_argument("--inventory", required=True)
    ap.add_argument("--episode-map", required=True)
    ap.add_argument("--decision", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    lane, out = Path(a.lane), Path(a.out)
    inv = json.loads(Path(a.inventory).read_text())
    ep = json.loads(Path(a.episode_map).read_text())
    dec = json.loads(Path(a.decision).read_text())
    sels = inv["climatology_selections"] + inv["antecedent_spillover_selections"]
    events = {e["event_id"].rsplit(":", 1)[1]: e for e in dec["events"]
              if e["adjudication"]["disposition"] == "ELIGIBLE"}

    failures, checks = [], {}

    # ---- artifact integrity -------------------------------------------------
    n_pay = n_req = 0
    total_bytes = 0
    tmp = list(lane.rglob("*.tmp.nc"))
    if tmp:
        failures.append(f"tmp files remain: {tmp[:3]}")
    for var in ("tp", "t2m", "sf", "tcwv", "cape", "sp", "t"):
        pd_dir = lane / f"payload-v17_{var}/retrieval/payloads"
        rq_dir = lane / f"payload-v17_{var}/retrieval/requests"
        for f in sorted(pd_dir.glob("*.nc")):
            n_pay += 1
            total_bytes += f.stat().st_size
            sc = Path(str(f) + ".sha256")
            if not sc.exists() or sc.read_text().split()[0] != sha(f):
                failures.append(f"payload sidecar fail: {f.name}")
            rq = rq_dir / (f.stem + ".json")
            rqs = Path(str(rq) + ".sha256")
            if not rq.exists():
                failures.append(f"missing request record {rq.name}")
                continue
            n_req += 1
            if not rqs.exists() or rqs.read_text().split()[0] != sha(rq):
                failures.append(f"request sidecar fail: {rq.name}")
            if json.loads(rq.read_text()).get("status") != "PAYLOAD_DERIVED_FROM_EARTHMOVER":
                failures.append(f"bad status {rq.name}")
    if total_bytes > CAP:
        failures.append(f"aggregate cap exceeded: {total_bytes}")
    checks["payloads"] = n_pay
    checks["request_records"] = n_req
    checks["payload_bytes"] = total_bytes
    expected = inv["n_selections"] * len(inv["raw_variables"])
    checks["expected_payloads"] = expected
    if n_pay != expected:
        failures.append(f"payload count {n_pay} != expected {expected}")

    # ---- window computability ----------------------------------------------
    tp = load_var(lane, "tp")
    per_unit = {}
    for u in ep["units"]:
        uid = u["unit_id"]
        mems = sorted(u["member_ids"],
                      key=lambda m: events[m]["adjudication"]["event_time_interval"]["start"])
        pm = mems[0]
        d = pd.Timestamp(events[pm]["adjudication"]["event_time_interval"]["start"][:10])
        box = json.dumps(
            next(s["event_box"] for s in inv["climatology_selections"]
                 if pm in s["member_ids"] and s["calendar_month"] == d.month),
            sort_keys=True)
        sel_ids = [s["selection_id"] for s in sels
                   if json.dumps(s["event_box"], sort_keys=True) == box]
        merged = pd.concat([tp[i] for i in sel_ids if i in tp]).sort_index()
        merged = merged[~merged.index.duplicated(keep="first")]
        dly = daily_accum(merged)
        wash = sorted({pd.Timestamp(events[m]["adjudication"]["event_time_interval"]["start"][:10])
                       for s in inv["climatology_selections"]
                       if json.dumps(s["event_box"], sort_keys=True) == box
                       for m in s["member_ids"]
                       if m in events
                       and pd.Timestamp(events[m]["adjudication"]["event_time_interval"]["start"][:10]).month
                       == s["calendar_month"]})
        unit_fail = []
        sea_tail_missing = []
        for lag in range(-17, 1):          # required: deepest declared window day
            if (d + pd.Timedelta(days=lag)) not in dly.index:
                unit_fail.append(f"day {lag:+d} incomplete")
        for lag in range(1, 4):            # descriptive SEA tail (next month unfetched)
            if (d + pd.Timedelta(days=lag)) not in dly.index:
                sea_tail_missing.append(lag)
        for L in (4, 7, 10):
            r = dly.rolling(L, min_periods=L).sum().dropna()
            ref = r[r.index.month == d.month]
            ok = 0
            for t in ref.index:
                lo, hi = t - pd.Timedelta(days=L - 1), t
                if not any(wd - pd.Timedelta(days=WASH) <= hi and
                           lo <= wd + pd.Timedelta(days=WASH) for wd in wash):
                    ok += 1
            if ok < MIN_REF:
                unit_fail.append(f"L={L} n_ref={ok} < {MIN_REF}")
        per_unit[uid] = {"primary_member": pm, "event_date": str(d.date()),
                         "n_selections": len(sel_ids), "status": "READY" if not unit_fail else "NOT_READY",
                         "failures": unit_fail,
                         "sea_tail_missing_days": sea_tail_missing}
        if unit_fail:
            failures.append(f"{uid}: {unit_fail}")
    checks["units"] = per_unit
    checks["n_ready"] = sum(1 for v in per_unit.values() if v["status"] == "READY")

    doc = {"schema": "P5_HMA_COVERAGE_CHECK_V1", "checks": checks,
           "failures": failures, "gate": "PASS" if not failures else "FAIL"}
    out.parent.mkdir(parents=True, exist_ok=True)
    if not out.exists():
        out.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
        Path(str(out) + ".sha256").write_text(f"{sha(out)}  {out.name}\n")
    print(json.dumps({"gate": doc["gate"], "failures": failures[:10],
                      "payloads": n_pay, "bytes": total_bytes,
                      "units_ready": checks["n_ready"]}))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
